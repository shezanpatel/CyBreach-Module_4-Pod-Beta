"""
CyBreach Module 4: Arena - Continuous Validation Scheduler Tests

Tests covering:
1. APScheduler engine startup and shutdown.
2. Scheduling continuous validation runs (daily, weekly, monthly, interval, cron).
3. Schedule management: pause, resume, list, delete.
4. Immediate manual triggers and execution record logging.
5. API endpoints for continuous validation scheduling.
6. Strict adherence to code rules: Zero em dashes.
"""
import time
import pytest
from httpx import ASGITransport, AsyncClient

from app.campaign.scheduler import (
    ValidationSchedulerEngine,
    ValidationSchedule,
    ScheduleExecutionRecord,
)
from app.campaign.api import app, scheduler_engine


@pytest.fixture
def engine():
    """Create isolated scheduler engine instance for testing."""
    eng = ValidationSchedulerEngine(auto_start=True)
    yield eng
    eng.shutdown(wait=False)


# =====================================================================
# 1. Scheduler Engine Lifecycle and Registration Tests
# =====================================================================

def test_scheduler_engine_start_and_shutdown():
    eng = ValidationSchedulerEngine(auto_start=False)
    assert eng.is_running() is False
    eng.start()
    assert eng.is_running() is True
    eng.shutdown(wait=False)
    assert eng.is_running() is False


def test_add_daily_schedule(engine):
    schedule = ValidationSchedule(
        schedule_id="sched-daily-001",
        tenant_id="tenant-acme-corp",
        campaign_id="CAMP-CONT-001",
        cadence="daily",
        hour=3,
        minute=30,
    )
    added = engine.add_schedule(schedule)
    assert added.schedule_id == "sched-daily-001"
    assert added.next_run_at is not None

    retrieved = engine.get_schedule("sched-daily-001")
    assert retrieved is not None
    assert retrieved.cadence == "daily"


def test_add_weekly_schedule(engine):
    schedule = ValidationSchedule(
        schedule_id="sched-weekly-001",
        tenant_id="tenant-acme-corp",
        campaign_id="CAMP-CONT-001",
        cadence="weekly",
        day_of_week="wed",
        hour=4,
        minute=0,
    )
    added = engine.add_schedule(schedule)
    assert added.schedule_id == "sched-weekly-001"
    assert added.next_run_at is not None


def test_add_monthly_schedule(engine):
    schedule = ValidationSchedule(
        schedule_id="sched-monthly-001",
        tenant_id="tenant-acme-corp",
        campaign_id="CAMP-CONT-001",
        cadence="monthly",
        day_of_month=15,
        hour=1,
        minute=0,
    )
    added = engine.add_schedule(schedule)
    assert added.schedule_id == "sched-monthly-001"
    assert added.next_run_at is not None


def test_add_interval_schedule(engine):
    schedule = ValidationSchedule(
        schedule_id="sched-interval-001",
        tenant_id="tenant-acme-corp",
        campaign_id="CAMP-CONT-001",
        cadence="interval",
        interval_seconds=300,
    )
    added = engine.add_schedule(schedule)
    assert added.schedule_id == "sched-interval-001"
    assert added.next_run_at is not None


def test_add_cron_schedule(engine):
    schedule = ValidationSchedule(
        schedule_id="sched-cron-001",
        tenant_id="tenant-acme-corp",
        campaign_id="CAMP-CONT-001",
        cadence="cron",
        cron_expression="0 5 * * *",
    )
    added = engine.add_schedule(schedule)
    assert added.schedule_id == "sched-cron-001"
    assert added.next_run_at is not None


def test_invalid_cadence_rejection(engine):
    schedule = ValidationSchedule(
        schedule_id="sched-invalid-001",
        tenant_id="tenant-acme-corp",
        campaign_id="CAMP-CONT-001",
        cadence="hourly_unsupported",
    )
    with pytest.raises(ValueError, match="Unsupported cadence"):
        engine.add_schedule(schedule)


def test_invalid_cron_expression_rejection(engine):
    schedule = ValidationSchedule(
        schedule_id="sched-bad-cron-001",
        tenant_id="tenant-acme-corp",
        campaign_id="CAMP-CONT-001",
        cadence="cron",
        cron_expression="invalid cron string",
    )
    with pytest.raises(ValueError, match="Invalid cron expression"):
        engine.add_schedule(schedule)


# =====================================================================
# 2. Schedule Management: Pause, Resume, Remove, List
# =====================================================================

def test_pause_and_resume_schedule(engine):
    schedule = ValidationSchedule(
        schedule_id="sched-pr-001",
        tenant_id="tenant-test",
        campaign_id="CAMP-CONT-001",
        cadence="daily",
    )
    engine.add_schedule(schedule)

    # Pause
    paused = engine.pause_schedule("sched-pr-001")
    assert paused.is_active is False

    # Resume
    resumed = engine.resume_schedule("sched-pr-001")
    assert resumed.is_active is True
    assert resumed.next_run_at is not None


