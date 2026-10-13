"""FastAPI application for Engagement Manager service.

Module 4: Arena - Pod Beta.
Follows strict zero em dash rule: only hyphens (-) or colons (:).

Endpoints:
- POST  /engagement/create
- PATCH /engagement/{id}/scope
- POST  /engagement/{id}/execute
- POST  /engagement/{id}/complete
- POST  /engagement/{id}/archive
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel, Field

from app.engagement.models import (
    CreateEngagementRequest,
    Engagement,
    EngagementResponse,
    EngagementState,
    LaunchTier,
    TIER_CREDIT_COSTS,
    UpdateScopeRequest,
    CompleteEngagementRequest,
    RevalidationRequest,
    RevalidationResponse,
    parse_launch_tier,
)
from pathlib import Path
from app.engagement.state_machine import (
    EngagementStateMachine,
    InvalidStateTransitionError,
    TerminalStateError,
)
from app.engagement.guardrails import (
    BudgetValidator,
    CooldownChecker,
    CooldownViolationError,
    InsufficientCreditsError,
    ScopeDriftDetector,
    ScopeDriftError,
)
from app.engagement.report_generator import ReportGenerator, SummaryReport
from app.engagement.mock_wallet import MockWalletClient, WalletInsufficientCreditsError
from app.engagement.event_bus import EventBus
from app.engagement.recommendations import RecommendationEngine, RecommendationResponse


class EngagementRepository:
    """In-memory engagement storage for Pod Beta service."""

    def __init__(self):
        self._store: Dict[str, Engagement] = {}

    def get(self, engagement_id: str) -> Optional[Engagement]:
        return self._store.get(engagement_id)

    def save(self, engagement: Engagement) -> None:
        self._store[engagement.engagement_id] = engagement

    def list_by_tenant(self, tenant_id: str) -> List[Engagement]:
        return [e for e in self._store.values() if e.tenant_id == tenant_id]

    def clear(self) -> None:
        self._store.clear()


# Initialize FastAPI app
app = FastAPI(
    title="Engagement Manager",
    description="CyBreach Module 4: Arena - Engagement and Lifecycle Management Service",
    version="2.0.0",
)

# Service singletons
repo = EngagementRepository()
wallet = MockWalletClient(seed_tenants=True)
cooldown = CooldownChecker()
event_bus = EventBus()
campaigns_dir = Path(__file__).resolve().parent.parent.parent / "fixtures" / "campaigns"
recommendation_engine = RecommendationEngine(campaigns_dir=campaigns_dir)


@app.post("/engagement/create", response_model=EngagementResponse, status_code=status.HTTP_201_CREATED)
async def create_engagement(req: CreateEngagementRequest):
    """Create a new engagement in CREATED state.

    1. Parses and validates launch tier.
    2. Validates tenant cooldown to prevent rapid cycling.
    3. Enforces idempotency key format: mod4:{engagement_id}.
    4. Debits wallet; aborts with HTTP 402 if balance is insufficient.
    5. Transitions engagement to CREATED state.
    6. Emits engagement.lifecycle event.
    """
    try:
        tier = parse_launch_tier(req.engagement_type)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    # Guardrail: Check cooldown period between engagements of the same type
    try:
        cooldown.assert_not_in_cooldown(req.tenant_id, tier)
    except CooldownViolationError as e:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=str(e),
        )

    # Determine credit cost
    credit_cost = TIER_CREDIT_COSTS[tier]
    engagement_id = f"eng-{uuid.uuid4().hex[:12]}"

    # Enforce Idempotency Key format: mod4:{engagement_id}
    idempotency_key = f"mod4:{engagement_id}"

    # Wallet debit operation
    if credit_cost > 0:
        try:
            await wallet.debit(
                tenant_id=req.tenant_id,
                amount=credit_cost,
                idempotency_key=idempotency_key,
                reason=f"Engagement creation: {tier.value}",
                module_source="mod4",
            )
        except WalletInsufficientCreditsError as e:
            # Abort engagement creation per cross-pod requirement
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail=f"Need {e.cost} credits, have {e.balance}",
            )
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Wallet debit failed: {str(e)}",
            )

    # Record engagement for cooldown tracking
    cooldown.record_engagement(req.tenant_id, tier)

    now = datetime.now(timezone.utc)
    initial_scope = req.scope or {}
    engagement = Engagement(
        engagement_id=engagement_id,
        tenant_id=req.tenant_id,
        state=EngagementState.CREATED,
        engagement_type=tier,
        scope=initial_scope,
        original_scope=initial_scope,
        credit_cost=credit_cost,
        created_at=now,
        updated_at=now,
        metadata=req.metadata or {},
    )

    repo.save(engagement)

    # Publish engagement.lifecycle event
    await event_bus.publish_engagement_lifecycle(
        event_type="engagement.created",
        engagement_id=engagement_id,
        tenant_id=req.tenant_id,
        state=engagement.state.value,
        engagement_type=tier.value,
        details={"credit_cost": credit_cost, "scope": initial_scope},
    )

    return EngagementResponse(
        engagement_id=engagement.engagement_id,
        tenant_id=engagement.tenant_id,
        state=engagement.state,
        engagement_type=engagement.engagement_type.value,
        credit_cost=engagement.credit_cost,
        scope=engagement.scope,
        created_at=engagement.created_at,
        updated_at=engagement.updated_at,
        completed_at=engagement.completed_at,
        archived_at=engagement.archived_at,
        report=engagement.report,
    )


@app.patch("/engagement/{id}/scope", response_model=EngagementResponse)
async def update_engagement_scope(id: str, req: UpdateScopeRequest):
    """Update engagement scope and advance state to SCOPED.

    1. Checks if engagement exists.
    2. Validates forward-only progression (only CREATED or SCOPED may update scope).
    3. Evaluates scope drift: flags if expansion exceeds 120% without confirmation.
    4. Transitions state to SCOPED.
    5. Emits engagement.lifecycle event.
    """
    engagement = repo.get(id)
    if not engagement:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Engagement '{id}' not found",
        )

    # Verify state permits scope update
    if engagement.state in (EngagementState.EXECUTING, EngagementState.COMPLETED, EngagementState.ARCHIVED):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Cannot update scope for engagement in state '{engagement.state.value}'. "
                f"Scope can only be set or modified prior to execution."
            ),
        )

    # Guardrail: Scope drift detection (> 120% of original scope)
    baseline_scope = engagement.original_scope
    if not baseline_scope and engagement.scope:
        baseline_scope = engagement.scope

    try:
        drift_report = ScopeDriftDetector.assert_no_unconfirmed_drift(
            original_scope=baseline_scope,
            new_scope=req.scope_manifest,
            confirmed=req.confirmed,
        )
    except ScopeDriftError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )

    # Transition state to SCOPED if currently CREATED
    if engagement.state == EngagementState.CREATED:
        try:
            EngagementStateMachine.transition_to_scoped(engagement)
        except InvalidStateTransitionError as e:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    # If original_scope was empty, record this initial scope as baseline
    if not engagement.original_scope:
        engagement.original_scope = req.scope_manifest

    engagement.scope = req.scope_manifest
    engagement.updated_at = datetime.now(timezone.utc)
    repo.save(engagement)

    # Publish lifecycle event
    await event_bus.publish_engagement_lifecycle(
        event_type="engagement.scoped",
        engagement_id=engagement.engagement_id,
        tenant_id=engagement.tenant_id,
        state=engagement.state.value,
        engagement_type=engagement.engagement_type.value,
        details={
            "scope": engagement.scope,
            "drift_detected": drift_report.drift_detected,
            "expansion_percentage": drift_report.expansion_percentage,
        },
    )

    return EngagementResponse(
        engagement_id=engagement.engagement_id,
        tenant_id=engagement.tenant_id,
        state=engagement.state,
        engagement_type=engagement.engagement_type.value,
        credit_cost=engagement.credit_cost,
        scope=engagement.scope,
        created_at=engagement.created_at,
        updated_at=engagement.updated_at,
        completed_at=engagement.completed_at,
        archived_at=engagement.archived_at,
        report=engagement.report,
        warning=drift_report.warning,
    )


@app.post("/engagement/{id}/execute", response_model=EngagementResponse)
async def execute_engagement(id: str):
    """Transition engagement from SCOPED to EXECUTING.

    Enforces:
    - Tenant cannot execute an engagement before scoping it.
    - Forward-only state transition: SCOPED -> EXECUTING.
    - Emits engagement.lifecycle event.
    """
    engagement = repo.get(id)
    if not engagement:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Engagement '{id}' not found",
        )

    if engagement.state == EngagementState.CREATED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot execute an engagement before scoping it. Please define scope first.",
        )

    try:
        EngagementStateMachine.transition_to_executing(engagement)
    except (InvalidStateTransitionError, TerminalStateError) as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    repo.save(engagement)

    await event_bus.publish_engagement_lifecycle(
        event_type="engagement.executing",
        engagement_id=engagement.engagement_id,
        tenant_id=engagement.tenant_id,
        state=engagement.state.value,
        engagement_type=engagement.engagement_type.value,
        details={"scope": engagement.scope},
    )

    return EngagementResponse(
        engagement_id=engagement.engagement_id,
        tenant_id=engagement.tenant_id,
        state=engagement.state,
        engagement_type=engagement.engagement_type.value,
        credit_cost=engagement.credit_cost,
        scope=engagement.scope,
        created_at=engagement.created_at,
        updated_at=engagement.updated_at,
        completed_at=engagement.completed_at,
        archived_at=engagement.archived_at,
        report=engagement.report,
    )


@app.post("/engagement/{id}/complete", response_model=EngagementResponse)
async def complete_engagement(id: str, req: CompleteEngagementRequest):
    """Mark engagement as completed with Module 2 results.

    Enforces:
    - Tenant cannot complete an engagement before executing it.
    - Forward-only state transition: EXECUTING -> COMPLETED.
    - Compiles Module 2 outcomes (Detected, Missed, Partial, NoData).
    - Flags NoData for refund eligibility and credits back wallet if eligible.
    - Emits engagement.lifecycle event.
    """
    engagement = repo.get(id)
    if not engagement:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Engagement '{id}' not found",
        )

    if engagement.state != EngagementState.EXECUTING:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Cannot complete an engagement before executing it. "
                f"Current state: '{engagement.state.value}'."
            ),
        )

    # Generate summary report from Module 2 outcomes
    report = ReportGenerator.generate_report(
        engagement_id=engagement.engagement_id,
        tenant_id=engagement.tenant_id,
        engagement_type=engagement.engagement_type.value,
        outcomes=req.results,
        debited_credits=engagement.credit_cost,
    )

    # If NoData outcomes resulted in refund eligibility, trigger automatic refund
    if report.refund.eligible and report.refund.refund_amount > 0:
        await wallet.credit(
            tenant_id=engagement.tenant_id,
            amount=report.refund.refund_amount,
            idempotency_key=report.refund.refund_idempotency_key,
            reason=report.refund.reason or "Automatic refund for inconclusive (NoData) verdicts",
            module_source="mod4",
        )

    try:
        EngagementStateMachine.transition_to_completed(engagement)
    except (InvalidStateTransitionError, TerminalStateError) as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    engagement.report = report.to_dict()
    repo.save(engagement)

    await event_bus.publish_engagement_lifecycle(
        event_type="engagement.completed",
        engagement_id=engagement.engagement_id,
        tenant_id=engagement.tenant_id,
        state=engagement.state.value,
        engagement_type=engagement.engagement_type.value,
        details={
            "detection_rate": report.executive_summary.detection_rate_percentage,
            "refund_eligible": report.refund.eligible,
            "refund_amount": report.refund.refund_amount,
        },
    )

    return EngagementResponse(
        engagement_id=engagement.engagement_id,
        tenant_id=engagement.tenant_id,
        state=engagement.state,
        engagement_type=engagement.engagement_type.value,
        credit_cost=engagement.credit_cost,
        scope=engagement.scope,
        created_at=engagement.created_at,
        updated_at=engagement.updated_at,
        completed_at=engagement.completed_at,
        archived_at=engagement.archived_at,
        report=engagement.report,
    )


@app.post("/engagement/{id}/archive", response_model=EngagementResponse)
async def archive_engagement(id: str):
    """Transition engagement from COMPLETED to ARCHIVED (terminal state).

    Enforces:
    - Forward-only state transition: COMPLETED -> ARCHIVED.
    - No transitions permitted after ARCHIVED.
    - Emits engagement.lifecycle event.
    """
    engagement = repo.get(id)
    if not engagement:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Engagement '{id}' not found",
        )

    try:
        EngagementStateMachine.transition_to_archived(engagement)
    except (InvalidStateTransitionError, TerminalStateError) as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    repo.save(engagement)

    await event_bus.publish_engagement_lifecycle(
        event_type="engagement.archived",
        engagement_id=engagement.engagement_id,
        tenant_id=engagement.tenant_id,
        state=engagement.state.value,
        engagement_type=engagement.engagement_type.value,
    )

    return EngagementResponse(
        engagement_id=engagement.engagement_id,
        tenant_id=engagement.tenant_id,
        state=engagement.state,
        engagement_type=engagement.engagement_type.value,
        credit_cost=engagement.credit_cost,
        scope=engagement.scope,
        created_at=engagement.created_at,
        updated_at=engagement.updated_at,
        completed_at=engagement.completed_at,
        archived_at=engagement.archived_at,
        report=engagement.report,
    )


@app.get("/engagement/{id}", response_model=EngagementResponse)
async def get_engagement(id: str):
    """Retrieve an engagement by ID."""
    engagement = repo.get(id)
    if not engagement:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Engagement '{id}' not found",
        )
    return EngagementResponse(
        engagement_id=engagement.engagement_id,
        tenant_id=engagement.tenant_id,
        state=engagement.state,
        engagement_type=engagement.engagement_type.value,
        credit_cost=engagement.credit_cost,
        scope=engagement.scope,
        created_at=engagement.created_at,
        updated_at=engagement.updated_at,
        completed_at=engagement.completed_at,
        archived_at=engagement.archived_at,
        report=engagement.report,
    )


@app.get("/engagement/{id}/report")
async def get_engagement_report(id: str):
    """Retrieve summary report for a completed engagement."""
    engagement = repo.get(id)
    if not engagement:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Engagement '{id}' not found",
        )
    if not engagement.report:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No report available for engagement '{id}'. State: {engagement.state.value}",
        )
    return engagement.report


@app.post("/engagement/{id}/revalidate", response_model=RevalidationResponse, status_code=status.HTTP_201_CREATED)
async def revalidate_engagement_technique(id: str, req: RevalidationRequest):
    """Trigger one-click re-validation for a previously missed technique.

    Week 11 Deliverable (PRD Section 7.2 & 9.1):
    1. Validates parent engagement exists and has completed outcomes.
    2. Verifies the technique was tested and produced a gap (Missed or Partial).
    3. Debits 3 credits from tenant wallet with idempotency key mod4:{reval_eng_id}.
       Aborts with HTTP 402 if balance is insufficient.
    4. Creates a new targeted engagement in SCOPED state ready for execution.
    5. Emits lifecycle event and triggers Module 1 playbook re-run.
    """
    engagement = repo.get(id)
    if not engagement:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Parent engagement '{id}' not found",
        )

    if not engagement.report:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Parent engagement '{id}' does not have completed validation outcomes.",
        )

    # Check outcomes for requested technique
    tech_results = engagement.report.get("technique_results", [])
    matched_verdict = None
    matched_playbook = None

    for item in tech_results:
        if item.get("technique_id") == req.technique_id:
            matched_verdict = item.get("verdict")
            matched_playbook = item.get("playbook_hash")
            break

    if not matched_verdict:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Technique '{req.technique_id}' was not tested in parent engagement '{id}'.",
        )

    # Normalization
    v_str = matched_verdict.value if hasattr(matched_verdict, "value") else str(matched_verdict)
    if v_str == "Detected":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Technique '{req.technique_id}' was already validated as Detected. "
                f"Re-validation is only permitted for gap techniques (Missed or Partial)."
            ),
        )

    # Debit wallet: 3 credits per re-validation (PRD Section 9.1)
    reval_cost = 3
    reval_eng_id = f"eng-reval-{uuid.uuid4().hex[:10]}"
    idempotency_key = f"mod4:{reval_eng_id}"

    try:
        await wallet.debit(
            tenant_id=engagement.tenant_id,
            amount=reval_cost,
            idempotency_key=idempotency_key,
            reason=f"One-click re-validation for technique {req.technique_id}",
            module_source="mod4",
        )
    except WalletInsufficientCreditsError as e:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=f"Need {e.cost} credits, have {e.balance}",
        )

    now = datetime.now(timezone.utc)
    reval_scope = {
        "techniques": [req.technique_id],
        "target_count": 1,
        "playbook_hashes": [matched_playbook] if matched_playbook else [],
        "revalidation_of": id,
        "revalidation_target": req.technique_id,
        "notes": req.notes or "",
    }

    # Newly spawned re-validation engagement is directly in SCOPED state
    reval_engagement = Engagement(
        engagement_id=reval_eng_id,
        tenant_id=engagement.tenant_id,
        state=EngagementState.SCOPED,
        engagement_type=LaunchTier.QUICK_SCAN,
        scope=reval_scope,
        original_scope=reval_scope,
        credit_cost=reval_cost,
        created_at=now,
        updated_at=now,
        metadata={"parent_engagement_id": id, "type": "revalidation"},
    )

    repo.save(reval_engagement)

    # Publish lifecycle event and trigger Module 1 re-run playbook
    await event_bus.publish_engagement_lifecycle(
        event_type="engagement.revalidation.created",
        engagement_id=reval_eng_id,
        tenant_id=engagement.tenant_id,
        state=EngagementState.SCOPED.value,
        engagement_type="revalidation",
        details={
            "target_technique": req.technique_id,
            "parent_engagement_id": id,
            "playbook_hash": matched_playbook,
            "credit_cost": reval_cost,
        },
    )

    return RevalidationResponse(
        revalidation_engagement_id=reval_eng_id,
        parent_engagement_id=id,
        tenant_id=engagement.tenant_id,
        target_technique=req.technique_id,
        credit_cost=reval_cost,
        state=reval_engagement.state,
        scope=reval_scope,
        created_at=now,
        message=f"Re-validation engagement initialized and scoped for technique {req.technique_id}.",
    )


@app.get("/engagement/recommendations/{tenant_id}", response_model=RecommendationResponse)
async def get_engagement_recommendations(tenant_id: str, limit: int = 5):
    """Suggest most impactful next campaigns based on tenant gap analysis.

    Week 11 Deliverable (PRD Section 7.2):
    Analyzes historical Module 2 outcomes across tenant engagements, discovers unclosed
    gaps (Missed/Partial), and returns ranked campaigns to remediate those gaps.
    """
    tenant_engagements = repo.list_by_tenant(tenant_id)
    return recommendation_engine.get_recommendations(
        tenant_id=tenant_id,
        engagements=tenant_engagements,
        limit=limit,
    )

