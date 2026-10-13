"""Data models and schemas for the Engagement Manager.

Module 4: Arena - Pod Beta.
Follows strict zero em dash rule: only hyphens (-) or colons (:).
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class EngagementState(str, Enum):
    """The five sequential states of an engagement session."""
    CREATED = "created"
    SCOPED = "scoped"
    EXECUTING = "executing"
    COMPLETED = "completed"
    ARCHIVED = "archived"


VALID_TRANSITIONS: Dict[EngagementState, List[EngagementState]] = {
    EngagementState.CREATED: [EngagementState.SCOPED],
    EngagementState.SCOPED: [EngagementState.EXECUTING],
    EngagementState.EXECUTING: [EngagementState.COMPLETED],
    EngagementState.COMPLETED: [EngagementState.ARCHIVED],
    EngagementState.ARCHIVED: [],  # terminal state
}


class LaunchTier(str, Enum):
    """The five launch mechanics and tiers for Arena engagements."""
    QUICK_SCAN = "quick_scan"
    GUIDED = "guided"
    AUDIT = "audit"
    CONTINUOUS = "continuous"
    ARENA = "arena"


TIER_CREDIT_COSTS: Dict[LaunchTier, int] = {
    LaunchTier.QUICK_SCAN: 5,
    LaunchTier.GUIDED: 25,
    LaunchTier.AUDIT: 100,
    LaunchTier.CONTINUOUS: 0,  # Subscription-based: credits handled via subscription
    LaunchTier.ARENA: 0,       # Free entry: competitive leaderboard mode
}

# Mapping to accept human-friendly string inputs
TIER_NAME_ALIASES: Dict[str, LaunchTier] = {
    "quick_scan": LaunchTier.QUICK_SCAN,
    "quick scan": LaunchTier.QUICK_SCAN,
    "quick-scan": LaunchTier.QUICK_SCAN,
    "self-service quick scan": LaunchTier.QUICK_SCAN,
    "guided": LaunchTier.GUIDED,
    "guided assessment": LaunchTier.GUIDED,
    "audit": LaunchTier.AUDIT,
    "compliance audit": LaunchTier.AUDIT,
    "continuous": LaunchTier.CONTINUOUS,
    "continuous validation": LaunchTier.CONTINUOUS,
    "arena": LaunchTier.ARENA,
    "competitive arena": LaunchTier.ARENA,
    "free": LaunchTier.ARENA,
}


def parse_launch_tier(value: str) -> LaunchTier:
    """Normalize and parse an engagement tier string into a LaunchTier enum."""
    cleaned = value.strip().lower()
    if cleaned in TIER_NAME_ALIASES:
        return TIER_NAME_ALIASES[cleaned]
    # Check directly against values
    for tier in LaunchTier:
        if tier.value == cleaned:
            return tier
    valid_options = ", ".join([t.value for t in LaunchTier])
    raise ValueError(f"Unknown launch tier '{value}'. Valid options: {valid_options}")


class OutcomeVerdict(str, Enum):
    """Module 2 validation outcome verdicts."""
    DETECTED = "Detected"
    MISSED = "Missed"
    PARTIAL = "Partial"
    NODATA = "NoData"


class Module2Outcome(BaseModel):
    """Outcome payload item emitted by Module 2 validation."""
    technique_id: str
    playbook_hash: Optional[str] = None
    rule_hash: Optional[str] = None
    verdict: OutcomeVerdict
    evidence_ref: Optional[str] = ""
    timestamp: Optional[datetime] = None
    details: Dict[str, Any] = Field(default_factory=dict)


class ScopeManifest(BaseModel):
    """Scope configuration for an engagement."""
    assets: List[str] = Field(default_factory=list)
    techniques: List[str] = Field(default_factory=list)
    playbook_hashes: List[str] = Field(default_factory=list)
    target_count: Optional[int] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class Engagement(BaseModel):
    """Core engagement session entity."""
    engagement_id: str
    tenant_id: str
    state: EngagementState = EngagementState.CREATED
    engagement_type: LaunchTier
    scope: Dict[str, Any] = Field(default_factory=dict)
    original_scope: Dict[str, Any] = Field(default_factory=dict)
    credit_cost: int = 0
    created_at: datetime
    updated_at: datetime
    completed_at: Optional[datetime] = None
    archived_at: Optional[datetime] = None
    report: Optional[Dict[str, Any]] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class CreateEngagementRequest(BaseModel):
    """Request payload to create a new engagement."""
    tenant_id: str
    engagement_type: str
    scope: Optional[Dict[str, Any]] = Field(default_factory=dict)
    metadata: Optional[Dict[str, Any]] = Field(default_factory=dict)


class UpdateScopeRequest(BaseModel):
    """Request payload to update engagement scope."""
    scope_manifest: Dict[str, Any]
    confirmed: bool = False  # Set to True to acknowledge scope drift over 120%


class CompleteEngagementRequest(BaseModel):
    """Request payload to complete an engagement with Module 2 results."""
    results: List[Dict[str, Any]] = Field(default_factory=list)
    metadata: Optional[Dict[str, Any]] = Field(default_factory=dict)


class EngagementResponse(BaseModel):
    """Standard engagement response schema."""
    engagement_id: str
    tenant_id: str
    state: EngagementState
    engagement_type: str
    credit_cost: int
    scope: Dict[str, Any]
    created_at: datetime
    updated_at: datetime
    completed_at: Optional[datetime] = None
    archived_at: Optional[datetime] = None
    report: Optional[Dict[str, Any]] = None
    warning: Optional[str] = None


class RevalidationRequest(BaseModel):
    """Request payload to initiate a one-click re-validation for a missed technique."""
    technique_id: str
    notes: Optional[str] = None


class RevalidationResponse(BaseModel):
    """Response payload for a newly spawned re-validation engagement."""
    revalidation_engagement_id: str
    parent_engagement_id: str
    tenant_id: str
    target_technique: str
    credit_cost: int = 3
    state: EngagementState
    scope: Dict[str, Any]
    created_at: datetime
    message: str

