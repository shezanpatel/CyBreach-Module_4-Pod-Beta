"""Unit tests for guardrails (cooldown checker, scope drift detection, budget validator).

Module 4: Arena - Pod Beta.
Follows strict zero em dash rule: only hyphens (-) or colons (:).
"""

import pytest
from datetime import datetime, timezone, timedelta

from app.engagement.models import LaunchTier
from app.engagement.guardrails import (
    CooldownChecker,
    CooldownViolationError,
    ScopeDriftDetector,
    ScopeDriftError,
    BudgetValidator,
    InsufficientCreditsError,
)


class TestCooldownChecker:
    """Tests for CooldownChecker preventing rapid cycling through campaigns."""

    def test_first_engagement_allowed(self):
        """Verify initial engagement for any tier is immediately allowed."""
        checker = CooldownChecker()
        allowed, remaining = checker.check_cooldown("tenant-alpha", LaunchTier.QUICK_SCAN)
        assert allowed is True
        assert remaining == 0.0

    def test_rapid_cycling_blocked_within_cooldown(self):
        """Verify starting second engagement of same type within cooldown is blocked."""
        checker = CooldownChecker()
        now = datetime.now(timezone.utc)
        tenant = "tenant-alpha"
        tier = LaunchTier.QUICK_SCAN

        # Record first engagement
        checker.record_engagement(tenant, tier, timestamp=now)

        # Immediate check (1 second later)
        check_time = now + timedelta(seconds=1)
        allowed, remaining = checker.check_cooldown(tenant, tier, current_time=check_time)
        assert allowed is False
        assert remaining > 0

        # Asserting raises CooldownViolationError
        with pytest.raises(CooldownViolationError) as exc_info:
            checker.assert_not_in_cooldown(tenant, tier, current_time=check_time)
        assert "Cooldown active for tenant 'tenant-alpha'" in str(exc_info.value)
        assert "quick_scan" in str(exc_info.value)

    def test_allowed_after_cooldown_expires(self):
        """Verify engagement is allowed once the cooldown window has elapsed."""
        checker = CooldownChecker()
        now = datetime.now(timezone.utc)
        tenant = "tenant-alpha"
        tier = LaunchTier.QUICK_SCAN
        period = checker.get_cooldown_period(tier)  # 300s

        checker.record_engagement(tenant, tier, timestamp=now)

        # Check at exactly cooldown_period + 1s
        future_time = now + timedelta(seconds=period + 1)
        allowed, remaining = checker.check_cooldown(tenant, tier, current_time=future_time)
        assert allowed is True
        assert remaining == 0.0

    def test_different_tiers_do_not_block_each_other(self):
        """Verify cooldown on Quick Scan does not block Guided Assessment."""
        checker = CooldownChecker()
        now = datetime.now(timezone.utc)
        tenant = "tenant-alpha"

        checker.record_engagement(tenant, LaunchTier.QUICK_SCAN, timestamp=now)

        # Different tier should be allowed immediately
        allowed, remaining = checker.check_cooldown(tenant, LaunchTier.GUIDED, current_time=now)
        assert allowed is True
        assert remaining == 0.0

    def test_different_tenants_do_not_block_each_other(self):
        """Verify cooldown for tenant A does not block tenant B on the same tier."""
        checker = CooldownChecker()
        now = datetime.now(timezone.utc)

        checker.record_engagement("tenant-alpha", LaunchTier.AUDIT, timestamp=now)

        # Tenant beta should be allowed immediately
        allowed, remaining = checker.check_cooldown("tenant-beta", LaunchTier.AUDIT, current_time=now)
        assert allowed is True
        assert remaining == 0.0

    def test_continuous_tier_has_zero_cooldown(self):
        """Verify continuous tier (subscription schedule) has 0 cooldown."""
        checker = CooldownChecker()
        now = datetime.now(timezone.utc)
        tenant = "tenant-alpha"

        checker.record_engagement(tenant, LaunchTier.CONTINUOUS, timestamp=now)
        allowed, remaining = checker.check_cooldown(tenant, LaunchTier.CONTINUOUS, current_time=now)
        assert allowed is True
        assert remaining == 0.0


