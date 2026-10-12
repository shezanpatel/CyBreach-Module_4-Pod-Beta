"""Event bus abstraction for inter-service communication.

Module 4: Arena - Pod Beta.
Follows strict zero em dash rule: only hyphens (-) or colons (:).

Topics:
- Published: engagement.lifecycle and campaign.updated
- Consumed: mod2.outcome and mod1.playbook
Every event includes a unique correlation key for end-to-end tracing.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional
from pydantic import BaseModel, Field


class BusMessage(BaseModel):
    """Standard event message envelope."""
    topic: str
    event_id: str
    correlation_key: str
    event_type: str
    payload: Dict[str, Any]
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class EventBus:
    """In-memory event bus managing message pub/sub across topics."""

    TOPIC_ENGAGEMENT_LIFECYCLE = "engagement.lifecycle"
    TOPIC_CAMPAIGN_UPDATED = "campaign.updated"
    TOPIC_MOD2_OUTCOME = "mod2.outcome"
    TOPIC_MOD1_PLAYBOOK = "mod1.playbook"

    def __init__(self):
        self._published_events: List[BusMessage] = []
        self._subscribers: Dict[str, List[Callable[[BusMessage], Any]]] = {
            self.TOPIC_ENGAGEMENT_LIFECYCLE: [],
            self.TOPIC_CAMPAIGN_UPDATED: [],
            self.TOPIC_MOD2_OUTCOME: [],
            self.TOPIC_MOD1_PLAYBOOK: [],
        }

    @staticmethod
    def generate_correlation_key(prefix: str = "mod4") -> str:
        """Generate a traceable correlation key."""
        return f"{prefix}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:8]}"

    async def publish(
        self,
        topic: str,
        event_type: str,
        payload: Dict[str, Any],
        correlation_key: Optional[str] = None,
    ) -> BusMessage:
        """Publish an event to a specified topic.

        Enforces inclusion of a correlation key for tracing.
        """
        corr_key = correlation_key or self.generate_correlation_key()
        event_id = f"evt-{uuid.uuid4().hex[:12]}"

        message = BusMessage(
            topic=topic,
            event_id=event_id,
            correlation_key=corr_key,
            event_type=event_type,
            payload=payload,
        )

        self._published_events.append(message)

        # Notify any subscribed listeners
        for handler in self._subscribers.get(topic, []):
            try:
                res = handler(message)
                if hasattr(res, "__await__"):
                    await res
            except Exception:
                # Retain decoupling
                pass

        return message

    async def publish_engagement_lifecycle(
        self,
        event_type: str,
        engagement_id: str,
        tenant_id: str,
        state: str,
        engagement_type: str,
        details: Optional[Dict[str, Any]] = None,
        correlation_key: Optional[str] = None,
    ) -> BusMessage:
        """Convenience method to emit an engagement.lifecycle event."""
        payload = {
            "engagement_id": engagement_id,
            "tenant_id": tenant_id,
            "state": state,
            "engagement_type": engagement_type,
            "details": details or {},
        }
        return await self.publish(
            topic=self.TOPIC_ENGAGEMENT_LIFECYCLE,
            event_type=event_type,
            payload=payload,
            correlation_key=correlation_key,
        )

    def subscribe(self, topic: str, handler: Callable[[BusMessage], Any]) -> None:
        """Register a subscriber callback for a topic."""
        if topic not in self._subscribers:
            self._subscribers[topic] = []
        self._subscribers[topic].append(handler)

    def get_published_events(self, topic: Optional[str] = None) -> List[BusMessage]:
        """Retrieve recorded published events, optionally filtered by topic."""
        if topic:
            return [e for e in self._published_events if e.topic == topic]
        return list(self._published_events)

    def clear(self) -> None:
        """Clear published event history."""
        self._published_events.clear()
