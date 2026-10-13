"""
CyBreach Module 4: Arena - Campaign Library Tests

Tests covering:
1. Content-addressed hash-pinning of Module 1 playbooks (strict SHA-256).
2. Hash-mismatch detection when Module 1 playbook hashes change.
3. Multi-axis catalog search (tier, region with global fallback, techniques, regulations).
4. Boss-fight multi-stage campaign logic, stage progression, and composite scoring.
5. Endpoints: GET /campaigns and GET /campaigns/{campaign_id}.
6. Strict adherence to code rules: Zero em dashes.
"""
from pathlib import Path
import pytest
from httpx import ASGITransport, AsyncClient

from app.campaign.models import (
    CampaignDefinition,
    BossStage,
    CampaignTier,
    AssetCriticality,
    BossFightSession,
    validate_sha256_hash,
)
from app.campaign.hash_checker import (
    compute_sha256,
    compute_canonical_content_hash,
    verify_campaign_hashes,
    detect_affected_campaigns,
    Module1PlaybookRegistry,
)
from app.campaign.api import app, catalog_service


@pytest.fixture(autouse=True)
def reset_catalog():
    """Reset catalog service state before each test."""
    catalog_service.reload_all()


# =====================================================================
# 1. Content-Addressed Hash-Pinning Tests
# =====================================================================

def test_valid_sha256_hash_validation():
    valid = "sha256:9a4e2e5f211e30e6e4ed7fbf66f8024d623ac6e0b52d1481c233dff39e00d785"
    assert validate_sha256_hash(valid) == valid


def test_invalid_sha256_hash_validation():
    with pytest.raises(ValueError, match="Invalid content-addressed hash"):
        validate_sha256_hash("md5:1234567890abcdef")

    with pytest.raises(ValueError, match="Invalid content-addressed hash"):
        validate_sha256_hash("playbook-name-not-hash")

    with pytest.raises(ValueError, match="Invalid content-addressed hash"):
        validate_sha256_hash("sha256:short")


def test_campaign_rejects_non_sha256_hashes():
    with pytest.raises(ValueError, match="Invalid content-addressed hash"):
        CampaignDefinition(
            campaign_id="CAMP-INVALID-001",
            title="Invalid Campaign",
            description="Testing non-sha256 hash rejection",
            playbook_hashes=["unhashed_playbook_name"],
            tier="quick_scan",
            credit_cost=5,
        )


def test_deterministic_content_hash_calculation():
    raw_data_1 = {
        "campaign_id": "CAMP-TEST-001",
        "title": "Deterministic Test",
        "description": "Verifying deterministic content hash",
        "playbook_hashes": [
            "sha256:9a4e2e5f211e30e6e4ed7fbf66f8024d623ac6e0b52d1481c233dff39e00d785"
        ],
        "tier": "quick_scan",
        "regions": ["global"],
        "techniques": ["T1190"],
        "regulations": ["NIST"],
        "credit_cost": 5,
        "asset_criticality": "low",
        "stages": [],
    }
    raw_data_2 = dict(raw_data_1)

    hash_1 = compute_canonical_content_hash(raw_data_1)
    hash_2 = compute_canonical_content_hash(raw_data_2)
    assert hash_1 == hash_2
    assert hash_1.startswith("sha256:")
    assert len(hash_1) == 71  # "sha256:" + 64 hex chars

    # Modifying description changes hash
    raw_data_modified = dict(raw_data_1, description="Modified description")
    hash_mod = compute_canonical_content_hash(raw_data_modified)
    assert hash_1 != hash_mod


# =====================================================================
# 2. Hash-Mismatch Detection Tests
# =====================================================================

def test_hash_verification_matches_intact_catalog():
    camp = catalog_service.get_campaign("CAMP-QS-001", check_hashes=True)
    assert camp is not None
    assert camp.hash_mismatch is False
    assert camp.needs_review is False
    assert len(camp.mismatched_playbooks) == 0


