"""Local JSONL reader for captured public trade events."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from strategy_verifier.domain import TradeSide, TradeTick
from strategy_verifier.domain.errors import DomainValidationError
from strategy_verifier.public_market_data.public_trade_stream_contract import (
    PublicTradeStreamEvent,
    build_public_trade_stream_event,
)


@dataclass(frozen=True, slots=True)
class PublicTradeCaptureReadConfig:
    input_path: Path
    skip_invalid_lines: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.input_path, Path):
            raise DomainValidationError("input_path must be a Path")
        _validate_local_jsonl_path(self.input_path)
        if not isinstance(self.skip_invalid_lines, bool):
            raise DomainValidationError("skip_invalid_lines must be a bool")


@dataclass(frozen=True, slots=True)
class PublicTradeCaptureReadResult:
    input_path: Path
    stream_events: list[PublicTradeStreamEvent]
    total_jsonl_lines: int
    valid_event_count: int
    invalid_line_count: int
    error_messages: list[str]
    reasons: list[str]
    block_reasons: list[str]

    def __post_init__(self) -> None:
        if not isinstance(self.input_path, Path):
            raise DomainValidationError("input_path must be a Path")
        if not isinstance(self.stream_events, list) or not all(
            isinstance(event, PublicTradeStreamEvent) for event in self.stream_events
        ):
            raise DomainValidationError(
                "stream_events must be a list of PublicTradeStreamEvent"
            )
        _require_non_negative_int(self.total_jsonl_lines, "total_jsonl_lines")
        _require_non_negative_int(self.valid_event_count, "valid_event_count")
        _require_non_negative_int(self.invalid_line_count, "invalid_line_count")
        if self.valid_event_count != len(self.stream_events):
            raise DomainValidationError("valid_event_count must match stream_events")
        if self.total_jsonl_lines != self.valid_event_count + self.invalid_line_count:
            raise DomainValidationError("JSONL line counts are inconsistent")
        _require_string_list(self.error_messages, "error_messages")
        _require_string_list(self.reasons, "reasons")
        _require_string_list(self.block_reasons, "block_reasons")
        object.__setattr__(self, "stream_events", list(self.stream_events))
        object.__setattr__(self, "error_messages", list(self.error_messages))
        object.__setattr__(self, "reasons", list(self.reasons))
        object.__setattr__(self, "block_reasons", list(self.block_reasons))


@dataclass(slots=True)
class PublicTradeCaptureStreamCounters:
    """Mutable line counters populated while streaming a capture file."""

    total_jsonl_lines: int = 0
    valid_event_count: int = 0
    invalid_line_count: int = 0


def iter_public_trade_capture_events(
    config: PublicTradeCaptureReadConfig,
    counters: PublicTradeCaptureStreamCounters,
):
    """Yield capture events one line at a time with bounded memory.

    Parses lines with exactly the same rules as
    read_public_trade_capture_jsonl and updates the provided counters so a
    streaming consumer can report identical line accounting.
    """
    if not isinstance(config, PublicTradeCaptureReadConfig):
        raise DomainValidationError("config must be PublicTradeCaptureReadConfig")
    if not isinstance(counters, PublicTradeCaptureStreamCounters):
        raise DomainValidationError(
            "counters must be PublicTradeCaptureStreamCounters"
        )
    if not config.input_path.exists():
        raise DomainValidationError(f"capture input does not exist: {config.input_path}")
    if not config.input_path.is_file():
        raise DomainValidationError(f"capture input is not a file: {config.input_path}")

    with config.input_path.open("r", encoding="utf-8") as file_obj:
        for line_number, raw_line in enumerate(file_obj, start=1):
            line = raw_line.strip()
            if not line:
                continue
            counters.total_jsonl_lines += 1
            try:
                record = json.loads(
                    line,
                    parse_float=Decimal,
                    parse_int=int,
                )
                event = _record_to_event(record)
            except json.JSONDecodeError as exc:
                _handle_streaming_invalid_line(
                    config,
                    counters,
                    line_number,
                    "malformed JSON",
                    exc,
                )
                continue
            except (DomainValidationError, TypeError, ValueError) as exc:
                _handle_streaming_invalid_line(
                    config,
                    counters,
                    line_number,
                    str(exc),
                    exc,
                )
                continue
            counters.valid_event_count += 1
            yield event


def _handle_streaming_invalid_line(
    config: PublicTradeCaptureReadConfig,
    counters: PublicTradeCaptureStreamCounters,
    line_number: int,
    reason: str,
    exc: Exception,
) -> None:
    message = f"line {line_number}: {reason}"
    if not config.skip_invalid_lines:
        raise DomainValidationError(message) from exc
    counters.invalid_line_count += 1


def read_public_trade_capture_jsonl(
    config: PublicTradeCaptureReadConfig,
) -> PublicTradeCaptureReadResult:
    if not isinstance(config, PublicTradeCaptureReadConfig):
        raise DomainValidationError("config must be PublicTradeCaptureReadConfig")
    if not config.input_path.exists():
        raise DomainValidationError(f"capture input does not exist: {config.input_path}")
    if not config.input_path.is_file():
        raise DomainValidationError(f"capture input is not a file: {config.input_path}")

    stream_events: list[PublicTradeStreamEvent] = []
    error_messages: list[str] = []
    total_jsonl_lines = 0

    with config.input_path.open("r", encoding="utf-8") as file_obj:
        for line_number, raw_line in enumerate(file_obj, start=1):
            line = raw_line.strip()
            if not line:
                continue
            total_jsonl_lines += 1
            try:
                record = json.loads(
                    line,
                    parse_float=Decimal,
                    parse_int=int,
                )
                stream_events.append(_record_to_event(record))
            except json.JSONDecodeError as exc:
                _handle_invalid_line(
                    config,
                    error_messages,
                    line_number,
                    "malformed JSON",
                    exc,
                )
            except (DomainValidationError, TypeError, ValueError) as exc:
                _handle_invalid_line(
                    config,
                    error_messages,
                    line_number,
                    str(exc),
                    exc,
                )

    invalid_line_count = len(error_messages)
    return PublicTradeCaptureReadResult(
        input_path=config.input_path,
        stream_events=stream_events,
        total_jsonl_lines=total_jsonl_lines,
        valid_event_count=len(stream_events),
        invalid_line_count=invalid_line_count,
        error_messages=error_messages,
        reasons=[
            "local public trade capture JSONL read completed",
            "network not used",
        ],
        block_reasons=[],
    )


def _record_to_event(record: Any) -> PublicTradeStreamEvent:
    if not isinstance(record, dict):
        raise DomainValidationError("capture record must be an object")
    sequence = record.get("sequence")
    if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 0:
        raise DomainValidationError("sequence must be a non-negative integer")
    received_at = _parse_datetime(record.get("received_at"), "received_at")
    source = record.get("source")
    if not isinstance(source, str) or not source.strip():
        raise DomainValidationError("source must be a non-empty string")
    raw_payload = record.get("raw_payload")
    if not isinstance(raw_payload, dict):
        raise DomainValidationError("raw_payload must be an object")
    mapped_trade_tick = _mapped_trade_tick_from_record(record)
    if mapped_trade_tick is not None:
        mapping_error = record.get("mapping_error")
        if mapping_error is not None and not isinstance(mapping_error, str):
            raise DomainValidationError("mapping_error must be a string or null")
        return PublicTradeStreamEvent(
            sequence=sequence,
            received_at=received_at,
            source=source,
            raw_payload=raw_payload,
            mapped_trade_tick=mapped_trade_tick,
            mapping_error=mapping_error,
            is_duplicate=bool(record.get("is_duplicate", False)),
            is_out_of_order=bool(record.get("is_out_of_order", False)),
        )

    return build_public_trade_stream_event(
        raw_payload=raw_payload,
        sequence=sequence,
        received_at=received_at,
        source=source,
        is_duplicate=bool(record.get("is_duplicate", False)),
        is_out_of_order=bool(record.get("is_out_of_order", False)),
        fail_on_mapping_error=False,
    )


def _mapped_trade_tick_from_record(record: dict[str, Any]) -> TradeTick | None:
    raw_tick = record.get("mapped_trade_tick")
    if raw_tick is None:
        return None
    if not isinstance(raw_tick, dict):
        raise DomainValidationError("mapped_trade_tick must be an object or null")
    symbol = raw_tick.get("symbol")
    timestamp = _parse_datetime(raw_tick.get("timestamp"), "timestamp")
    return TradeTick(
        symbol=_require_string(symbol, "symbol"),
        timestamp=timestamp,
        price=_parse_decimal(raw_tick.get("price"), "price"),
        quantity=_parse_decimal(raw_tick.get("quantity"), "quantity"),
        side=_parse_trade_side(raw_tick.get("side")),
    )


def _handle_invalid_line(
    config: PublicTradeCaptureReadConfig,
    error_messages: list[str],
    line_number: int,
    reason: str,
    exc: Exception,
) -> None:
    message = f"line {line_number}: {reason}"
    if not config.skip_invalid_lines:
        raise DomainValidationError(message) from exc
    error_messages.append(message)


def _parse_datetime(value: Any, field_name: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise DomainValidationError(f"{field_name} must be an ISO datetime string")
    try:
        return datetime.fromisoformat(value.strip())
    except ValueError as exc:
        raise DomainValidationError(
            f"{field_name} must be an ISO datetime string"
        ) from exc


def _parse_decimal(value: Any, field_name: str) -> Decimal:
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return Decimal(value)
    if isinstance(value, str) and value.strip():
        return Decimal(value.strip())
    raise DomainValidationError(f"{field_name} must be a Decimal-compatible value")


def _parse_trade_side(value: Any) -> TradeSide:
    if isinstance(value, TradeSide):
        return value
    if isinstance(value, str):
        try:
            return TradeSide(value)
        except ValueError as exc:
            raise DomainValidationError("side must be a TradeSide") from exc
    raise DomainValidationError("side must be a TradeSide")


def _require_string(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DomainValidationError(f"{field_name} must be a non-empty string")
    return value.strip()


def _validate_local_jsonl_path(path: Path) -> None:
    raw_path = str(path)
    first_part = path.parts[0] if path.parts else ""
    if "://" in raw_path or first_part.endswith(":") or not raw_path.strip():
        raise DomainValidationError("input_path must be a local JSONL file path")
    if path.suffix.lower() != ".jsonl":
        raise DomainValidationError("input_path must be a local JSONL file path")


def _require_non_negative_int(value: int, field_name: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise DomainValidationError(f"{field_name} must be a non-negative integer")


def _require_string_list(value: list[str], field_name: str) -> None:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise DomainValidationError(f"{field_name} must be a list of strings")


@dataclass(frozen=True, slots=True)
class CaptureInputReadSummary:
    """Lightweight per-input read accounting without retained events."""

    input_path: Path
    total_jsonl_lines: int
    valid_event_count: int
    invalid_line_count: int
    block_reasons: list[str]

    def __post_init__(self) -> None:
        if not isinstance(self.input_path, Path):
            raise DomainValidationError("input_path must be a Path")
        for name in (
            "total_jsonl_lines",
            "valid_event_count",
            "invalid_line_count",
        ):
            _require_non_negative_int(getattr(self, name), name)
        if not isinstance(self.block_reasons, list) or not all(
            isinstance(item, str) for item in self.block_reasons
        ):
            raise DomainValidationError("block_reasons must contain strings")
        object.__setattr__(self, "block_reasons", list(self.block_reasons))
