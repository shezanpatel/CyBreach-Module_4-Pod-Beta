"""Unit tests for summary report generator and refund eligibility logic.

Module 4: Arena - Pod Beta.
Follows strict zero em dash rule: only hyphens (-) or colons (:).
"""

import json
from pathlib import Path
import pytest

from app.engagement.models import OutcomeVerdict
from app.engagement.report_generator import ReportGenerator


class TestReportGenerator:
    """Tests for summary report generation from Module 2 outcomes."""

    @pytest.fixture
    def sample_outcomes(self):
        return [
            {
                "technique_id": "T1059.001",
                "playbook_hash": "sha256:hash1",
                "rule_hash": "sha256:rule1",
                "verdict": "Detected",
                "evidence_ref": "ev-01",
            },
            {
                "technique_id": "T1003.001",
                "playbook_hash": "sha256:hash2",
                "rule_hash": "sha256:rule2",
                "verdict": "Missed",
                "evidence_ref": "ev-02",
            },
            {
                "technique_id": "T1047",
                "playbook_hash": "sha256:hash3",
                "rule_hash": "sha256:rule3",
                "verdict": "Partial",
                "evidence_ref": "ev-03",
            },
            {
                "technique_id": "T1055.001",
                "playbook_hash": "sha256:hash4",
                "rule_hash": "sha256:rule4",
                "verdict": "NoData",
                "evidence_ref": "ev-04",
            },
        ]

    def test_compile_all_four_outcome_verdicts(self, sample_outcomes):
        """Verify summary report accurately counts Detected, Missed, Partial, and NoData."""
        report = ReportGenerator.generate_report(
            engagement_id="eng-1234",
            tenant_id="tenant-test",
            engagement_type="guided",
            outcomes=sample_outcomes,
            debited_credits=25,
        )

        exec_sum = report.executive_summary
        assert exec_sum.total_tested == 4
        assert exec_sum.detected == 1
        assert exec_sum.missed == 1
        assert exec_sum.partial == 1
        assert exec_sum.nodata == 1
        assert exec_sum.detection_rate_percentage == 25.0

    def test_nodata_flags_refund_eligibility(self, sample_outcomes):
        """Verify inconclusive verdicts (NoData) flag refund eligibility per PRD Section 3.1 & 4.3."""
        report = ReportGenerator.generate_report(
            engagement_id="eng-1234",
            tenant_id="tenant-test",
            engagement_type="guided",
            outcomes=sample_outcomes,
            debited_credits=25,
        )

        assert report.refund.eligible is True
        assert report.refund.nodata_count == 1
        # 1 of 4 outcomes is NoData -> round(1/4 * 25) = 6 credits refund
        assert report.refund.refund_amount == 6
        assert report.refund.refund_idempotency_key == "mod4:eng-1234:refund"
        assert "inconclusive" in report.refund.reason.lower()
        assert report.refund.sla_hours == 24
        assert report.refund.sla_deadline is not None

    def test_full_nodata_produces_full_refund(self):
        """Verify 100% NoData outcomes triggers full credit refund."""
        all_nodata = [
            {"technique_id": f"T100{i}", "verdict": "NoData"}
            for i in range(5)
        ]
        report = ReportGenerator.generate_report(
            engagement_id="eng-nodata-all",
            tenant_id="tenant-test",
            engagement_type="audit",
            outcomes=all_nodata,
            debited_credits=100,
        )

        assert report.refund.eligible is True
        assert report.refund.nodata_count == 5
        assert report.refund.refund_amount == 100
        assert report.refund.refund_idempotency_key == "mod4:eng-nodata-all:refund"

    def test_zero_nodata_means_not_eligible_for_refund(self):
        """Verify when all outcomes are conclusive, refund eligibility is False."""
        conclusive_outcomes = [
            {"technique_id": "T1059", "verdict": "Detected"},
            {"technique_id": "T1003", "verdict": "Missed"},
            {"technique_id": "T1047", "verdict": "Partial"},
        ]
        report = ReportGenerator.generate_report(
            engagement_id="eng-conclusive",
            tenant_id="tenant-test",
            engagement_type="guided",
            outcomes=conclusive_outcomes,
            debited_credits=25,
        )

        assert report.refund.eligible is False
        assert report.refund.refund_amount == 0
        assert report.refund.nodata_count == 0

    def test_remediation_recommendations_generated(self, sample_outcomes):
        """Verify remediation recommendations are generated for Missed and Partial techniques."""
        report = ReportGenerator.generate_report(
            engagement_id="eng-rem",
            tenant_id="tenant-test",
            engagement_type="quick_scan",
            outcomes=sample_outcomes,
            debited_credits=5,
        )

        assert len(report.remediation_recommendations) == 2
        priorities = {r.priority: r for r in report.remediation_recommendations}
        assert "HIGH" in priorities
        assert priorities["HIGH"].technique_id == "T1003.001"
        assert priorities["HIGH"].verdict == OutcomeVerdict.MISSED

        assert "MEDIUM" in priorities
        assert priorities["MEDIUM"].technique_id == "T1047"
        assert priorities["MEDIUM"].verdict == OutcomeVerdict.PARTIAL

    def test_comparison_with_previous_engagement(self, sample_outcomes):
        """Verify posture comparison and gap-closure metrics against previous engagement."""
        previous_outcomes = [
            {"technique_id": "T1059.001", "verdict": "Missed"},  # Flipped to Detected (Gap Closed)
            {"technique_id": "T1003.001", "verdict": "Missed"},
            {"technique_id": "T1047", "verdict": "Detected"},    # Flipped to Partial (Regression)
            {"technique_id": "T1055.001", "verdict": "Missed"},
        ]

        report = ReportGenerator.generate_report(
            engagement_id="eng-comp",
            tenant_id="tenant-test",
            engagement_type="guided",
            outcomes=sample_outcomes,
            debited_credits=25,
            previous_outcomes=previous_outcomes,
        )

        comp = report.comparison
        assert comp.has_baseline is True
        assert comp.previous_detection_rate == 25.0
        assert comp.current_detection_rate == 25.0
        assert "T1059.001" in comp.gaps_closed
        assert "T1047" in comp.regressions

    def test_render_markdown_and_to_dict(self, sample_outcomes):
        """Verify report serialization to dictionary and markdown rendering without em dashes."""
        report = ReportGenerator.generate_report(
            engagement_id="eng-render",
            tenant_id="tenant-test",
            engagement_type="quick_scan",
            outcomes=sample_outcomes,
            debited_credits=5,
        )

        data = report.to_dict()
        assert isinstance(data, dict)
        assert data["engagement_id"] == "eng-render"

        md = report.to_markdown()
        assert "# Engagement Summary Report: eng-render" in md
        # Zero em dash verification in generated markdown output
        assert "\u2014" not in md
        assert "\u2013" not in md
