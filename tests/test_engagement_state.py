"""Unit tests for the 5-state machine and launch tiers.

Module 4: Arena - Pod Beta.
Follows strict zero em dash rule: only hyphens (-) or colons (:).
"""

import pytest
from datetime import datetime, timezone

from app.engagement.models import (
    Engagement,
    EngagementState,
    LaunchTier,
    TIER_CREDIT_COSTS,
    parse_launch_tier,
    VALID_TRANSITIONS,
)
from app.engagement.state_machine import (
    EngagementStateMachine,
    InvalidStateTransitionError,
    TerminalStateError,
)


def create_sample_engagement(
    state: EngagementState = EngagementState.CREATED,
    tier: LaunchTier = LaunchTier.QUICK_SCAN,
) -> Engagement:
    """Helper to create test engagement instance."""
    now = datetime.now(timezone.utc)
    return Engagement(
        engagement_id="test-eng-100",
        tenant_id="test-tenant-1",
        state=state,
        engagement_type=tier,
        scope={"assets": ["10.0.0.1", "10.0.0.2"]},
        original_scope={"assets": ["10.0.0.1", "10.0.0.2"]},
        credit_cost=TIER_CREDIT_COSTS[tier],
        created_at=now,
        updated_at=now,
    )


class TestEngagementStateMachine:
    """Tests for 5-state machine with forward-only progression."""

    def test_all_five_states_exist(self):
        """Verify the 5 required states exist in the state machine."""
        assert EngagementState.CREATED.value == "created"
        assert EngagementState.SCOPED.value == "scoped"
        assert EngagementState.EXECUTING.value == "executing"
        assert EngagementState.COMPLETED.value == "completed"
        assert EngagementState.ARCHIVED.value == "archived"

    def test_valid_forward_transitions(self):
        """Verify the complete forward-only progression path."""
        eng = create_sample_engagement(state=EngagementState.CREATED)

        # 1. CREATED -> SCOPED
        assert EngagementStateMachine.is_valid_transition(eng.state, EngagementState.SCOPED)
        EngagementStateMachine.transition_to_scoped(eng)
        assert eng.state == EngagementState.SCOPED

        # 2. SCOPED -> EXECUTING
        assert EngagementStateMachine.is_valid_transition(eng.state, EngagementState.EXECUTING)
        EngagementStateMachine.transition_to_executing(eng)
        assert eng.state == EngagementState.EXECUTING

        # 3. EXECUTING -> COMPLETED
        assert EngagementStateMachine.is_valid_transition(eng.state, EngagementState.COMPLETED)
        EngagementStateMachine.transition_to_completed(eng)
        assert eng.state == EngagementState.COMPLETED
        assert eng.completed_at is not None

        # 4. COMPLETED -> ARCHIVED
        assert EngagementStateMachine.is_valid_transition(eng.state, EngagementState.ARCHIVED)
        EngagementStateMachine.transition_to_archived(eng)
        assert eng.state == EngagementState.ARCHIVED
        assert eng.archived_at is not None

    def test_cannot_execute_before_scoping(self):
        """Enforce rule: tenant cannot execute an engagement before scoping it."""
        eng = create_sample_engagement(state=EngagementState.CREATED)
        with pytest.raises(InvalidStateTransitionError) as exc_info:
            EngagementStateMachine.transition(eng, EngagementState.EXECUTING)
        assert "Cannot transition engagement from 'created' to 'executing'" in str(exc_info.value)
        assert eng.state == EngagementState.CREATED

    def test_cannot_complete_before_executing(self):
        """Enforce rule: tenant cannot complete an engagement before executing it."""
        eng = create_sample_engagement(state=EngagementState.SCOPED)
        with pytest.raises(InvalidStateTransitionError) as exc_info:
            EngagementStateMachine.transition(eng, EngagementState.COMPLETED)
        assert "Cannot transition engagement from 'scoped' to 'completed'" in str(exc_info.value)
        assert eng.state == EngagementState.SCOPED

    def test_cannot_skip_to_archived(self):
        """Enforce rule: cannot jump to ARCHIVED before completing."""
        eng = create_sample_engagement(state=EngagementState.CREATED)
        with pytest.raises(InvalidStateTransitionError):
            EngagementStateMachine.transition(eng, EngagementState.ARCHIVED)

        eng_exec = create_sample_engagement(state=EngagementState.EXECUTING)
        with pytest.raises(InvalidStateTransitionError):
            EngagementStateMachine.transition(eng_exec, EngagementState.ARCHIVED)

    def test_no_backward_transitions_restart_or_undo(self):
        """Enforce rule: strictly forward-only; no restart or undo allowed."""
        # Cannot revert SCOPED -> CREATED
        eng_scoped = create_sample_engagement(state=EngagementState.SCOPED)
        with pytest.raises(InvalidStateTransitionError):
            EngagementStateMachine.transition(eng_scoped, EngagementState.CREATED)

        # Cannot revert EXECUTING -> SCOPED
        eng_exec = create_sample_engagement(state=EngagementState.EXECUTING)
        with pytest.raises(InvalidStateTransitionError):
            EngagementStateMachine.transition(eng_exec, EngagementState.SCOPED)

        # Cannot revert EXECUTING -> CREATED
        with pytest.raises(InvalidStateTransitionError):
            EngagementStateMachine.transition(eng_exec, EngagementState.CREATED)

        # Cannot revert COMPLETED -> EXECUTING
        eng_comp = create_sample_engagement(state=EngagementState.COMPLETED)
        with pytest.raises(InvalidStateTransitionError):
            EngagementStateMachine.transition(eng_comp, EngagementState.EXECUTING)

        # Cannot revert COMPLETED -> CREATED
        with pytest.raises(InvalidStateTransitionError):
            EngagementStateMachine.transition(eng_comp, EngagementState.CREATED)

    def test_archived_is_terminal_state(self):
        """Enforce rule: ARCHIVED is terminal; no further transitions allowed."""
        eng_arch = create_sample_engagement(state=EngagementState.ARCHIVED)
        for target in EngagementState:
            with pytest.raises(TerminalStateError):
                EngagementStateMachine.transition(eng_arch, target)

    def test_transition_dictionary_integrity(self):
        """Verify VALID_TRANSITIONS map integrity matches specification."""
        assert VALID_TRANSITIONS[EngagementState.CREATED] == [EngagementState.SCOPED]
        assert VALID_TRANSITIONS[EngagementState.SCOPED] == [EngagementState.EXECUTING]
        assert VALID_TRANSITIONS[EngagementState.EXECUTING] == [EngagementState.COMPLETED]
        assert VALID_TRANSITIONS[EngagementState.COMPLETED] == [EngagementState.ARCHIVED]
        assert VALID_TRANSITIONS[EngagementState.ARCHIVED] == []


