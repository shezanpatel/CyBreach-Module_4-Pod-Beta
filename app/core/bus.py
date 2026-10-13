import uuid
from datetime import datetime, timezone
from typing import Dict, Any, List


class EventBus:
    def __init__(self):
        self.published_events: List[Dict[str, Any]] = []

    async def publish(self, topic: str, payload: Dict[str, Any], correlation_id: str = None) -> Dict[str, Any]:
        event_message = {
            "topic": topic,
            "correlation_id": correlation_id or str(uuid.uuid4()),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "payload": payload,
        }
        self.published_events.append(event_message)
        return event_message

    def get_events_by_topic(self, topic: str) -> List[Dict[str, Any]]:
        return [e for e in self.published_events if e["topic"] == topic]

    def clear(self):
        self.published_events.clear()


bus = EventBus()