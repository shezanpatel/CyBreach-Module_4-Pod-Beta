import json
import pytest
from jsonschema import validate
from app.core.bus import bus
from app.core.database import Base, engine
from app.core.db_models import EngagementSessionModel, CampaignDefinitionModel


def test_database_tables_create_cleanly():
    Base.metadata.create_all(bind=engine)
    assert EngagementSessionModel.__tablename__ == "engagement_session"
    assert CampaignDefinitionModel.__tablename__ == "campaign_definition"


@pytest.mark.asyncio
async def test_event_bus_lifecycle_emission():
    bus.clear()
    payload = {
        "engagement_id": "ENG-2026-001",
        "tenant_id": "tenant-alpha",
        "state": "executing",
        "engagement_type": "quick_scan"
    }
    
    event = await bus.publish(
        topic="engagement.lifecycle",
        payload=payload,
        correlation_id="corr-test-123"
    )
    
    assert event["topic"] == "engagement.lifecycle"
    assert event["correlation_id"] == "corr-test-123"
    assert len(bus.get_events_by_topic("engagement.lifecycle")) == 1


def test_campaign_fixtures_match_published_contract():
    with open("contracts/campaign_schema.json", "r") as f:
        schema = json.load(f)

    with open("fixtures/campaigns/quick_scan_perimeter.json", "r") as f:
        fixture_data = json.load(f)

    validate(instance=fixture_data, schema=schema)