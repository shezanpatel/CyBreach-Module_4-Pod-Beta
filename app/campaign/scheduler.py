"""
CyBreach Module 4: Arena - Continuous Validation Scheduler

Recurring continuous validation engine using APScheduler.
Enables scheduled campaign executions (daily, weekly, monthly, interval, or cron)
for Professional and Enterprise continuous assurance.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional
from pydantic import BaseModel, Field

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

logger = logging.getLogger(__name__)


class ValidationSchedule(BaseModel):
    """Configuration model for a recurring validation campaign."""
    schedule_id: str = Field(default_factory=lambda: f"sched-{uuid.uuid4().hex[:12]}")
    tenant_id: str = Field(..., description="Target tenant ID")
    campaign_id: str = Field(..., description="Target campaign ID to execute")
    cadence: str = Field(
        ...,
        description="Schedule cadence: daily, weekly, monthly, interval, or cron",
    )
    cron_expression: Optional[str] = Field(
        default=None,
        description="Cron expression for custom cadences (e.g. '0 2 * * *')",
    )
    interval_seconds: Optional[int] = Field(
        default=None,
        description="Interval duration in seconds for interval cadence",
    )
    hour: int = Field(default=2, ge=0, le=23, description="Execution hour for daily/weekly/monthly (UTC)")
    minute: int = Field(default=0, ge=0, le=59, description="Execution minute (UTC)")
    day_of_week: Optional[str] = Field(
        default="mon",
        description="Day of week for weekly cadence: mon, tue, wed, thu, fri, sat, sun",
    )
    day_of_month: Optional[int] = Field(
        default=1,
        ge=1,
        le=31,
        description="Day of month for monthly cadence",
    )
    is_active: bool = Field(default=True, description="Schedule active state")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_run_at: Optional[datetime] = None
    next_run_at: Optional[datetime] = None
    run_count: int = 0


class ScheduleExecutionRecord(BaseModel):
    """Audit record generated every time a recurring validation triggers."""
    execution_id: str = Field(default_factory=lambda: f"exec-{uuid.uuid4().hex[:12]}")
    schedule_id: str
    tenant_id: str
    campaign_id: str
    triggered_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    status: str = Field(default="SUCCESS", description="Execution status: SUCCESS, FAILED, RUNNING")
    engagement_session_id: Optional[str] = None
    error_message: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ValidationSchedulerEngine:
    """
    Continuous Validation Engine utilizing APScheduler.
    Manages automated recurring campaign validation runs.
    """

    def __init__(
        self,
        execution_handler: Optional[Callable[[ValidationSchedule], ScheduleExecutionRecord]] = None,
        auto_start: bool = False,
    ):
        self._scheduler = BackgroundScheduler(timezone="UTC")
        self._schedules: Dict[str, ValidationSchedule] = {}
        self._history: List[ScheduleExecutionRecord] = []
        self._execution_handler = execution_handler or self._default_execution_handler

        if auto_start:
            self.start()

    def start(self) -> None:
        """Start the background scheduler if not already running."""
        if not self._scheduler.running:
            self._scheduler.start()
            logger.info("ValidationSchedulerEngine started successfully")

    def shutdown(self, wait: bool = False) -> None:
        """Shutdown the scheduler cleanly."""
        if self._scheduler.running:
            self._scheduler.shutdown(wait=wait)
            logger.info("ValidationSchedulerEngine shutdown")

    def is_running(self) -> bool:
        """Return True if background scheduler is active."""
        return self._scheduler.running

    def _default_execution_handler(self, schedule: ValidationSchedule) -> ScheduleExecutionRecord:
        """Default stub handler simulating campaign execution session creation."""
        session_id = f"eng-cont-{uuid.uuid4().hex[:8]}"
        return ScheduleExecutionRecord(
            schedule_id=schedule.schedule_id,
            tenant_id=schedule.tenant_id,
            campaign_id=schedule.campaign_id,
            triggered_at=datetime.now(timezone.utc),
            status="SUCCESS",
            engagement_session_id=session_id,
            metadata={"trigger_source": "apscheduler_continuous_engine"},
        )

    def _job_wrapper(self, schedule_id: str) -> None:
        """Internal callback invoked when an APScheduler job fires."""
        schedule = self._schedules.get(schedule_id)
        if not schedule or not schedule.is_active:
            return

        now = datetime.now(timezone.utc)
        schedule.last_run_at = now
        schedule.run_count += 1

        try:
            record = self._execution_handler(schedule)
        except Exception as exc:
            logger.exception("Error executing schedule %s: %s", schedule_id, exc)
            record = ScheduleExecutionRecord(
                schedule_id=schedule_id,
                tenant_id=schedule.tenant_id,
                campaign_id=schedule.campaign_id,
                triggered_at=now,
                status="FAILED",
                error_message=str(exc),
            )

        self._history.append(record)

        # Update next_run_at from APScheduler job
        job = self._scheduler.get_job(schedule_id)
        if job:
            next_time = getattr(job, "next_run_time", None)
            if next_time:
                schedule.next_run_at = next_time

    def _build_trigger(self, schedule: ValidationSchedule):
        """Construct an APScheduler trigger based on schedule parameters."""
        cadence = schedule.cadence.lower()

        if cadence == "daily":
            return CronTrigger(
                hour=schedule.hour,
                minute=schedule.minute,
                timezone="UTC",
            )
        elif cadence == "weekly":
            day = schedule.day_of_week or "mon"
            return CronTrigger(
                day_of_week=day.lower(),
                hour=schedule.hour,
                minute=schedule.minute,
                timezone="UTC",
            )
        elif cadence == "monthly":
            day = schedule.day_of_month or 1
            return CronTrigger(
                day=day,
                hour=schedule.hour,
                minute=schedule.minute,
                timezone="UTC",
            )
        elif cadence == "interval":
            secs = schedule.interval_seconds or 3600
            return IntervalTrigger(seconds=secs, timezone="UTC")
        elif cadence == "cron":
            if not schedule.cron_expression:
                raise ValueError("cron_expression is required for 'cron' cadence")
            parts = schedule.cron_expression.strip().split()
            if len(parts) == 5:
                minute, hour, dom, month, dow = parts
                return CronTrigger(
                    minute=minute,
                    hour=hour,
                    day=dom,
                    month=month,
                    day_of_week=dow,
                    timezone="UTC",
                )
            raise ValueError(f"Invalid cron expression: '{schedule.cron_expression}'. Expected 5 fields.")
        else:
            raise ValueError(f"Unsupported cadence '{schedule.cadence}'. Expected daily, weekly, monthly, interval, or cron.")

    def add_schedule(self, schedule: ValidationSchedule) -> ValidationSchedule:
        """Register a new recurring validation schedule."""
        if schedule.schedule_id in self._schedules:
            raise ValueError(f"Schedule with ID '{schedule.schedule_id}' already exists")

        trigger = self._build_trigger(schedule)
        job = self._scheduler.add_job(
            func=self._job_wrapper,
            trigger=trigger,
            id=schedule.schedule_id,
            args=[schedule.schedule_id],
            replace_existing=True,
        )

        next_time = getattr(job, "next_run_time", None)
        if next_time:
            schedule.next_run_at = next_time

        self._schedules[schedule.schedule_id] = schedule
        logger.info(
            "Registered validation schedule %s for campaign %s (cadence: %s)",
            schedule.schedule_id,
            schedule.campaign_id,
            schedule.cadence,
        )
        return schedule

    def get_schedule(self, schedule_id: str) -> Optional[ValidationSchedule]:
        """Look up schedule by ID."""
        return self._schedules.get(schedule_id)

    def list_schedules(self, tenant_id: Optional[str] = None) -> List[ValidationSchedule]:
        """List registered schedules, optionally filtered by tenant."""
        all_s = list(self._schedules.values())
        if tenant_id:
            return [s for s in all_s if s.tenant_id == tenant_id]
        return all_s

    def pause_schedule(self, schedule_id: str) -> ValidationSchedule:
        """Pause a recurring validation schedule."""
        schedule = self._schedules.get(schedule_id)
        if not schedule:
            raise KeyError(f"Schedule '{schedule_id}' not found")
        self._scheduler.pause_job(schedule_id)
        schedule.is_active = False
        return schedule

    def resume_schedule(self, schedule_id: str) -> ValidationSchedule:
        """Resume a paused validation schedule."""
        schedule = self._schedules.get(schedule_id)
        if not schedule:
            raise KeyError(f"Schedule '{schedule_id}' not found")
        self._scheduler.resume_job(schedule_id)
        schedule.is_active = True
        job = self._scheduler.get_job(schedule_id)
        if job:
            next_time = getattr(job, "next_run_time", None)
            if next_time:
                schedule.next_run_at = next_time
        return schedule

    def remove_schedule(self, schedule_id: str) -> bool:
        """Remove and unregister a schedule."""
        if schedule_id not in self._schedules:
            return False
        if self._scheduler.get_job(schedule_id):
            self._scheduler.remove_job(schedule_id)
        del self._schedules[schedule_id]
        return True

    def trigger_now(self, schedule_id: str) -> ScheduleExecutionRecord:
        """Manually trigger immediate execution of a scheduled campaign."""
        schedule = self._schedules.get(schedule_id)
        if not schedule:
            raise KeyError(f"Schedule '{schedule_id}' not found")

        self._job_wrapper(schedule_id)
        return self._history[-1]

    def get_execution_history(
        self,
        schedule_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
    ) -> List[ScheduleExecutionRecord]:
        """Retrieve audit history of triggered executions."""
        records = list(self._history)
        if schedule_id:
            records = [r for r in records if r.schedule_id == schedule_id]
        if tenant_id:
            records = [r for r in records if r.tenant_id == tenant_id]
        return records
