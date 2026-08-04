"""Append-only in-memory event log for the fake runtime."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from tomtit.domain.enums import EvaluationEventType
from tomtit.domain.evaluation import EvaluationEvent


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class PreparedEvent:
    """A validated and already serialized event that is not yet in the journal."""

    event: EvaluationEvent
    serialized: str


class InMemoryEventLog:
    """Stores immutable serialized events and exposes detached snapshots only."""

    def __init__(
        self,
        *,
        event_id_factory: Callable[[], str] = lambda: str(uuid4()),
    ) -> None:
        self._event_id_factory = event_id_factory
        self._serialized_events: list[str] = []

    def prepare(
        self,
        *,
        timestamp: datetime,
        event_type: EvaluationEventType,
        task_id: str,
        run_id: str,
        attempt_number: int | None,
        actor: str,
        payload: Mapping[str, Any],
    ) -> PreparedEvent:
        """Allocate the ID, validate, and serialize an event without touching history.

        Terminal completion prepares its events through this method so every
        fallible construction step happens before canonical state changes.
        """

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
        return PreparedEvent(event=event, serialized=event.model_dump_json())

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
        prepared = self.prepare(
            timestamp=timestamp,
            event_type=event_type,
            task_id=task_id,
            run_id=run_id,
            attempt_number=attempt_number,
            actor=actor,
            payload=payload,
        )
        self._serialized_events.append(prepared.serialized)
        return EvaluationEvent.model_validate_json(prepared.serialized)

    def _append_prepared_batch(self, prepared: Sequence[PreparedEvent]) -> None:
        """Append already prepared events as one unit.

        Internal support for terminal completion. This performs no ID allocation,
        validation, or serialization, so the batch either lands whole or not at
        all; it can never leave a partial terminal pair.
        """

        serialized = [item.serialized for item in prepared]
        self._serialized_events.extend(serialized)

    def _checkpoint(self) -> int:
        """Record how much history exists, for terminal-completion recovery only."""

        return len(self._serialized_events)

    def _restore_to_checkpoint(self, checkpoint: int) -> None:
        """Drop only entries appended after `checkpoint`; earlier history is untouched."""

        del self._serialized_events[checkpoint:]

    @property
    def events(self) -> tuple[EvaluationEvent, ...]:
        return tuple(
            EvaluationEvent.model_validate_json(serialized)
            for serialized in self._serialized_events
        )

    def __len__(self) -> int:
        return len(self._serialized_events)
