"""Guardrail enforcement for the Engagement Manager.

Module 4: Arena - Pod Beta.
Follows strict zero em dash rule: only hyphens (-) or colons (:).

Includes:
1. CooldownChecker: Enforces cooldown intervals between engagements of the same type.
2. ScopeDriftDetector: Flags and requires confirmation if scope expands over 120%.
3. BudgetValidator: Validates wallet credit limits prior to engagement actions.
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple
from pydantic import BaseModel, Field

from app.engagement.models import LaunchTier


class GuardrailError(Exception):
    """Base exception for guardrail violations."""
    pass


class CooldownViolationError(GuardrailError):
    """Raised when an engagement of the same type is started within the cooldown window."""

    def __init__(
        self,
        tenant_id: str,
        engagement_type: LaunchTier,
        remaining_seconds: float,
        cooldown_period: float,
    ):
        self.tenant_id = tenant_id
        self.engagement_type = engagement_type
        self.remaining_seconds = round(remaining_seconds, 1)
        self.cooldown_period = round(cooldown_period, 1)
        super().__init__(
            f"Cooldown active for tenant '{tenant_id}' on tier '{engagement_type.value}'. "
            f"Must wait {self.remaining_seconds}s more (cooldown window: {self.cooldown_period}s)."
        )


class ScopeDriftError(GuardrailError):
    """Raised when scope expands beyond 120% without explicit tenant confirmation."""

    def __init__(
        self,
        expansion_percentage: float,
        original_size: int,
        new_size: int,
        threshold_percentage: float = 120.0,
    ):
        self.expansion_percentage = round(expansion_percentage, 2)
        self.original_size = original_size
        self.new_size = new_size
        self.threshold_percentage = threshold_percentage
        super().__init__(
            f"Scope drift detected: new scope ({new_size}) is {self.expansion_percentage}% "
            f"of original scope ({original_size}), exceeding the {threshold_percentage}% threshold. "
            f"Explicit confirmation is required to proceed with scope expansion."
        )


class InsufficientCreditsError(GuardrailError):
    """Raised when tenant wallet does not have sufficient credits for engagement."""

    def __init__(self, cost: int, balance: int):
        self.cost = cost
        self.balance = balance
        super().__init__(f"Need {cost} credits, have {balance}")


class ScopeDriftReport(BaseModel):
    """Result of scope drift evaluation."""
    drift_detected: bool = False
    original_size: int = 0
    new_size: int = 0
    expansion_ratio: float = 1.0
    expansion_percentage: float = 100.0
    threshold_percentage: float = 120.0
    requires_confirmation: bool = False
    warning: Optional[str] = None


class CooldownChecker:
    """Enforces cooldown periods between engagements of the same type per tenant.

    Prevents rapid cycling through campaigns to farm leaderboard points or rewards.
    """

    DEFAULT_COOLDOWN_SECONDS: Dict[LaunchTier, float] = {
        LaunchTier.QUICK_SCAN: 300.0,   # 5 minutes
        LaunchTier.GUIDED: 1800.0,       # 30 minutes
        LaunchTier.AUDIT: 3600.0,        # 60 minutes
        LaunchTier.CONTINUOUS: 0.0,      # Continuous validation handled by schedule
        LaunchTier.ARENA: 600.0,         # 10 minutes for competitive mode
    }

    def __init__(
        self,
        cooldown_settings: Optional[Dict[LaunchTier, float]] = None,
    ):
        self.cooldown_settings = (
            cooldown_settings.copy()
            if cooldown_settings
            else self.DEFAULT_COOLDOWN_SECONDS.copy()
        )
        # Store execution history: (tenant_id, engagement_type) -> list of datetimes
        self._history: Dict[Tuple[str, LaunchTier], List[datetime]] = {}

    def get_cooldown_period(self, engagement_type: LaunchTier) -> float:
        """Return configured cooldown duration in seconds for given engagement tier."""
        return self.cooldown_settings.get(engagement_type, 300.0)

    def set_cooldown_period(self, engagement_type: LaunchTier, seconds: float) -> None:
        """Configure cooldown duration for a specific engagement tier."""
        self.cooldown_settings[engagement_type] = max(0.0, seconds)

    def check_cooldown(
        self,
        tenant_id: str,
        engagement_type: LaunchTier,
        current_time: Optional[datetime] = None,
    ) -> Tuple[bool, float]:
        """Check if tenant is eligible to start an engagement of engagement_type.

        Returns:
            Tuple of (is_allowed, remaining_seconds)
        """
        cooldown_period = self.get_cooldown_period(engagement_type)
        if cooldown_period <= 0.0:
            return True, 0.0

        key = (tenant_id, engagement_type)
        timestamps = self._history.get(key, [])
        if not timestamps:
            return True, 0.0

        now = current_time or datetime.now(timezone.utc)
        last_time = timestamps[-1]
        elapsed = (now - last_time).total_seconds()

        if elapsed < cooldown_period:
            remaining = cooldown_period - elapsed
            return False, remaining

        return True, 0.0

    def assert_not_in_cooldown(
        self,
        tenant_id: str,
        engagement_type: LaunchTier,
        current_time: Optional[datetime] = None,
    ) -> None:
        """Validate cooldown compliance and raise CooldownViolationError if violated."""
        is_allowed, remaining = self.check_cooldown(tenant_id, engagement_type, current_time)
        if not is_allowed:
            period = self.get_cooldown_period(engagement_type)
            raise CooldownViolationError(tenant_id, engagement_type, remaining, period)

    def record_engagement(
        self,
        tenant_id: str,
        engagement_type: LaunchTier,
        timestamp: Optional[datetime] = None,
    ) -> None:
        """Record an engagement initiation for cooldown tracking."""
        key = (tenant_id, engagement_type)
        now = timestamp or datetime.now(timezone.utc)
        if key not in self._history:
            self._history[key] = []
        self._history[key].append(now)

    def reset(self, tenant_id: Optional[str] = None) -> None:
        """Reset history for a tenant or for all tenants."""
        if tenant_id is None:
            self._history.clear()
        else:
            keys_to_remove = [k for k in self._history if k[0] == tenant_id]
            for k in keys_to_remove:
                del self._history[k]


class ScopeDriftDetector:
    """Detects scope drift when an engagement scope expands beyond 120% of original.

    Emits warnings and requires explicit tenant confirmation before accepting expanded scope.
    """

    DRIFT_THRESHOLD_RATIO: float = 1.20  # 120% of original scope

    @classmethod
    def calculate_scope_size(cls, scope: Dict[str, Any]) -> int:
        """Calculate quantitative size of a scope manifest.

        Counts target assets, techniques, playbooks, or target_count.
        """
        if not scope:
            return 0

        # If explicit target_count is provided and positive
        target_count = scope.get("target_count")
        if isinstance(target_count, int) and target_count > 0:
            return target_count

        total = 0

        assets = scope.get("assets", [])
        if isinstance(assets, list):
            total += len(assets)

        techniques = scope.get("techniques", [])
        if isinstance(techniques, list):
            total += len(techniques)

        playbooks = scope.get("playbook_hashes") or scope.get("playbooks", [])
        if isinstance(playbooks, list):
            total += len(playbooks)

        # If no standard lists were provided, count non-empty keys or elements
        if total == 0:
            for k, v in scope.items():
                if k in ("metadata", "target_count"):
                    continue
                if isinstance(v, list):
                    total += len(v)
                elif isinstance(v, dict):
                    total += len(v)
                elif v is not None:
                    total += 1

        return total

    @classmethod
    def evaluate_drift(
        cls,
        original_scope: Dict[str, Any],
        new_scope: Dict[str, Any],
        confirmed: bool = False,
    ) -> ScopeDriftReport:
        """Evaluate if updating from original_scope to new_scope incurs scope drift.

        Returns a ScopeDriftReport containing drift metrics and warnings.
        """
        orig_size = cls.calculate_scope_size(original_scope)
        new_size = cls.calculate_scope_size(new_scope)

        # Baseline: if original scope was empty (0), setting initial scope is not drift
        if orig_size <= 0:
            return ScopeDriftReport(
                drift_detected=False,
                original_size=orig_size,
                new_size=new_size,
                expansion_ratio=1.0,
                expansion_percentage=100.0,
                requires_confirmation=False,
            )

        expansion_ratio = new_size / orig_size
        expansion_percentage = round(expansion_ratio * 100.0, 2)
        drift_detected = expansion_ratio > cls.DRIFT_THRESHOLD_RATIO

        warning = None
        requires_confirmation = False

        if drift_detected:
            warning = (
                f"Scope expansion ({expansion_percentage}%) exceeds the 120% threshold "
                f"of original scope (original: {orig_size}, new: {new_size})."
            )
            requires_confirmation = not confirmed

        return ScopeDriftReport(
            drift_detected=drift_detected,
            original_size=orig_size,
            new_size=new_size,
            expansion_ratio=round(expansion_ratio, 4),
            expansion_percentage=expansion_percentage,
            threshold_percentage=120.0,
            requires_confirmation=requires_confirmation,
            warning=warning,
        )

    @classmethod
    def assert_no_unconfirmed_drift(
        cls,
        original_scope: Dict[str, Any],
        new_scope: Dict[str, Any],
        confirmed: bool = False,
    ) -> ScopeDriftReport:
        """Validate scope expansion and raise ScopeDriftError if unconfirmed drift occurs."""
        report = cls.evaluate_drift(original_scope, new_scope, confirmed=confirmed)
        if report.requires_confirmation:
            raise ScopeDriftError(
                expansion_percentage=report.expansion_percentage,
                original_size=report.original_size,
                new_size=report.new_size,
                threshold_percentage=report.threshold_percentage,
            )
        return report


class BudgetValidator:
    """Validates tenant credit limits against engagement costs."""

    @staticmethod
    def validate_budget(balance: int, cost: int) -> None:
        """Raise InsufficientCreditsError if balance is less than required cost."""
        if balance < cost:
            raise InsufficientCreditsError(cost=cost, balance=balance)