class TestScopeDriftDetector:
    """Tests for ScopeDriftDetector flagging scope expansion exceeding 120%."""

    def test_expansion_under_or_equal_to_120_percent_allowed(self):
        """Verify expansion within 120% threshold proceeds without warning or confirmation."""
        # 10 assets to 12 assets is exactly 120.0%
        original = {"assets": [f"192.168.1.{i}" for i in range(10)]}
        expanded = {"assets": [f"192.168.1.{i}" for i in range(12)]}

        report = ScopeDriftDetector.evaluate_drift(original, expanded)
        assert report.drift_detected is False
        assert report.expansion_percentage == 120.0
        assert report.requires_confirmation is False

        # Should not raise exception
        ScopeDriftDetector.assert_no_unconfirmed_drift(original, expanded)

    def test_expansion_over_120_percent_flags_drift(self):
        """Verify expansion exceeding 120% flags drift and requires confirmation."""
        # 10 assets to 15 assets is 150% (exceeds 120%)
        original = {"assets": [f"192.168.1.{i}" for i in range(10)]}
        expanded = {"assets": [f"192.168.1.{i}" for i in range(15)]}

        report = ScopeDriftDetector.evaluate_drift(original, expanded, confirmed=False)
        assert report.drift_detected is True
        assert report.expansion_percentage == 150.0
        assert report.requires_confirmation is True
        assert report.warning is not None
        assert "exceeds the 120% threshold" in report.warning

        # Unconfirmed raises ScopeDriftError
        with pytest.raises(ScopeDriftError) as exc_info:
            ScopeDriftDetector.assert_no_unconfirmed_drift(original, expanded, confirmed=False)
        assert "Scope drift detected" in str(exc_info.value)
        assert "150.0%" in str(exc_info.value)

    def test_expansion_over_120_percent_with_confirmation_allowed(self):
        """Verify expansion over 120% is accepted when explicit confirmation is provided."""
        original = {"assets": ["host1", "host2", "host3", "host4", "host5"]}  # 5 items
        expanded = {"assets": [f"host{i}" for i in range(1, 10)]}             # 9 items (180%)

        report = ScopeDriftDetector.assert_no_unconfirmed_drift(original, expanded, confirmed=True)
        assert report.drift_detected is True
        assert report.requires_confirmation is False
        assert report.expansion_percentage == 180.0

    def test_initial_scoping_from_empty_scope_does_not_trigger_drift(self):
        """Verify setting scope for the first time (from empty dict) is not flagged as drift."""
        original = {}
        new_scope = {"assets": ["10.0.0.1", "10.0.0.2"], "techniques": ["T1059"]}

        report = ScopeDriftDetector.evaluate_drift(original, new_scope)
        assert report.drift_detected is False
        assert report.requires_confirmation is False

    def test_multi_dimensional_scope_sizing(self):
        """Verify calculation across assets, techniques, and playbooks."""
        scope = {
            "assets": ["web01", "db01"],
            "techniques": ["T1059", "T1047"],
            "playbooks": ["pb1"],
        }
        size = ScopeDriftDetector.calculate_scope_size(scope)
        assert size == 5  # 2 + 2 + 1


class TestBudgetValidator:
    """Tests for budget and credit limit validation."""

    def test_sufficient_credits_passes(self):
        """Verify validation succeeds when balance is greater than or equal to cost."""
        BudgetValidator.validate_budget(balance=100, cost=25)
        BudgetValidator.validate_budget(balance=25, cost=25)

    def test_insufficient_credits_raises_error(self):
        """Verify validation raises InsufficientCreditsError with exact formatting."""
        with pytest.raises(InsufficientCreditsError) as exc_info:
            BudgetValidator.validate_budget(balance=10, cost=25)
        assert "Need 25 credits, have 10" in str(exc_info.value)
        assert exc_info.value.cost == 25
        assert exc_info.value.balance == 10
