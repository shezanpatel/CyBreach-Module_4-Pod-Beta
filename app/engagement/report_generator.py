"""Summary report generation for engagements.

Module 4: Arena - Pod Beta.
Follows strict zero em dash rule: only hyphens (-) or colons (:).

Compiles Module 2 outcomes (Detected, Missed, Partial, NoData).
Flags NoData outcomes for refund eligibility per CyBreach PRD Section 3.1 & 4.3.
Generates executive summary, technique-level details, remediation recommendations,
and comparative metrics against previous engagements.
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from app.engagement.models import OutcomeVerdict


class TechniqueResult(BaseModel):
    """Detailed technique-level test result."""
    technique_id: str
    playbook_hash: Optional[str] = None
    rule_hash: Optional[str] = None
    verdict: OutcomeVerdict
    evidence_ref: Optional[str] = ""
    timestamp: Optional[str] = None
    notes: Optional[str] = None


class RefundDetails(BaseModel):
    """Details regarding refund eligibility due to inconclusive verdicts (NoData)."""
    eligible: bool = False
    nodata_count: int = 0
    total_outcomes: int = 0
    refund_amount: int = 0
    refund_idempotency_key: Optional[str] = None
    reason: Optional[str] = None
    sla_hours: int = 24
    sla_deadline: Optional[str] = None


class ExecutiveSummary(BaseModel):
    """High-level summary of engagement outcomes and security posture."""
    total_tested: int = 0
    detected: int = 0
    missed: int = 0
    partial: int = 0
    nodata: int = 0
    detection_rate_percentage: float = 0.0
    posture_status: str = "PENDING"
    headline: str = ""


class ComparisonMetrics(BaseModel):
    """Side-by-side comparison with the tenant's previous engagement."""
    has_baseline: bool = False
    previous_detection_rate: Optional[float] = None
    current_detection_rate: float = 0.0
    detection_rate_delta: Optional[float] = None
    gaps_closed: List[str] = Field(default_factory=list)
    regressions: List[str] = Field(default_factory=list)


class RemediationItem(BaseModel):
    """Actionable remediation recommendation for missed or partial detections."""
    technique_id: str
    priority: str  # 'HIGH', 'MEDIUM', 'LOW'
    verdict: OutcomeVerdict
    recommendation: str
    re_validate_eligible: bool = True


