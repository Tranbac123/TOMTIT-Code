"""Append-only in-memory event log for the fake runtime."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from tomtit.domain.enums import EvaluationEventType
from tomtit.domain.evaluation import EvaluationEvent


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class InMemoryEventLog:
    """Stores immutable serialized events and exposes detached snapshots only."""

    def __init__(
        self,
        *,
        event_id_factory: Callable[[], str] = lambda: str(uuid4()),
    ) -> None:
        self._event_id_factory = event_id_factory
        self._serialized_events: list[str] = []

    def append(
        self,
        *,
        timestamp: datetime,
        event_type: EvaluationEventType,
        task_id: str,
        run_id: str,
        attempt_number: int | None,
        actor: str,
        payload: Mapping[str, Any],
    ) -> EvaluationEvent:
        event = EvaluationEvent(
            event_id=self._event_id_factory(),
            schema_version="evaluation-event.v1",
            timestamp=timestamp,
            event_type=event_type,
            task_id=task_id,
            run_id=run_id,
            attempt_number=attempt_number,
            actor=actor,
            payload=dict(payload),
        )
        serialized = event.model_dump_json()
        self._serialized_events.append(serialized)
        return EvaluationEvent.model_validate_json(serialized)

    @property
    def events(self) -> tuple[EvaluationEvent, ...]:
        return tuple(
            EvaluationEvent.model_validate_json(serialized)
            for serialized in self._serialized_events
        )

    def __len__(self) -> int:
        return len(self._serialized_events)
