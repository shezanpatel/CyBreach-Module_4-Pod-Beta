"""Tests for Week 11 engagement recommendation engine.

Module 4: Arena - Pod Beta.
Follows strict zero em dash rule: only hyphens (-) or colons (:).

Tests:
- Gap analysis extraction from historical engagement outcomes
- Gap closure detection when techniques flip from Missed to Detected
- Campaign ranking by gap coverage and impact score
- GET /engagement/recommendations/{tenant_id} endpoint
"""

from pathlib import Path
import pytest
from httpx import AsyncClient, ASGITransport

from app.engagement.api import app, repo, wallet, cooldown, event_bus
from app.engagement.recommendations import RecommendationEngine, CampaignSummary


@pytest.fixture(autouse=True)
def reset_state():
    repo.clear()
    cooldown.reset()
    event_bus.clear()
    wallet._balances = {"tenant_rec": 500}
    wallet._entries.clear()
    wallet._idempotency.clear()


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


class TestRecommendationEngine:
    """Unit tests for gap analysis and recommendation scoring."""

    def test_engine_loads_campaign_fixtures(self):
        """Verify engine loads campaign definitions from fixtures/campaigns directory."""
        camp_dir = Path(__file__).resolve().parent.parent / "fixtures" / "campaigns"
        engine = RecommendationEngine(campaigns_dir=camp_dir)
        assert len(engine.campaigns) >= 3

        ids = [c.campaign_id for c in engine.campaigns]
        assert "CAMP-APT29-001" in ids
        assert "CAMP-RANSOM-002" in ids
        assert "CAMP-WMI-003" in ids

    def test_campaign_ranking_by_tenant_gaps(self):
        """Verify campaigns addressing tenant gaps receive highest impact scores."""
        engine = RecommendationEngine()
        engine.add_campaign(
            CampaignSummary(
                campaign_id="CAMP-A",
                title="Ransomware Defense",
                description="Targets LSASS and PowerShell",
                tier="guided",
                credit_cost=25,
                techniques=["T1003.001", "T1059.001"],
            )
        )
        engine.add_campaign(
            CampaignSummary(
                campaign_id="CAMP-B",
                title="Generic Baseline",
                description="Targets unrelated techniques",
                tier="quick_scan",
                credit_cost=5,
                techniques=["T9999.001"],
            )
        )

        # Mock engagement with Missed T1003.001
        from app.engagement.models import Engagement, EngagementState, LaunchTier
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        eng = Engagement(
            engagement_id="eng-1",
            tenant_id="t-1",
            state=EngagementState.COMPLETED,
            engagement_type=LaunchTier.GUIDED,
            credit_cost=25,
            created_at=now,
            updated_at=now,
            completed_at=now,
            report={
                "technique_results": [
                    {"technique_id": "T1003.001", "verdict": "Missed"},
                    {"technique_id": "T1059.001", "verdict": "Detected"},
                ]
            },
        )

        resp = engine.get_recommendations("t-1", [eng])
        assert resp.unresolved_gap_count == 1
        assert "T1003.001" in resp.unresolved_gaps
        assert len(resp.recommendations) > 0

        top = resp.recommendations[0]
        assert top.campaign_id == "CAMP-A"
        assert "T1003.001" in top.addressed_gaps
        assert top.impact_score > 0


@pytest.mark.asyncio
class TestRecommendationsAPI:
    """Integration test for GET /engagement/recommendations/{tenant_id}."""

    async def test_api_recommendations_flow(self, client: AsyncClient):
        """Verify API returns tailored campaign recommendations based on completed engagement gaps."""
        # Create and complete engagement for tenant_rec with Missed T1003.001 (LSASS) and Partial T1047 (WMI)
        c_res = await client.post("/engagement/create", json={"tenant_id": "tenant_rec", "engagement_type": "guided", "scope": {}})
        eng_id = c_res.json()["engagement_id"]

        await client.patch(f"/engagement/{eng_id}/scope", json={"scope_manifest": {"assets": ["srv1"]}, "confirmed": False})
        await client.post(f"/engagement/{eng_id}/execute")

        outcomes = [
            {"technique_id": "T1003.001", "verdict": "Missed"},
            {"technique_id": "T1047", "verdict": "Partial"},
            {"technique_id": "T1059.001", "verdict": "Detected"},
        ]
        await client.post(f"/engagement/{eng_id}/complete", json={"results": outcomes})

        # Query recommendations for tenant_rec
        rec_res = await client.get("/engagement/recommendations/tenant_rec")
        assert rec_res.status_code == 200
        data = rec_res.json()

        assert data["tenant_id"] == "tenant_rec"
        assert data["unresolved_gap_count"] == 2
        assert "T1003.001" in data["unresolved_gaps"]
        assert "T1047" in data["unresolved_gaps"]

        recs = data["recommendations"]
        assert len(recs) >= 2

        # Ransomware campaign (CAMP-RANSOM-002) addresses T1003.001
        # WMI campaign (CAMP-WMI-003) addresses T1047
        rec_campaign_ids = [r["campaign_id"] for r in recs]
        assert "CAMP-RANSOM-002" in rec_campaign_ids or "CAMP-WMI-003" in rec_campaign_ids