class SummaryReport(BaseModel):
    """Complete structured engagement summary report."""
    engagement_id: str
    tenant_id: str
    engagement_type: str
    generated_at: str
    executive_summary: ExecutiveSummary
    technique_results: List[TechniqueResult]
    remediation_recommendations: List[RemediationItem]
    refund: RefundDetails
    comparison: ComparisonMetrics
    metadata: Dict[str, Any] = Field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert report to nested dictionary."""
        return self.model_dump()

    def to_markdown(self) -> str:
        """Render report as human-readable markdown without em dashes."""
        exec_sum = self.executive_summary
        lines = [
            f"# Engagement Summary Report: {self.engagement_id}",
            f"**Tenant ID:** {self.tenant_id} | **Tier:** {self.engagement_type} | **Generated:** {self.generated_at}",
            "",
            "## 1. Executive Summary",
            f"- **Status:** {exec_sum.posture_status}",
            f"- **Headline:** {exec_sum.headline}",
            f"- **Total Techniques Tested:** {exec_sum.total_tested}",
            f"- **Detected:** {exec_sum.detected} ({exec_sum.detection_rate_percentage}%)",
            f"- **Missed:** {exec_sum.missed}",
            f"- **Partial:** {exec_sum.partial}",
            f"- **NoData (Inconclusive):** {exec_sum.nodata}",
            "",
            "## 2. Refund Eligibility",
            f"- **Eligible:** {'YES' if self.refund.eligible else 'NO'}",
        ]

        if self.refund.eligible:
            lines.extend([
                f"- **Refund Amount:** {self.refund.refund_amount} credits",
                f"- **Reason:** {self.refund.reason}",
                f"- **Idempotency Key:** {self.refund.refund_idempotency_key}",
                f"- **SLA:** Within {self.refund.sla_hours} hours (Deadline: {self.refund.sla_deadline})",
            ])
        else:
            lines.append("- All validation tests produced definitive verdicts. No refund required.")

        lines.extend([
            "",
            "## 3. Technique Results",
            "| Technique | Verdict | Playbook Hash | Rule Hash | Evidence Ref |",
            "|---|---|---|---|---|",
        ])

        for tr in self.technique_results:
            p_hash = tr.playbook_hash or "N/A"
            r_hash = tr.rule_hash or "N/A"
            e_ref = tr.evidence_ref or "N/A"
            lines.append(f"| {tr.technique_id} | {tr.verdict.value} | {p_hash[:12]} | {r_hash[:12]} | {e_ref} |")

        if self.remediation_recommendations:
            lines.extend([
                "",
                "## 4. Remediation Recommendations",
            ])
            for rem in self.remediation_recommendations:
                lines.append(
                    f"- **[{rem.priority}] Technique {rem.technique_id}** ({rem.verdict.value}): {rem.recommendation}"
                )

        if self.comparison.has_baseline:
            lines.extend([
                "",
                "## 5. Comparative Posture vs Previous Engagement",
                f"- **Previous Detection Rate:** {self.comparison.previous_detection_rate}%",
                f"- **Current Detection Rate:** {self.comparison.current_detection_rate}%",
                f"- **Delta:** {self.comparison.detection_rate_delta:+0.2f}%",
                f"- **Gaps Closed:** {', '.join(self.comparison.gaps_closed) if self.comparison.gaps_closed else 'None'}",
                f"- **Regressions:** {', '.join(self.comparison.regressions) if self.comparison.regressions else 'None'}",
            ])

        return "\n".join(lines)


class ReportGenerator:
    """Generates structured engagement summary reports from Module 2 outcomes."""

    @classmethod
    def normalize_verdict(cls, verdict_raw: Any) -> OutcomeVerdict:
        """Map raw verdict string to OutcomeVerdict enum."""
        if isinstance(verdict_raw, OutcomeVerdict):
            return verdict_raw

        val = str(verdict_raw).strip()
        for v in OutcomeVerdict:
            if v.value.lower() == val.lower():
                return v

        # Default mapping fallback
        raise ValueError(f"Unknown outcome verdict '{verdict_raw}'. Expected Detected, Missed, Partial, or NoData.")

    @classmethod
    def generate_report(
        cls,
        engagement_id: str,
        tenant_id: str,
        engagement_type: str,
        outcomes: List[Dict[str, Any]],
        debited_credits: int = 0,
        previous_outcomes: Optional[List[Dict[str, Any]]] = None,
        generated_at: Optional[datetime] = None,
    ) -> SummaryReport:
        """Compile Module 2 outcomes into a full SummaryReport.

        Identifies Detected, Missed, Partial, and NoData verdicts.
        Flags NoData for credit refund eligibility.
        Calculates refund amount pro-rata based on debited credits.
        """
        now = generated_at or datetime.now(timezone.utc)
        now_str = now.isoformat()

        technique_results: List[TechniqueResult] = []
        detected_count = 0
        missed_count = 0
        partial_count = 0
        nodata_count = 0

        current_verdicts_by_tech: Dict[str, OutcomeVerdict] = {}

        for item in outcomes:
            tech_id = item.get("technique_id", "UNKNOWN")
            verdict = cls.normalize_verdict(item.get("verdict"))
            current_verdicts_by_tech[tech_id] = verdict

            if verdict == OutcomeVerdict.DETECTED:
                detected_count += 1
            elif verdict == OutcomeVerdict.MISSED:
                missed_count += 1
            elif verdict == OutcomeVerdict.PARTIAL:
                partial_count += 1
            elif verdict == OutcomeVerdict.NODATA:
                nodata_count += 1

            t_ts = item.get("timestamp")
            if isinstance(t_ts, datetime):
                t_ts_str = t_ts.isoformat()
            else:
                t_ts_str = str(t_ts) if t_ts else now_str

            technique_results.append(
                TechniqueResult(
                    technique_id=tech_id,
                    playbook_hash=item.get("playbook_hash"),
                    rule_hash=item.get("rule_hash"),
                    verdict=verdict,
                    evidence_ref=item.get("evidence_ref", ""),
                    timestamp=t_ts_str,
                    notes=item.get("notes"),
                )
            )

        total_tested = len(outcomes)
        conclusive_count = detected_count + missed_count + partial_count

        if total_tested > 0:
            detection_rate = round((detected_count / total_tested) * 100.0, 2)
        else:
            detection_rate = 0.0

        # Assess overall posture status
        if total_tested == 0:
            posture_status = "EMPTY"
            headline = "No validation outcomes were processed."
        elif detection_rate >= 80.0:
            posture_status = "STRONG"
            headline = f"High detection efficacy: {detected_count} of {total_tested} techniques validated."
        elif detection_rate >= 50.0:
            posture_status = "MODERATE"
            headline = f"Moderate detection coverage: {detected_count} of {total_tested} techniques validated."
        else:
            posture_status = "DEFICIENT"
            headline = f"Security gaps identified: {missed_count} missed, {partial_count} partial out of {total_tested} techniques."

        exec_summary = ExecutiveSummary(
            total_tested=total_tested,
            detected=detected_count,
            missed=missed_count,
            partial=partial_count,
            nodata=nodata_count,
            detection_rate_percentage=detection_rate,
            posture_status=posture_status,
            headline=headline,
        )

        # Inconclusive verdict refund processing (Section 3.1 & 4.3)
        refund_eligible = nodata_count > 0 and debited_credits > 0
        refund_amount = 0
        refund_idempotency_key = None
        refund_reason = None
        sla_deadline = None

        if refund_eligible:
            # Pro-rata refund calculation based on proportion of inconclusive outcomes
            refund_amount = round((nodata_count / total_tested) * debited_credits)
            # Ensure at least 1 credit refunded if eligible and debited > 0
            refund_amount = max(1, min(refund_amount, debited_credits))
            # Format idempotency key: mod4:{engagement_id}:refund (matches PDF specification)
            refund_idempotency_key = f"mod4:{engagement_id}:refund"
            refund_reason = (
                f"Automatic refund for {nodata_count} inconclusive (NoData) validation outcome(s) "
                f"out of {total_tested} total tests."
            )
            deadline_dt = now + timedelta(hours=24)
            sla_deadline = deadline_dt.isoformat()
        elif nodata_count > 0 and debited_credits == 0:
            refund_reason = f"{nodata_count} inconclusive (NoData) outcomes detected on a zero-credit tier."

        refund_details = RefundDetails(
            eligible=refund_eligible,
            nodata_count=nodata_count,
            total_outcomes=total_tested,
            refund_amount=refund_amount,
            refund_idempotency_key=refund_idempotency_key,
            reason=refund_reason,
            sla_hours=24,
            sla_deadline=sla_deadline,
        )

        # Remediation recommendations for Missed and Partial techniques
        remediation_recommendations: List[RemediationItem] = []
        for tr in technique_results:
            if tr.verdict == OutcomeVerdict.MISSED:
                remediation_recommendations.append(
                    RemediationItem(
                        technique_id=tr.technique_id,
                        priority="HIGH",
                        verdict=OutcomeVerdict.MISSED,
                        recommendation=(
                            f"Detection rule missing or alert suppressed for {tr.technique_id}. "
                            f"Create or tune SIEM/EDR detection rule and execute one-click re-validation."
                        ),
                        re_validate_eligible=True,
                    )
                )
            elif tr.verdict == OutcomeVerdict.PARTIAL:
                remediation_recommendations.append(
                    RemediationItem(
                        technique_id=tr.technique_id,
                        priority="MEDIUM",
                        verdict=OutcomeVerdict.PARTIAL,
                        recommendation=(
                            f"Telemetry partially captured for {tr.technique_id}. "
                            f"Enrich endpoint logging sensor parameters to ensure complete evidence."
                        ),
                        re_validate_eligible=True,
                    )
                )

        # Previous engagement comparison
        has_baseline = False
        prev_rate = None
        delta_rate = None
        gaps_closed: List[str] = []
        regressions: List[str] = []

        if previous_outcomes:
            has_baseline = True
            prev_detected = 0
            prev_total = len(previous_outcomes)
            prev_verdicts: Dict[str, OutcomeVerdict] = {}

            for p_item in previous_outcomes:
                p_tid = p_item.get("technique_id", "UNKNOWN")
                p_verdict = cls.normalize_verdict(p_item.get("verdict"))
                prev_verdicts[p_tid] = p_verdict
                if p_verdict == OutcomeVerdict.DETECTED:
                    prev_detected += 1

            if prev_total > 0:
                prev_rate = round((prev_detected / prev_total) * 100.0, 2)
                delta_rate = round(detection_rate - prev_rate, 2)

            for tid, curr_v in current_verdicts_by_tech.items():
                old_v = prev_verdicts.get(tid)
                if old_v in (OutcomeVerdict.MISSED, OutcomeVerdict.PARTIAL) and curr_v == OutcomeVerdict.DETECTED:
                    gaps_closed.append(tid)
                elif old_v == OutcomeVerdict.DETECTED and curr_v in (OutcomeVerdict.MISSED, OutcomeVerdict.PARTIAL):
                    regressions.append(tid)

        comparison_metrics = ComparisonMetrics(
            has_baseline=has_baseline,
            previous_detection_rate=prev_rate,
            current_detection_rate=detection_rate,
            detection_rate_delta=delta_rate,
            gaps_closed=gaps_closed,
            regressions=regressions,
        )

        return SummaryReport(
            engagement_id=engagement_id,
            tenant_id=tenant_id,
            engagement_type=engagement_type,
            generated_at=now_str,
            executive_summary=exec_summary,
            technique_results=technique_results,
            remediation_recommendations=remediation_recommendations,
            refund=refund_details,
            comparison=comparison_metrics,
        )