def test_hash_mismatch_detected_on_upstream_change():
    camp = catalog_service.get_campaign("CAMP-QS-001", check_hashes=False)
    assert camp is not None

    # Simulate upstream Module 1 playbook change
    playbook_id = "MOD1-PB-PERIMETER-001"
    new_hash = "sha256:ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
    catalog_service.registry.update_playbook_hash(playbook_id, new_hash)

    # Re-verify campaign hashes
    verified_camp = verify_campaign_hashes(camp, catalog_service.registry)
    assert verified_camp.hash_mismatch is True
    assert verified_camp.needs_review is True
    assert len(verified_camp.mismatched_playbooks) == 1


def test_hash_mismatch_detected_for_unknown_playbook():
    unknown_hash = "sha256:0000000000000000000000000000000000000000000000000000000000000000"
    camp = CampaignDefinition(
        campaign_id="CAMP-UNKNOWN-001",
        title="Unknown Hash Campaign",
        playbook_hashes=[unknown_hash],
        tier="quick_scan",
        credit_cost=5,
    )
    verified = verify_campaign_hashes(camp, catalog_service.registry)
    assert verified.hash_mismatch is True
    assert verified.needs_review is True
    assert len(verified.mismatched_playbooks) == 1
    assert verified.mismatched_playbooks[0]["pinned_hash"] == unknown_hash


def test_detect_affected_campaigns_event_handler():
    old_hash = "sha256:9a4e2e5f211e30e6e4ed7fbf66f8024d623ac6e0b52d1481c233dff39e00d785"
    new_hash = "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"

    campaigns = list(catalog_service.campaigns.values())
    affected = detect_affected_campaigns(
        playbook_id="MOD1-PB-PERIMETER-001",
        old_hash=old_hash,
        new_hash=new_hash,
        campaigns=campaigns,
    )

    assert len(affected) >= 1
    affected_ids = [c.campaign_id for c in affected]
    assert "CAMP-QS-001" in affected_ids
    assert affected[0].hash_mismatch is True
    assert affected[0].needs_review is True


# =====================================================================
# 3. Multi-Axis Catalog Search Tests
# =====================================================================

@pytest.mark.asyncio
async def test_filter_by_tier():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Quick scan tier
        resp = await client.get("/campaigns", params={"tier": "quick_scan"})
        assert resp.status_code == 200
        items = resp.json()
        assert len(items) >= 1
        assert all(c["tier"] == "quick_scan" for c in items)

        # Boss fight tier
        resp_boss = await client.get("/campaigns", params={"tier": "boss_fight"})
        assert resp_boss.status_code == 200
        boss_items = resp_boss.json()
        assert len(boss_items) >= 2
        assert all(c["tier"] == "boss_fight" for c in boss_items)


@pytest.mark.asyncio
async def test_filter_by_region_with_global_fallback():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Regional query for EU must return EU-specific and Global campaigns
        resp = await client.get("/campaigns", params={"region": "eu"})
        assert resp.status_code == 200
        items = resp.json()
        assert len(items) >= 2
        for item in items:
            assert "eu" in item["regions"] or "global" in item["regions"]


@pytest.mark.asyncio
async def test_filter_by_technique():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # MITRE ATT&CK technique T1566.001 (Spear-phishing attachment)
        resp = await client.get("/campaigns", params={"technique": "T1566.001"})
        assert resp.status_code == 200
        items = resp.json()
        assert len(items) >= 1
        assert any(c["campaign_id"] == "CAMP-APT29-001" for c in items)


@pytest.mark.asyncio
async def test_filter_by_regulation():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # GDPR compliance framework
        resp = await client.get("/campaigns", params={"regulation": "GDPR"})
        assert resp.status_code == 200
        items = resp.json()
        assert len(items) >= 1
        assert any("GDPR" in c["regulations"] for c in items)

        # PCI-DSS compliance framework
        resp_pci = await client.get("/campaigns", params={"regulation": "PCI-DSS"})
        assert resp_pci.status_code == 200
        pci_items = resp_pci.json()
        assert len(pci_items) >= 2
        assert all("PCI-DSS" in c["regulations"] for c in pci_items)