class TestLaunchTiers:
    """Tests for 5 launch mechanics and tiers."""

    def test_five_launch_tiers_and_credit_costs(self):
        """Verify 5 launch tiers and credit costs match PRD Section 4.1."""
        assert TIER_CREDIT_COSTS[LaunchTier.QUICK_SCAN] == 5
        assert TIER_CREDIT_COSTS[LaunchTier.GUIDED] == 25
        assert TIER_CREDIT_COSTS[LaunchTier.AUDIT] == 100
        assert TIER_CREDIT_COSTS[LaunchTier.CONTINUOUS] == 0
        assert TIER_CREDIT_COSTS[LaunchTier.ARENA] == 0

    @pytest.mark.parametrize(
        "raw_input,expected_tier",
        [
            ("quick_scan", LaunchTier.QUICK_SCAN),
            ("Quick Scan", LaunchTier.QUICK_SCAN),
            ("Self-Service Quick Scan", LaunchTier.QUICK_SCAN),
            ("guided", LaunchTier.GUIDED),
            ("Guided Assessment", LaunchTier.GUIDED),
            ("audit", LaunchTier.AUDIT),
            ("Compliance Audit", LaunchTier.AUDIT),
            ("continuous", LaunchTier.CONTINUOUS),
            ("Continuous Validation", LaunchTier.CONTINUOUS),
            ("arena", LaunchTier.ARENA),
            ("Competitive Arena", LaunchTier.ARENA),
            ("free", LaunchTier.ARENA),
        ],
    )
    def test_parse_launch_tier_success(self, raw_input, expected_tier):
        """Verify robust tier parsing for human and machine string formats."""
        assert parse_launch_tier(raw_input) == expected_tier

    def test_parse_invalid_tier_raises_error(self):
        """Verify invalid tier string raises ValueError with options."""
        with pytest.raises(ValueError) as exc_info:
            parse_launch_tier("unsupported_tier")
        assert "Unknown launch tier 'unsupported_tier'" in str(exc_info.value)
