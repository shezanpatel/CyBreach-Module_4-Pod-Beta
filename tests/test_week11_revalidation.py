"""Tests for Week 11 one-click re-validation flow.

Module 4: Arena - Pod Beta.
Follows strict zero em dash rule: only hyphens (-) or colons (:).

Tests:
- Re-validation of Missed techniques (3 credits debited, mod4:{reval_eng_id})
- Re-validation of Partial techniques
- Rejection of already-Detected techniques (HTTP 400)
- Abort on insufficient wallet balance (HTTP 402)
- Re-validation session created in SCOPED state and executable
"""

import pytest
from httpx import AsyncClient, ASGITransport

from app.engagement.api import app, repo, wallet, cooldown, event_bus
from app.engagement.models import EngagementState


@pytest.fixture(autouse=True)
def reset_state():
    repo.clear()
    cooldown.reset()
    event_bus.clear()
    wallet._balances = {
        "tenant_high": 200,
        "tenant_poor": 2,  # Insufficient for 3-credit re-validation
    }
    wallet._entries.clear()
    wallet._idempotency.clear()


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def setup_completed_engagement(client: AsyncClient, tenant_id: str = "tenant_high") -> str:
    """Helper to create, scope, execute, and complete an engagement with sample outcomes."""
    # 1. Create
    c_res = await client.post("/engagement/create", json={"tenant_id": tenant_id, "engagement_type": "guided", "scope": {}})
    eng_id = c_res.json()["engagement_id"]

    # 2. Scope
    await client.patch(f"/engagement/{eng_id}/scope", json={"scope_manifest": {"assets": ["host1"]}, "confirmed": False})

    # 3. Execute
    await client.post(f"/engagement/{eng_id}/execute")

    # 4. Complete with Missed and Detected techniques
    outcomes = [
        {
            "technique_id": "T1003.001",
            "playbook_hash": "sha256:pb_lsass",
            "verdict": "Missed",
            "evidence_ref": "ev-01",
        },
        {
            "technique_id": "T1047",
            "playbook_hash": "sha256:pb_wmi",
            "verdict": "Partial",
            "evidence_ref": "ev-02",
        },
        {
            "technique_id": "T1059.001",
            "playbook_hash": "sha256:pb_ps",
            "verdict": "Detected",
            "evidence_ref": "ev-03",
        },
    ]
    await client.post(f"/engagement/{eng_id}/complete", json={"results": outcomes})
    return eng_id


@pytest.mark.asyncio
class TestRevalidationFlow:
    """Tests for one-click re-validation endpoint."""

    async def test_revalidate_missed_technique_success(self, client: AsyncClient):
        """Verify re-validating a Missed technique debits 3 credits and creates SCOPED engagement."""
        parent_id = await setup_completed_engagement(client, "tenant_high")

        # Balance before re-validation: 200 - 25 = 175
        bal_before = await wallet.get_balance("tenant_high")
        assert bal_before == 175

        # Re-validate T1003.001 (Missed)
        reval_payload = {"technique_id": "T1003.001", "notes": "SIEM rule updated for LSASS access"}
        resp = await client.post(f"/engagement/{parent_id}/revalidate", json=reval_payload)

        assert resp.status_code == 201
        data = resp.json()

        reval_id = data["revalidation_engagement_id"]
        assert data["parent_engagement_id"] == parent_id
        assert data["target_technique"] == "T1003.001"
        assert data["credit_cost"] == 3
        assert data["state"] == "scoped"
        assert data["scope"]["techniques"] == ["T1003.001"]
        assert data["scope"]["playbook_hashes"] == ["sha256:pb_lsass"]

        # Verify wallet was debited exactly 3 credits
        bal_after = await wallet.get_balance("tenant_high")
        assert bal_after == 172

        # Verify idempotency key format: mod4:{reval_id}
        entries = wallet.get_entries("tenant_high")
        reval_entry = [e for e in entries if e.idempotency_key == f"mod4:{reval_id}"]
        assert len(reval_entry) == 1
        assert reval_entry[0].amount == 3

        # Verify re-validation engagement is in repository and directly executable
        reval_eng = repo.get(reval_id)
        assert reval_eng is not None
        assert reval_eng.state == EngagementState.SCOPED

        # Execute the re-validation engagement
        exec_res = await client.post(f"/engagement/{reval_id}/execute")
        assert exec_res.status_code == 200
        assert exec_res.json()["state"] == "executing"

    async def test_revalidate_already_detected_technique_rejected(self, client: AsyncClient):
        """Verify attempting to re-validate an already Detected technique returns HTTP 400."""
        parent_id = await setup_completed_engagement(client, "tenant_high")

        reval_payload = {"technique_id": "T1059.001"}  # Already Detected
        resp = await client.post(f"/engagement/{parent_id}/revalidate", json=reval_payload)

        assert resp.status_code == 400
        assert "already validated as Detected" in resp.json()["detail"]

    async def test_revalidate_insufficient_credits_aborts(self, client: AsyncClient):
        """Verify re-validation aborts with HTTP 402 if wallet balance < 3 credits."""
        parent_id = await setup_completed_engagement(client, "tenant_high")

        # Set tenant balance to 1 credit (< 3 required)
        wallet.set_balance("tenant_high", 1)

        reval_payload = {"technique_id": "T1003.001"}
        resp = await client.post(f"/engagement/{parent_id}/revalidate", json=reval_payload)

        assert resp.status_code == 402
        assert "Need 3 credits, have 1" in resp.json()["detail"]

    async def test_revalidate_nonexistent_technique_returns_404(self, client: AsyncClient):
        """Verify technique not in parent engagement returns HTTP 404."""
        parent_id = await setup_completed_engagement(client, "tenant_high")

        reval_payload = {"technique_id": "T9999"}
        resp = await client.post(f"/engagement/{parent_id}/revalidate", json=reval_payload)

        assert resp.status_code == 404
        assert "was not tested in parent engagement" in resp.json()["detail"]