@pytest.mark.asyncio
async def test_combined_multi_axis_filters():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/campaigns",
            params={
                "tier": "boss_fight",
                "region": "na",
                "regulation": "NIST",
            },
        )
        assert resp.status_code == 200
        items = resp.json()
        assert len(items) >= 1
        for c in items:
            assert c["tier"] == "boss_fight"
            assert "na" in c["regions"] or "global" in c["regions"]
            assert "NIST" in c["regulations"]


# =====================================================================
# 4. Boss-Fight Multi-Stage Logic Tests
# =====================================================================

def test_boss_fight_campaign_structure():
    camp = catalog_service.get_campaign("CAMP-APT29-001", check_hashes=True)
    assert camp is not None
    assert camp.tier == "boss_fight"
    assert len(camp.stages) == 4
    # Check sequential stage numbering
    numbers = [s.stage_number for s in camp.stages]
    assert numbers == [1, 2, 3, 4]


def test_difficulty_rating_calculation():
    # APT29 boss fight has 4 stages, multiple techniques, high criticality
    apt29 = catalog_service.get_campaign("CAMP-APT29-001", check_hashes=False)
    diff_apt = apt29.compute_difficulty()
    assert diff_apt.score > 20.0
    assert diff_apt.level in ["Hard", "Boss"]
    assert diff_apt.technique_count >= 4
    assert diff_apt.stage_count == 4

    # Quick scan perimeter has 1 technique, low criticality, 0 stages
    qs = catalog_service.get_campaign("CAMP-QS-001", check_hashes=False)
    diff_qs = qs.compute_difficulty()
    assert diff_qs.score < diff_apt.score
    assert diff_qs.level in ["Easy", "Medium"]


def test_boss_fight_progression_and_composite_score():
    camp = catalog_service.get_campaign("CAMP-APT29-001", check_hashes=False)
    assert camp is not None

    session = BossFightSession(
        session_id="boss-sess-test-001",
        campaign_id=camp.campaign_id,
        tenant_id="tenant-alpha-01",
        total_stages=len(camp.stages),
    )

    # Initial state
    assert session.current_stage_number == 1
    assert session.is_completed is False

    # Attempting to execute Stage 2 out of order must raise ValueError
    with pytest.raises(ValueError, match="Expected current stage 1"):
        session.submit_stage(camp.stages[1], verdict="Detected", score=90.0)

    # Submit Stage 1 successfully
    res1 = session.submit_stage(camp.stages[0], verdict="Detected", score=80.0)
    assert res1.passed is True
    assert session.current_stage_number == 2
    assert session.is_completed is False

    # Submit Stage 2 successfully
    res2 = session.submit_stage(camp.stages[1], verdict="Detected", score=90.0)
    assert res2.passed is True
    assert session.current_stage_number == 3
    assert session.is_completed is False

    # Submit Stage 3 successfully
    res3 = session.submit_stage(camp.stages[2], verdict="Detected", score=85.0)
    assert res3.passed is True
    assert session.current_stage_number == 4
    assert session.is_completed is False

    # Submit Final Stage 4 successfully -> triggers completion
    res4 = session.submit_stage(camp.stages[3], verdict="Detected", score=95.0)
    assert res4.passed is True
    assert session.is_completed is True
    assert session.all_stages_passed is True
    assert session.composite_score == round((80.0 + 90.0 + 85.0 + 95.0) / 4.0, 2)
    assert session.completed_at is not None


def test_boss_fight_fails_when_stage_missed():
    camp = catalog_service.get_campaign("CAMP-APT29-001", check_hashes=False)
    session = BossFightSession(
        session_id="boss-sess-fail-002",
        campaign_id=camp.campaign_id,
        tenant_id="tenant-alpha-02",
        total_stages=len(camp.stages),
    )

    # Stage 1 succeeds
    session.submit_stage(camp.stages[0], verdict="Detected", score=90.0)

    # Stage 2 misses detection
    res2 = session.submit_stage(camp.stages[1], verdict="Missed", score=0.0)
    assert res2.passed is False
    assert session.is_completed is True
    assert session.all_stages_passed is False


# =====================================================================
# 5. Endpoint Contract Tests
# =====================================================================