def test_list_and_filter_schedules(engine):
    s1 = ValidationSchedule(
        schedule_id="sched-t1-001",
        tenant_id="tenant-1",
        campaign_id="CAMP-CONT-001",
        cadence="daily",
    )
    s2 = ValidationSchedule(
        schedule_id="sched-t2-001",
        tenant_id="tenant-2",
        campaign_id="CAMP-CONT-001",
        cadence="daily",
    )
    engine.add_schedule(s1)
    engine.add_schedule(s2)

    # All schedules
    all_scheds = engine.list_schedules()
    assert len(all_scheds) == 2

    # Tenant-filtered
    t1_scheds = engine.list_schedules(tenant_id="tenant-1")
    assert len(t1_scheds) == 1
    assert t1_scheds[0].schedule_id == "sched-t1-001"


def test_remove_schedule(engine):
    schedule = ValidationSchedule(
        schedule_id="sched-del-001",
        tenant_id="tenant-test",
        campaign_id="CAMP-CONT-001",
        cadence="daily",
    )
    engine.add_schedule(schedule)
    assert engine.get_schedule("sched-del-001") is not None

    removed = engine.remove_schedule("sched-del-001")
    assert removed is True
    assert engine.get_schedule("sched-del-001") is None


# =====================================================================
# 3. Execution Triggering and Audit Records
# =====================================================================

def test_manual_trigger_now(engine):
    schedule = ValidationSchedule(
        schedule_id="sched-trig-001",
        tenant_id="tenant-corp",
        campaign_id="CAMP-CONT-001",
        cadence="daily",
    )
    engine.add_schedule(schedule)

    record = engine.trigger_now("sched-trig-001")
    assert record.schedule_id == "sched-trig-001"
    assert record.status == "SUCCESS"
    assert record.engagement_session_id is not None

    # Check that run_count incremented and last_run_at updated
    updated_sched = engine.get_schedule("sched-trig-001")
    assert updated_sched.run_count == 1
    assert updated_sched.last_run_at is not None

    # Verify execution history
    history = engine.get_execution_history(schedule_id="sched-trig-001")
    assert len(history) == 1
    assert history[0].execution_id == record.execution_id


def test_execution_failure_handling(engine):
    def failing_handler(sched):
        raise RuntimeError("Simulated execution failure in playbook launch")

    failing_engine = ValidationSchedulerEngine(
        execution_handler=failing_handler,
        auto_start=True,
    )
    try:
        schedule = ValidationSchedule(
            schedule_id="sched-fail-001",
            tenant_id="tenant-fail",
            campaign_id="CAMP-CONT-001",
            cadence="daily",
        )
        failing_engine.add_schedule(schedule)

        record = failing_engine.trigger_now("sched-fail-001")
        assert record.status == "FAILED"
        assert "Simulated execution failure" in (record.error_message or "")
    finally:
        failing_engine.shutdown(wait=False)


# =====================================================================
# 4. Scheduler API Endpoint Tests
# =====================================================================

@pytest.mark.asyncio
async def test_scheduler_api_workflow():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Create schedule
        create_payload = {
            "tenant_id": "tenant-api-test",
            "campaign_id": "CAMP-CONT-001",
            "cadence": "daily",
            "hour": 2,
            "minute": 0,
        }
        resp = await client.post("/campaigns/schedule", json=create_payload)
        assert resp.status_code == 201
        sched_data = resp.json()
        sched_id = sched_data["schedule_id"]
        assert sched_data["tenant_id"] == "tenant-api-test"

        # 2. List schedules
        list_resp = await client.get("/campaigns/schedules", params={"tenant_id": "tenant-api-test"})
        assert list_resp.status_code == 200
        assert len(list_resp.json()) >= 1

        # 3. Pause schedule
        pause_resp = await client.post(f"/campaigns/schedules/{sched_id}/pause")
        assert pause_resp.status_code == 200
        assert pause_resp.json()["is_active"] is False

        # 4. Resume schedule
        resume_resp = await client.post(f"/campaigns/schedules/{sched_id}/resume")
        assert resume_resp.status_code == 200
        assert resume_resp.json()["is_active"] is True

        # 5. Trigger execution now
        trig_resp = await client.post(f"/campaigns/schedules/{sched_id}/trigger")
        assert trig_resp.status_code == 200
        assert trig_resp.json()["status"] == "SUCCESS"

        # 6. Delete schedule
        del_resp = await client.delete(f"/campaigns/schedules/{sched_id}")
        assert del_resp.status_code == 200
        assert del_resp.json()["status"] == "deleted"
