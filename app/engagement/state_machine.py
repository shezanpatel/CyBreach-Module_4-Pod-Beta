"""State machine implementation for engagement lifecycle.

Module 4: Arena - Pod Beta.
Follows strict zero em dash rule: only hyphens (-) or colons (:).

Enforces strict 5-state forward-only progression:
CREATED -> SCOPED -> EXECUTING -> COMPLETED -> ARCHIVED
No restart or undo permitted under any circumstances.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from app.engagement.models import (
    Engagement,
    EngagementState,
    VALID_TRANSITIONS,
)


class EngagementStateError(Exception):
    """Base exception for engagement state violations."""
    pass


class InvalidStateTransitionError(EngagementStateError):
    """Raised when an illegal state transition is attempted."""

    def __init__(
        self,
        current_state: EngagementState,
        target_state: EngagementState,
        message: Optional[str] = None,
    ):
        self.current_state = current_state
        self.target_state = target_state
        if not message:
            allowed = [s.value for s in VALID_TRANSITIONS.get(current_state, [])]
            allowed_str = ", ".join(allowed) if allowed else "none (terminal state)"
            message = (
                f"Cannot transition engagement from '{current_state.value}' "
                f"to '{target_state.value}'. Allowed transitions: [{allowed_str}]. "
                f"Forward-only progression enforced: no restart or undo permitted."
            )
        super().__init__(message)


class TerminalStateError(EngagementStateError):
    """Raised when attempting to transition out of a terminal state."""

    def __init__(self, current_state: EngagementState):
        super().__init__(
            f"Engagement is in terminal state '{current_state.value}' "
            f"and cannot undergo further state transitions."
        )


class EngagementStateMachine:
    """Manages state transitions for engagements with forward-only progression."""

    @staticmethod
    def is_valid_transition(
        current_state: EngagementState,
        target_state: EngagementState,
    ) -> bool:
        """Check if a transition from current_state to target_state is permitted."""
        allowed_targets = VALID_TRANSITIONS.get(current_state, [])
        return target_state in allowed_targets

    @classmethod
    def validate_transition(
        cls,
        current_state: EngagementState,
        target_state: EngagementState,
    ) -> None:
        """Validate state transition, raising an appropriate exception if invalid.

        Enforces:
        - Cannot execute before scoping
        - Cannot complete before executing
        - Cannot archive before completing
        - No backward transitions (no restart, no undo)
        - No transitions out of ARCHIVED (terminal state)
        """
        if current_state == EngagementState.ARCHIVED:
            raise TerminalStateError(current_state)

        if not cls.is_valid_transition(current_state, target_state):
            raise InvalidStateTransitionError(current_state, target_state)

    @classmethod
    def transition(
        cls,
        engagement: Engagement,
        target_state: EngagementState,
        timestamp: Optional[datetime] = None,
    ) -> Engagement:
        """Execute a state transition on an engagement object.

        Updates engagement state, updated_at timestamp, and lifecycle timestamps:
        - completed_at is set when transitioning to COMPLETED
        - archived_at is set when transitioning to ARCHIVED
        """
        cls.validate_transition(engagement.state, target_state)

        now = timestamp or datetime.now(timezone.utc)
        engagement.state = target_state
        engagement.updated_at = now

        if target_state == EngagementState.COMPLETED:
            engagement.completed_at = now
        elif target_state == EngagementState.ARCHIVED:
            engagement.archived_at = now

        return engagement

    @classmethod
    def transition_to_scoped(
        cls,
        engagement: Engagement,
        timestamp: Optional[datetime] = None,
    ) -> Engagement:
        """Transition engagement from CREATED to SCOPED."""
        return cls.transition(engagement, EngagementState.SCOPED, timestamp)

    @classmethod
    def transition_to_executing(
        cls,
        engagement: Engagement,
        timestamp: Optional[datetime] = None,
    ) -> Engagement:
        """Transition engagement from SCOPED to EXECUTING."""
        return cls.transition(engagement, EngagementState.EXECUTING, timestamp)

    @classmethod
    def transition_to_completed(
        cls,
        engagement: Engagement,
        timestamp: Optional[datetime] = None,
    ) -> Engagement:
        """Transition engagement from EXECUTING to COMPLETED."""
        return cls.transition(engagement, EngagementState.COMPLETED, timestamp)

    @classmethod
    def transition_to_archived(
        cls,
        engagement: Engagement,
        timestamp: Optional[datetime] = None,
    ) -> Engagement:
        """Transition engagement from COMPLETED to ARCHIVED."""
        return cls.transition(engagement, EngagementState.ARCHIVED, timestamp)