@pytest.mark.asyncio
async def test_get_campaigns_endpoint():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/campaigns")
        assert resp.status_code == 200
        items = resp.json()
        assert len(items) >= 6

        # Check required schema fields
        for c in items:
            assert "campaign_id" in c
            assert "title" in c
            assert "playbook_hashes" in c
            assert "tier" in c
            assert "credit_cost" in c
            assert "difficulty_rating" in c


@pytest.mark.asyncio
async def test_get_campaign_by_id_endpoint():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/campaigns/CAMP-APT29-001")
        assert resp.status_code == 200
        data = resp.json()
        assert data["campaign_id"] == "CAMP-APT29-001"
        assert data["tier"] == "boss_fight"
        assert len(data["stages"]) == 4
        assert data["hash_mismatch"] is False


@pytest.mark.asyncio
async def test_get_campaign_not_found():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/campaigns/NON-EXISTENT-ID")
        assert resp.status_code == 404


@pytest.mark.asyncio
async def test_create_campaign_endpoint():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        payload = {
            "campaign_id": "CAMP-NEW-001",
            "title": "New Custom Campaign",
            "description": "Custom campaign creation test",
            "playbook_hashes": [
                "sha256:9a4e2e5f211e30e6e4ed7fbf66f8024d623ac6e0b52d1481c233dff39e00d785"
            ],
            "tier": "guided",
            "regions": ["global", "na"],
            "techniques": ["T1190"],
            "regulations": ["NIST"],
            "credit_cost": 25,
            "asset_criticality": "medium",
            "estimated_score_impact": 5.0,
            "stages": [],
        }
        resp = await client.post("/campaigns", json=payload)
        assert resp.status_code == 201
        data = resp.json()
        assert data["campaign_id"] == "CAMP-NEW-001"
        assert data["content_hash"].startswith("sha256:")


@pytest.mark.asyncio
async def test_boss_fight_api_workflow():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Start boss fight
        start_resp = await client.post(
            "/campaigns/CAMP-APT29-001/boss-fight/start",
            json={"tenant_id": "tenant-corp-01"},
        )
        assert start_resp.status_code == 200
        session_data = start_resp.json()
        session_id = session_data["session_id"]
        assert session_data["current_stage_number"] == 1

        # Submit Stage 1
        s1_resp = await client.post(
            f"/campaigns/CAMP-APT29-001/boss-fight/{session_id}/stage",
            json={
                "stage_id": "stage-apt29-1",
                "verdict": "Detected",
                "score": 95.0,
                "evidence_ref": "https://evidence.cybreach.local/apt29/s1",
            },
        )
        assert s1_resp.status_code == 200
        assert s1_resp.json()["current_stage_number"] == 2
        assert s1_resp.json()["is_completed"] is False


# =====================================================================
# 6. Zero Em Dashes Guardrail Check
# =====================================================================

def test_zero_em_dashes_in_code_and_fixtures():
    """Verify strictly no em dashes (Unicode U+2014) in any project files."""
    project_root = Path(__file__).parent.parent
    shivam_targets = [
        project_root / "app" / "campaign",
        project_root / "fixtures" / "campaigns",
        project_root / "fixtures" / "playbooks",
        project_root / "tests" / "test_campaign_library.py",
        project_root / "tests" / "test_scheduler.py",
    ]
    readme_path = project_root / "README.md"
    if readme_path.exists() and "Project_CyBreach" not in str(project_root):
        shivam_targets.append(readme_path)

    checked_files = []
    em_dash_violations = []

    for target in shivam_targets:
        if target.is_file():
            candidates = [target]
        elif target.is_dir():
            candidates = list(target.rglob("*.py")) + list(target.rglob("*.json")) + list(target.rglob("*.md"))
        else:
            continue

        for file_path in candidates:
            if any(part in {".git", ".pytest_cache", ".venv", "__pycache__"} for part in file_path.parts):
                continue
            checked_files.append(file_path)
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()
            if chr(0x2014) in content or chr(0x2013) in content:
                em_dash_violations.append(str(file_path))

    assert len(checked_files) >= 8
    assert len(em_dash_violations) == 0, f"Found em dashes in: {em_dash_violations}"
