"""API integration tests for Engagement Manager endpoints.

Module 4: Arena - Pod Beta.
Follows strict zero em dash rule: only hyphens (-) or colons (:).

Tests endpoints:
- POST  /engagement/create
- PATCH /engagement/{id}/scope
- POST  /engagement/{id}/execute
- POST  /engagement/{id}/complete
- POST  /engagement/{id}/archive
"""

import pytest
from httpx import AsyncClient, ASGITransport

from app.engagement.api import app, repo, wallet, cooldown, event_bus
from app.engagement.models import LaunchTier


@pytest.fixture(autouse=True)
def reset_service_state():
    """Reset repository, wallet, cooldown, and event bus state between tests."""
    repo.clear()
    cooldown.reset()
    event_bus.clear()
    wallet._balances = {
        "tenant_zero": 0,
        "tenant_low": 10,
        "tenant_medium": 50,
        "tenant_high": 200,
        "tenant_whale": 999999,
    }
    wallet._entries.clear()
    wallet._idempotency.clear()


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.asyncio
class TestEngagementEndpoints:
    """Full lifecycle API endpoint tests."""

    async def test_create_engagement_with_wallet_debit(self, client: AsyncClient):
        """Verify POST /engagement/create debits wallet with idempotency key mod4:{engagement_id}."""
        init_balance = await wallet.get_balance("tenant_high")  # 200
        payload = {
            "tenant_id": "tenant_high",
            "engagement_type": "guided",  # 25 credits
            "scope": {"assets": ["10.0.0.1", "10.0.0.2"]},
        }

        resp = await client.post("/engagement/create", json=payload)
        assert resp.status_code == 201
        data = resp.json()

        assert data["state"] == "created"
        assert data["engagement_type"] == "guided"
        assert data["credit_cost"] == 25
        engagement_id = data["engagement_id"]

        # Verify wallet balance reduced by 25
        new_balance = await wallet.get_balance("tenant_high")
        assert new_balance == init_balance - 25

        # Verify idempotency key format: mod4:{engagement_id}
        entries = wallet.get_entries("tenant_high")
        assert len(entries) == 1
        assert entries[0].idempotency_key == f"mod4:{engagement_id}"
        assert entries[0].amount == 25
        assert entries[0].module_source == "mod4"

        # Verify event bus emitted engagement.lifecycle
        events = event_bus.get_published_events(event_bus.TOPIC_ENGAGEMENT_LIFECYCLE)
        assert len(events) == 1
        assert events[0].payload["state"] == "created"
        assert events[0].correlation_key.startswith("mod4-")

    async def test_create_engagement_insufficient_credits_aborts(self, client: AsyncClient):
        """Verify HTTP 402 aborts creation when wallet has insufficient credits."""
        payload = {
            "tenant_id": "tenant_zero",  # balance = 0
            "engagement_type": "quick_scan",  # 5 credits
            "scope": {"assets": ["192.168.1.1"]},
        }

        resp = await client.post("/engagement/create", json=payload)
        assert resp.status_code == 402
        assert "Need 5 credits, have 0" in resp.json()["detail"]

        # Verify no engagement created in repository
        assert len(repo.list_by_tenant("tenant_zero")) == 0

    async def test_create_engagement_free_arena_tier(self, client: AsyncClient):
        """Verify competitive Arena mode is free (0 credits) and does not debit wallet."""
        payload = {
            "tenant_id": "tenant_zero",
            "engagement_type": "arena",
            "scope": {"assets": ["app.example.com"]},
        }

        resp = await client.post("/engagement/create", json=payload)
        assert resp.status_code == 201
        data = resp.json()
        assert data["credit_cost"] == 0
        assert data["state"] == "created"

    async def test_create_engagement_cooldown_guardrail(self, client: AsyncClient):
        """Verify cooldown guardrail prevents rapid cycling on same engagement type."""
        payload = {
            "tenant_id": "tenant_high",
            "engagement_type": "quick_scan",
            "scope": {"assets": ["10.0.0.1"]},
        }

        resp1 = await client.post("/engagement/create", json=payload)
        assert resp1.status_code == 201

        # Immediate second creation on same tier should trigger HTTP 429
        resp2 = await client.post("/engagement/create", json=payload)
        assert resp2.status_code == 429
        assert "Cooldown active for tenant 'tenant_high'" in resp2.json()["detail"]

    async def test_update_scope_and_state_progression(self, client: AsyncClient):
        """Verify PATCH /engagement/{id}/scope updates scope and transitions state to SCOPED."""
        create_resp = await client.post(
            "/engagement/create",
            json={"tenant_id": "tenant_high", "engagement_type": "guided", "scope": {}},
        )
        eng_id = create_resp.json()["engagement_id"]

        scope_payload = {
            "scope_manifest": {
                "assets": ["10.0.0.1"],
                "techniques": ["T1059", "T1047"],
            },
            "confirmed": False,
        }

        patch_resp = await client.patch(f"/engagement/{eng_id}/scope", json=scope_payload)
        assert patch_resp.status_code == 200
        data = patch_resp.json()
        assert data["state"] == "scoped"
        assert len(data["scope"]["techniques"]) == 2

    async def test_scope_drift_guardrail_unconfirmed(self, client: AsyncClient):
        """Verify scope expansion over 120% without confirmation returns HTTP 400."""
        create_resp = await client.post(
            "/engagement/create",
            json={
                "tenant_id": "tenant_high",
                "engagement_type": "quick_scan",
                "scope": {"assets": ["10.0.0.1", "10.0.0.2", "10.0.0.3", "10.0.0.4", "10.0.0.5"]},  # 5 items
            },
        )
        eng_id = create_resp.json()["engagement_id"]

        # Expand to 10 assets (200% of original, > 120%)
        expanded_scope = {
            "scope_manifest": {
                "assets": [f"10.0.0.{i}" for i in range(1, 11)],
            },
            "confirmed": False,
        }

        patch_resp = await client.patch(f"/engagement/{eng_id}/scope", json=expanded_scope)
        assert patch_resp.status_code == 400
        assert "Scope drift detected" in patch_resp.json()["detail"]
        assert "200.0%" in patch_resp.json()["detail"]

    async def test_scope_drift_guardrail_confirmed_succeeds(self, client: AsyncClient):
        """Verify scope expansion over 120% succeeds when confirmed=True."""
        create_resp = await client.post(
            "/engagement/create",
            json={
                "tenant_id": "tenant_high",
                "engagement_type": "quick_scan",
                "scope": {"assets": ["10.0.0.1", "10.0.0.2", "10.0.0.3", "10.0.0.4", "10.0.0.5"]},  # 5 items
            },
        )
        eng_id = create_resp.json()["engagement_id"]

        # Expand to 10 assets with confirmed=True
        expanded_scope = {
            "scope_manifest": {
                "assets": [f"10.0.0.{i}" for i in range(1, 11)],
            },
            "confirmed": True,
        }

        patch_resp = await client.patch(f"/engagement/{eng_id}/scope", json=expanded_scope)
        assert patch_resp.status_code == 200
        data = patch_resp.json()
        assert data["state"] == "scoped"
        assert data["warning"] is not None
        assert "Scope expansion (200.0%) exceeds the 120% threshold" in data["warning"]

    async def test_cannot_execute_before_scoping(self, client: AsyncClient):
        """Verify cannot execute engagement directly from CREATED state."""
        create_resp = await client.post(
            "/engagement/create",
            json={"tenant_id": "tenant_high", "engagement_type": "quick_scan"},
        )
        eng_id = create_resp.json()["engagement_id"]

        exec_resp = await client.post(f"/engagement/{eng_id}/execute")
        assert exec_resp.status_code == 400
        assert "Cannot execute an engagement before scoping it" in exec_resp.json()["detail"]

    async def test_execute_from_scoped_succeeds(self, client: AsyncClient):
        """Verify execute succeeds once engagement is in SCOPED state."""
        create_resp = await client.post(
            "/engagement/create",
            json={"tenant_id": "tenant_high", "engagement_type": "quick_scan"},
        )
        eng_id = create_resp.json()["engagement_id"]

        await client.patch(
            f"/engagement/{eng_id}/scope",
            json={"scope_manifest": {"assets": ["10.0.0.1"]}, "confirmed": False},
        )

        exec_resp = await client.post(f"/engagement/{eng_id}/execute")
        assert exec_resp.status_code == 200
        data = exec_resp.json()
        assert data["state"] == "executing"

    async def test_cannot_complete_before_executing(self, client: AsyncClient):
        """Verify complete cannot be called on CREATED or SCOPED engagement."""
        create_resp = await client.post(
            "/engagement/create",
            json={"tenant_id": "tenant_high", "engagement_type": "quick_scan"},
        )
        eng_id = create_resp.json()["engagement_id"]

        comp_resp = await client.post(f"/engagement/{eng_id}/complete", json={"results": []})
        assert comp_resp.status_code == 400
        assert "Cannot complete an engagement before executing it" in comp_resp.json()["detail"]

    async def test_complete_generates_report_and_refunds_nodata(self, client: AsyncClient):
        """Verify completing engagement generates report and triggers refund for NoData."""
        # 1. Create engagement (Guided: 25 credits)
        create_resp = await client.post(
            "/engagement/create",
            json={"tenant_id": "tenant_high", "engagement_type": "guided"},
        )
        eng_id = create_resp.json()["engagement_id"]

        # 2. Scope
        await client.patch(
            f"/engagement/{eng_id}/scope",
            json={"scope_manifest": {"assets": ["target1"]}, "confirmed": False},
        )

        # 3. Execute
        await client.post(f"/engagement/{eng_id}/execute")

        # Check balance before complete: 200 - 25 = 175
        bal_before_comp = await wallet.get_balance("tenant_high")
        assert bal_before_comp == 175

        # 4. Complete with Module 2 outcomes (2 outcomes: 1 Detected, 1 NoData)
        complete_payload = {
            "results": [
                {
                    "technique_id": "T1059",
                    "playbook_hash": "sha256:pb1",
                    "verdict": "Detected",
                    "evidence_ref": "ev-01",
                },
                {
                    "technique_id": "T1003",
                    "playbook_hash": "sha256:pb2",
                    "verdict": "NoData",
                    "evidence_ref": "ev-02",
                },
            ]
        }

        comp_resp = await client.post(f"/engagement/{eng_id}/complete", json=complete_payload)
        assert comp_resp.status_code == 200
        data = comp_resp.json()
        assert data["state"] == "completed"
        assert data["report"] is not None

        # Verify summary report details
        report = data["report"]
        assert report["executive_summary"]["detected"] == 1
        assert report["executive_summary"]["nodata"] == 1
        assert report["refund"]["eligible"] is True
        # 1/2 of 25 = 12 credits refund (Python bankers rounding)
        assert report["refund"]["refund_amount"] == 12
        assert report["refund"]["refund_idempotency_key"] == f"mod4:{eng_id}:refund"

        # Verify wallet was credited back 12 credits automatically
        bal_after_refund = await wallet.get_balance("tenant_high")
        assert bal_after_refund == 175 + 12

    async def test_archive_engagement(self, client: AsyncClient):
        """Verify archiving an engagement and enforcing terminal state."""
        # Setup and progress through complete lifecycle
        create_resp = await client.post(
            "/engagement/create",
            json={"tenant_id": "tenant_high", "engagement_type": "quick_scan"},
        )
        eng_id = create_resp.json()["engagement_id"]

        await client.patch(
            f"/engagement/{eng_id}/scope",
            json={"scope_manifest": {"assets": ["10.0.0.1"]}, "confirmed": False},
        )
        await client.post(f"/engagement/{eng_id}/execute")
        await client.post(f"/engagement/{eng_id}/complete", json={"results": [{"technique_id": "T1", "verdict": "Detected"}]})

        # Archive
        arch_resp = await client.post(f"/engagement/{eng_id}/archive")
        assert arch_resp.status_code == 200
        data = arch_resp.json()
        assert data["state"] == "archived"
        assert data["archived_at"] is not None

        # Attempt to transition out of archived fails
        second_arch = await client.post(f"/engagement/{eng_id}/archive")
        assert second_arch.status_code == 400
        assert "terminal state" in second_arch.json()["detail"]
