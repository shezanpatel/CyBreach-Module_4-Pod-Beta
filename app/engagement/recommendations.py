"""Engagement recommendation engine based on Module 2 gap analysis.

Module 4: Arena - Pod Beta (Week 11 Deliverable).
Follows strict zero em dash rule: only hyphens (-) or colons (:).

Analyzes tenant gap history from completed engagements and suggests
the most impactful campaigns from the catalog to remediate missed detections.
No dark patterns: informational guidance based on auditable evidence.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Set
from pydantic import BaseModel, Field

from app.engagement.models import Engagement, EngagementState, OutcomeVerdict


class CampaignSummary(BaseModel):
    """Catalog summary of a campaign for recommendation matching."""
    campaign_id: str
    title: str
    description: str
    tier: str
    credit_cost: int
    techniques: List[str]
    regulations: List[str] = Field(default_factory=list)
    asset_criticality: str = "medium"


class GapItem(BaseModel):
    """Identified detection gap for a tenant."""
    technique_id: str
    verdict: OutcomeVerdict
    engagement_id: str
    playbook_hash: Optional[str] = None
    rule_hash: Optional[str] = None


class GapAnalysisResult(BaseModel):
    """Consolidated gap analysis summary for a tenant."""
    tenant_id: str
    total_gaps: int
    missed_techniques: List[str]
    partial_techniques: List[str]
    gaps: List[GapItem]


class CampaignRecommendation(BaseModel):
    """Specific campaign recommendation tailored to close identified gaps."""
    campaign_id: str
    title: str
    tier: str
    credit_cost: int
    impact_score: float
    addressed_gaps: List[str]
    addressed_gap_count: int
    reason: str


class RecommendationResponse(BaseModel):
    """API response containing gap analysis and ranked campaign recommendations."""
    tenant_id: str
    unresolved_gap_count: int
    unresolved_gaps: List[str]
    recommendations: List[CampaignRecommendation]
    guidance_notice: str = "Informational recommendation based on verified gaps; no dark patterns."


class RecommendationEngine:
    """Computes gap analysis and recommends next impactful campaigns for tenants."""

    def __init__(self, campaigns_dir: Optional[Path] = None):
        self.campaigns: List[CampaignSummary] = []
        if campaigns_dir and campaigns_dir.exists():
            self.load_campaigns_from_dir(campaigns_dir)

    def load_campaigns_from_dir(self, directory: Path) -> None:
        """Load campaign definitions from a directory of JSON fixture files."""
        self.campaigns.clear()
        for p in directory.glob("*.json"):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                self.campaigns.append(
                    CampaignSummary(
                        campaign_id=data["campaign_id"],
                        title=data["title"],
                        description=data.get("description", ""),
                        tier=data.get("tier", "guided"),
                        credit_cost=data.get("credit_cost", 25),
                        techniques=data.get("techniques", []),
                        regulations=data.get("regulations", []),
                        asset_criticality=data.get("asset_criticality", "medium"),
                    )
                )
            except Exception:
                continue

    def add_campaign(self, campaign: CampaignSummary) -> None:
        """Register a campaign definition manually."""
        self.campaigns.append(campaign)

    @staticmethod
    def extract_tenant_gaps(engagements: List[Engagement]) -> GapAnalysisResult:
        """Analyze completed engagements for a tenant to discover unresolved gaps.

        Identifies techniques whose most recent validation was Missed or Partial.
        Techniques subsequently validated as Detected are considered resolved.
        """
        # Track most recent verdict per technique
        latest_verdict: Dict[str, OutcomeVerdict] = {}
        gap_records: Dict[str, GapItem] = {}

        # Sort completed engagements chronologically
        completed = [
            e for e in engagements
            if e.state in (EngagementState.COMPLETED, EngagementState.ARCHIVED) and e.report
        ]
        completed.sort(key=lambda x: x.completed_at or x.created_at)

        for eng in completed:
            tech_results = eng.report.get("technique_results", [])
            for res in tech_results:
                t_id = res.get("technique_id")
                raw_v = res.get("verdict")
                if not t_id or not raw_v:
                    continue

                v = OutcomeVerdict(raw_v) if isinstance(raw_v, str) else raw_v
                latest_verdict[t_id] = v

                if v in (OutcomeVerdict.MISSED, OutcomeVerdict.PARTIAL):
                    gap_records[t_id] = GapItem(
                        technique_id=t_id,
                        verdict=v,
                        engagement_id=eng.engagement_id,
                        playbook_hash=res.get("playbook_hash"),
                        rule_hash=res.get("rule_hash"),
                    )
                elif v == OutcomeVerdict.DETECTED and t_id in gap_records:
                    # Gap successfully closed
                    del gap_records[t_id]

        missed = [t for t, item in gap_records.items() if item.verdict == OutcomeVerdict.MISSED]
        partial = [t for t, item in gap_records.items() if item.verdict == OutcomeVerdict.PARTIAL]

        tenant_id = engagements[0].tenant_id if engagements else "unknown"

        return GapAnalysisResult(
            tenant_id=tenant_id,
            total_gaps=len(gap_records),
            missed_techniques=missed,
            partial_techniques=partial,
            gaps=list(gap_records.values()),
        )

    def get_recommendations(
        self,
        tenant_id: str,
        engagements: List[Engagement],
        limit: int = 5,
    ) -> RecommendationResponse:
        """Rank and return the most impactful campaigns to close the tenant's gaps."""
        gap_analysis = self.extract_tenant_gaps(engagements)
        unresolved_set: Set[str] = set(gap_analysis.missed_techniques + gap_analysis.partial_techniques)

        recommendations: List[CampaignRecommendation] = []

        for camp in self.campaigns:
            camp_tech_set = set(camp.techniques)
            matching_gaps = list(camp_tech_set.intersection(unresolved_set))
            match_count = len(matching_gaps)

            if match_count == 0 and len(unresolved_set) > 0:
                # If tenant has gaps, prioritize campaigns that address them
                continue

            # Weight Missed gaps higher (2.0) than Partial gaps (1.0)
            missed_overlap = sum(1 for g in matching_gaps if g in gap_analysis.missed_techniques)
            partial_overlap = sum(1 for g in matching_gaps if g in gap_analysis.partial_techniques)
            impact = (missed_overlap * 2.0) + (partial_overlap * 1.0)

            # Bonus for technique coverage ratio
            if len(camp.techniques) > 0:
                impact += round(match_count / len(camp.techniques), 2)

            if match_count > 0:
                reason = (
                    f"Directly addresses {match_count} identified security gap(s): "
                    f"{', '.join(matching_gaps)}. Remediate and validate detection rules."
                )
            else:
                reason = (
                    f"Recommended baseline campaign to validate security coverage "
                    f"across {len(camp.techniques)} core techniques."
                )

            recommendations.append(
                CampaignRecommendation(
                    campaign_id=camp.campaign_id,
                    title=camp.title,
                    tier=camp.tier,
                    credit_cost=camp.credit_cost,
                    impact_score=round(impact, 2),
                    addressed_gaps=matching_gaps,
                    addressed_gap_count=match_count,
                    reason=reason,
                )
            )

        # Sort descending by impact score
        recommendations.sort(key=lambda r: r.impact_score, reverse=True)

        return RecommendationResponse(
            tenant_id=tenant_id,
            unresolved_gap_count=len(unresolved_set),
            unresolved_gaps=sorted(list(unresolved_set)),
            recommendations=recommendations[:limit],
        )
