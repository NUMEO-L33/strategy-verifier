"""Diagnostics-only capture session continuity helpers."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
import re

from strategy_verifier.domain.errors import DomainValidationError
from strategy_verifier.public_market_data.public_trade_stream_contract import (
    PublicTradeStreamEvent,
)


INPUT_SESSION_MODES = ("file", "prefix", "single")
SINGLE_SESSION_GROUP_ID = "single-session"
_CHUNK_SUFFIX_PATTERN = re.compile(r"^(?P<prefix>.+)-\d+-\d+\.jsonl$")


@dataclass(frozen=True, slots=True)
class CaptureSessionGroupSummary:
    session_group_id: str
    input_count: int
    valid_event_count: int
    first_input_path: Path
    last_input_path: Path

    def __post_init__(self) -> None:
        if not isinstance(self.session_group_id, str) or not self.session_group_id:
            raise DomainValidationError("session_group_id must be a non-empty string")
        _require_positive_int(self.input_count, "input_count")
        _require_non_negative_int(self.valid_event_count, "valid_event_count")
        if not isinstance(self.first_input_path, Path):
            raise DomainValidationError("first_input_path must be a Path")
        if not isinstance(self.last_input_path, Path):
            raise DomainValidationError("last_input_path must be a Path")


@dataclass(frozen=True, slots=True)
class CaptureSessionEventLocation:
    input_index: int
    input_path: Path
    session_group_id: str
    events: list[PublicTradeStreamEvent]
    session_events: list[PublicTradeStreamEvent]
    local_event_index: int
    session_event_index: int
    combined_event_index: int
    combined_future_event_count: int

    def __post_init__(self) -> None:
        _require_non_negative_int(self.input_index, "input_index")
        if not isinstance(self.input_path, Path):
            raise DomainValidationError("input_path must be a Path")
        if not isinstance(self.session_group_id, str) or not self.session_group_id:
            raise DomainValidationError("session_group_id must be a non-empty string")
        if not isinstance(self.events, list) or not all(
            isinstance(event, PublicTradeStreamEvent) for event in self.events
        ):
            raise DomainValidationError("events must contain PublicTradeStreamEvent")
        if not isinstance(self.session_events, list) or not all(
            isinstance(event, PublicTradeStreamEvent) for event in self.session_events
        ):
            raise DomainValidationError(
                "session_events must contain PublicTradeStreamEvent"
            )
        _require_non_negative_int(self.local_event_index, "local_event_index")
        _require_non_negative_int(self.session_event_index, "session_event_index")
        _require_non_negative_int(self.combined_event_index, "combined_event_index")
        _require_non_negative_int(
            self.combined_future_event_count,
            "combined_future_event_count",
        )
        object.__setattr__(self, "events", list(self.events))
        object.__setattr__(self, "session_events", list(self.session_events))


@dataclass(frozen=True, slots=True)
class CaptureSessionContext:
    input_session_mode: str
    session_group_summaries: list[CaptureSessionGroupSummary]
    locations_by_timeline_index: dict[int, CaptureSessionEventLocation]

    def __post_init__(self) -> None:
        validate_input_session_mode(self.input_session_mode)
        if not isinstance(self.session_group_summaries, list) or not all(
            isinstance(summary, CaptureSessionGroupSummary)
            for summary in self.session_group_summaries
        ):
            raise DomainValidationError(
                "session_group_summaries must contain CaptureSessionGroupSummary"
            )
        if not isinstance(self.locations_by_timeline_index, dict) or not all(
            isinstance(key, int) and isinstance(value, CaptureSessionEventLocation)
            for key, value in self.locations_by_timeline_index.items()
        ):
            raise DomainValidationError(
                "locations_by_timeline_index must map int to CaptureSessionEventLocation"
            )
        object.__setattr__(
            self,
            "session_group_summaries",
            list(self.session_group_summaries),
        )
        object.__setattr__(
            self,
            "locations_by_timeline_index",
            dict(self.locations_by_timeline_index),
        )


def validate_input_session_mode(input_session_mode: str) -> None:
    if input_session_mode not in INPUT_SESSION_MODES:
        raise DomainValidationError(
            "input_session_mode must be file, prefix, or single"
        )


def capture_session_group_id(input_path: Path, input_session_mode: str) -> str:
    validate_input_session_mode(input_session_mode)
    if not isinstance(input_path, Path):
        raise DomainValidationError("input_path must be a Path")
    if input_session_mode == "single":
        return SINGLE_SESSION_GROUP_ID
    if input_session_mode == "file":
        return input_path.name
    match = _CHUNK_SUFFIX_PATTERN.match(input_path.name)
    if match:
        return match.group("prefix")
    return input_path.stem


def allowed_future_events(
    location: CaptureSessionEventLocation | None,
    horizon_events: int,
) -> list[PublicTradeStreamEvent]:
    _require_positive_int(horizon_events, "horizon_events")
    if location is None:
        return []
    return location.session_events[
        location.session_event_index + 1 :
        location.session_event_index + 1 + horizon_events
    ]


def future_crosses_file_boundary(
    location: CaptureSessionEventLocation | None,
    horizon_events: int,
) -> bool:
    _require_positive_int(horizon_events, "horizon_events")
    if location is None:
        return False
    same_file_future_count = max(0, len(location.events) - location.local_event_index - 1)
    return (
        same_file_future_count < horizon_events
        and location.combined_future_event_count >= horizon_events
    )


def future_crosses_session_boundary(
    location: CaptureSessionEventLocation | None,
    horizon_events: int,
) -> bool:
    _require_positive_int(horizon_events, "horizon_events")
    if location is None:
        return False
    same_session_future_count = max(
        0,
        len(location.session_events) - location.session_event_index - 1,
    )
    return (
        same_session_future_count < horizon_events
        and location.combined_future_event_count >= horizon_events
    )


def long_excursions(
    *,
    entry_price: Decimal,
    future_events: list[PublicTradeStreamEvent],
    threshold_delta: Decimal,
) -> dict[str, Decimal | int | None]:
    _require_positive_decimal(entry_price, "entry_price")
    _require_positive_decimal(threshold_delta, "threshold_delta")
    max_favorable = Decimal("0")
    max_adverse = Decimal("0")
    first_favorable_sequence: int | None = None
    first_adverse_sequence: int | None = None
    for event in future_events:
        if event.mapped_trade_tick is None:
            continue
        current_price = event.mapped_trade_tick.price
        favorable_movement = max(current_price - entry_price, Decimal("0"))
        adverse_movement = max(entry_price - current_price, Decimal("0"))
        max_favorable = max(max_favorable, favorable_movement)
        max_adverse = max(max_adverse, adverse_movement)
        if (
            first_favorable_sequence is None
            and favorable_movement >= threshold_delta
        ):
            first_favorable_sequence = event.sequence
        if first_adverse_sequence is None and adverse_movement >= threshold_delta:
            first_adverse_sequence = event.sequence
    return {
        "max_favorable_movement": max_favorable,
        "max_adverse_movement": max_adverse,
        "first_favorable_touch_sequence": first_favorable_sequence,
        "first_adverse_touch_sequence": first_adverse_sequence,
    }


def _session_events_by_group(event_refs) -> dict[str, list[PublicTradeStreamEvent]]:
    session_events: dict[str, list[PublicTradeStreamEvent]] = {}
    for ref in event_refs:
        session_events.setdefault(ref["session_group_id"], []).append(ref["event"])
    return session_events


def _require_positive_int(value: int, field_name: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise DomainValidationError(f"{field_name} must be a positive integer")


def _require_non_negative_int(value: int, field_name: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise DomainValidationError(f"{field_name} must be a non-negative integer")


def _require_positive_decimal(value: Decimal, field_name: str) -> None:
    if not isinstance(value, Decimal):
        raise DomainValidationError(f"{field_name} must be a Decimal")
    if value <= 0:
        raise DomainValidationError(f"{field_name} must be greater than zero")
