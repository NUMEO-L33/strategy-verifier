"""Integrity diagnostics for local public trade capture JSONL chunks."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import struct

from hashlib import blake2b
from pathlib import Path

from strategy_verifier.domain.errors import DomainValidationError
from strategy_verifier.public_market_data.public_trade_capture_reader import (
    PublicTradeCaptureReadConfig,
    PublicTradeCaptureStreamCounters,
    iter_public_trade_capture_events,
    read_public_trade_capture_jsonl,
)
from strategy_verifier.public_market_data.public_trade_stream_contract import (
    PublicTradeStreamEvent,
)


SUPPORTED_TICK_SIZES = (100, 500, 1000, 2000, 5000)


@dataclass(frozen=True, slots=True)
class CaptureIntegrityDiagnosticsConfig:
    read_configs: tuple[PublicTradeCaptureReadConfig, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.read_configs, tuple) or not self.read_configs:
            raise DomainValidationError("read_configs must be a non-empty tuple")
        if not all(
            isinstance(config, PublicTradeCaptureReadConfig)
            for config in self.read_configs
        ):
            raise DomainValidationError(
                "read_configs must contain PublicTradeCaptureReadConfig"
            )


@dataclass(frozen=True, slots=True)
class CaptureIntegrityIssue:
    issue_type: str
    input_index: int
    input_path: Path
    line_number: int
    event_sequence: int
    timestamp: datetime | None
    previous_input_path: Path | None
    previous_line_number: int | None
    previous_event_sequence: int | None
    previous_timestamp: datetime | None

    def __post_init__(self) -> None:
        if self.issue_type not in {"DUPLICATE_PAYLOAD", "OUT_OF_ORDER_TIMESTAMP"}:
            raise DomainValidationError("issue_type must be supported")
        _require_non_negative_int(self.input_index, "input_index")
        if not isinstance(self.input_path, Path):
            raise DomainValidationError("input_path must be a Path")
        _require_positive_int(self.line_number, "line_number")
        _require_non_negative_int(self.event_sequence, "event_sequence")
        if self.timestamp is not None and not isinstance(self.timestamp, datetime):
            raise DomainValidationError("timestamp must be datetime or None")
        if self.previous_input_path is not None and not isinstance(
            self.previous_input_path,
            Path,
        ):
            raise DomainValidationError("previous_input_path must be Path or None")
        if self.previous_line_number is not None:
            _require_positive_int(self.previous_line_number, "previous_line_number")
        if self.previous_event_sequence is not None:
            _require_non_negative_int(
                self.previous_event_sequence,
                "previous_event_sequence",
            )
        if self.previous_timestamp is not None and not isinstance(
            self.previous_timestamp,
            datetime,
        ):
            raise DomainValidationError("previous_timestamp must be datetime or None")


@dataclass(frozen=True, slots=True)
class CaptureIntegrityDiagnosticsResult:
    config: CaptureIntegrityDiagnosticsConfig
    input_count: int
    total_jsonl_lines: int
    valid_event_count: int
    invalid_line_count: int
    stored_duplicate_count: int
    stored_out_of_order_count: int
    detected_duplicate_payload_count: int
    detected_out_of_order_timestamp_count: int
    issue_count: int
    issue_event_count: int
    clean_event_count: int
    completed_bars_by_tick_size_if_clean: dict[int, int]
    safe_for_strategy_analysis: bool
    issues: list[CaptureIntegrityIssue]
    reasons: list[str]
    block_reasons: list[str]

    def __post_init__(self) -> None:
        if not isinstance(self.config, CaptureIntegrityDiagnosticsConfig):
            raise DomainValidationError(
                "config must be CaptureIntegrityDiagnosticsConfig"
            )
        for field_name in (
            "input_count",
            "total_jsonl_lines",
            "valid_event_count",
            "invalid_line_count",
            "stored_duplicate_count",
            "stored_out_of_order_count",
            "detected_duplicate_payload_count",
            "detected_out_of_order_timestamp_count",
            "issue_count",
            "issue_event_count",
            "clean_event_count",
        ):
            _require_non_negative_int(getattr(self, field_name), field_name)
        if not isinstance(self.completed_bars_by_tick_size_if_clean, dict):
            raise DomainValidationError(
                "completed_bars_by_tick_size_if_clean must be a dict"
            )
        for tick_size, count in self.completed_bars_by_tick_size_if_clean.items():
            if tick_size not in SUPPORTED_TICK_SIZES:
                raise DomainValidationError("unsupported tick size in bar summary")
            _require_non_negative_int(count, "completed bar count")
        if not isinstance(self.safe_for_strategy_analysis, bool):
            raise DomainValidationError("safe_for_strategy_analysis must be a bool")
        if not isinstance(self.issues, list) or not all(
            isinstance(issue, CaptureIntegrityIssue) for issue in self.issues
        ):
            raise DomainValidationError("issues must contain CaptureIntegrityIssue")
        _require_string_list(self.reasons, "reasons")
        _require_string_list(self.block_reasons, "block_reasons")
        object.__setattr__(
            self,
            "completed_bars_by_tick_size_if_clean",
            dict(self.completed_bars_by_tick_size_if_clean),
        )
        object.__setattr__(self, "issues", list(self.issues))
        object.__setattr__(self, "reasons", list(self.reasons))
        object.__setattr__(self, "block_reasons", list(self.block_reasons))


@dataclass(frozen=True, slots=True)
class _LocatedEvent:
    input_index: int
    input_path: Path
    line_number: int
    event: PublicTradeStreamEvent


def _payload_identity_digest(event) -> bytes:
    """16-byte digest of the canonical payload identity.

    The identity is a deterministic tuple of string pairs, encoded via
    repr for a canonical byte form. At the largest realistic scales
    (about 1e8 events) the chance of any blake2b-128 collision is on
    the order of 1e-22, and the equivalence tests inject exact
    duplicates where digest equality coincides with identity equality.
    """
    return blake2b(
        repr(event.payload_identity).encode("utf-8"),
        digest_size=16,
    ).digest()


def run_capture_integrity_diagnostics(
    config: CaptureIntegrityDiagnosticsConfig,
) -> CaptureIntegrityDiagnosticsResult:
    """Streaming integrity diagnostics with bounded memory.

    Events are parsed one line at a time. Duplicate detection keeps one
    digest-keyed location record per distinct payload instead of the
    events themselves, and out-of-order detection keeps only the
    previous event's location, so memory stays far below whole-corpus
    retention at multi-day perpetual scale.
    """
    if not isinstance(config, CaptureIntegrityDiagnosticsConfig):
        raise DomainValidationError(
            "config must be CaptureIntegrityDiagnosticsConfig"
        )

    total_jsonl_lines = 0
    invalid_line_count = 0
    valid_event_count = 0
    stored_duplicate_count = 0
    stored_out_of_order_count = 0
    read_block_reasons: list[str] = []
    issues: list[CaptureIntegrityIssue] = []
    seen_payload_locations: dict[bytes, bytes] = {}
    previous_location: tuple[int, Path, int, int, object] | None = None
    input_paths: list[Path] = []

    for input_index, read_config in enumerate(config.read_configs):
        input_paths.append(read_config.input_path)
        counters = PublicTradeCaptureStreamCounters()
        line_number = 0
        for event in iter_public_trade_capture_events(read_config, counters):
            line_number += 1
            valid_event_count += 1
            if event.is_duplicate:
                stored_duplicate_count += 1
            if event.is_out_of_order:
                stored_out_of_order_count += 1
            timestamp = _event_timestamp(event)
            digest = _payload_identity_digest(event)
            previous_payload = seen_payload_locations.get(digest)
            if previous_payload is not None:
                (
                    prev_input_index,
                    prev_line_number,
                    prev_sequence,
                    prev_epoch_us_raw,
                ) = struct.unpack("<IIQq", previous_payload)
                prev_epoch_us = (
                    None if prev_epoch_us_raw == -1 else prev_epoch_us_raw
                )
                issues.append(
                    CaptureIntegrityIssue(
                        issue_type="DUPLICATE_PAYLOAD",
                        input_index=input_index,
                        input_path=read_config.input_path,
                        line_number=line_number,
                        event_sequence=event.sequence,
                        timestamp=timestamp,
                        previous_input_path=input_paths[prev_input_index],
                        previous_line_number=prev_line_number,
                        previous_event_sequence=prev_sequence,
                        previous_timestamp=_datetime_from_epoch_us(
                            prev_epoch_us
                        ),
                    )
                )
            else:
                epoch_us = _epoch_us_or_none(timestamp)
                seen_payload_locations[digest] = struct.pack(
                    "<IIQq",
                    input_index,
                    line_number,
                    event.sequence,
                    -1 if epoch_us is None else epoch_us,
                )
            if (
                previous_location is not None
                and timestamp is not None
                and previous_location[4] is not None
                and timestamp < previous_location[4]
            ):
                issues.append(
                    CaptureIntegrityIssue(
                        issue_type="OUT_OF_ORDER_TIMESTAMP",
                        input_index=input_index,
                        input_path=read_config.input_path,
                        line_number=line_number,
                        event_sequence=event.sequence,
                        timestamp=timestamp,
                        previous_input_path=previous_location[1],
                        previous_line_number=previous_location[2],
                        previous_event_sequence=previous_location[3],
                        previous_timestamp=previous_location[4],
                    )
                )
            previous_location = (
                input_index,
                read_config.input_path,
                line_number,
                event.sequence,
                timestamp,
            )
        total_jsonl_lines += counters.total_jsonl_lines
        invalid_line_count += counters.invalid_line_count

    detected_duplicate_count = sum(
        1 for issue in issues if issue.issue_type == "DUPLICATE_PAYLOAD"
    )
    detected_out_of_order_count = sum(
        1 for issue in issues if issue.issue_type == "OUT_OF_ORDER_TIMESTAMP"
    )
    issue_count = len(issues)
    issue_event_count = len(
        {
            (issue.input_index, str(issue.input_path), issue.line_number)
            for issue in issues
        }
    )
    clean_event_count = max(0, valid_event_count - issue_event_count)
    block_reasons = list(read_block_reasons)
    if invalid_line_count:
        block_reasons.append("capture contains invalid JSONL lines")
    if detected_duplicate_count:
        block_reasons.append("capture contains duplicate public trade payloads")
    if detected_out_of_order_count:
        block_reasons.append("capture contains out-of-order public trade timestamps")

    return CaptureIntegrityDiagnosticsResult(
        config=config,
        input_count=len(config.read_configs),
        total_jsonl_lines=total_jsonl_lines,
        valid_event_count=valid_event_count,
        invalid_line_count=invalid_line_count,
        stored_duplicate_count=stored_duplicate_count,
        stored_out_of_order_count=stored_out_of_order_count,
        detected_duplicate_payload_count=detected_duplicate_count,
        detected_out_of_order_timestamp_count=detected_out_of_order_count,
        issue_count=issue_count,
        issue_event_count=issue_event_count,
        clean_event_count=clean_event_count,
        completed_bars_by_tick_size_if_clean={
            tick_size: clean_event_count // tick_size
            for tick_size in SUPPORTED_TICK_SIZES
        },
        safe_for_strategy_analysis=not block_reasons,
        issues=issues,
        reasons=[
            "local capture integrity diagnostics completed",
            "network not used",
            "capture files not modified",
        ],
        block_reasons=block_reasons,
    )


_EPOCH_UTC = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _epoch_us_or_none(timestamp: datetime | None) -> int | None:
    if timestamp is None:
        return None
    delta = timestamp - _EPOCH_UTC
    return (
        delta.days * 86_400_000_000
        + delta.seconds * 1_000_000
        + delta.microseconds
    )


def _datetime_from_epoch_us(epoch_us: int | None) -> datetime | None:
    if epoch_us is None:
        return None
    return _EPOCH_UTC + timedelta(microseconds=epoch_us)


def _run_in_memory_reference_diagnostics(
    config: CaptureIntegrityDiagnosticsConfig,
) -> CaptureIntegrityDiagnosticsResult:
    """Whole-list reference engine retained only for equivalence tests.

    Loads every event into memory and is unusable at multi-day perpetual
    scale. The streaming engine below is the production path and must
    always produce an identical result, which the equivalence tests
    enforce.
    """
    if not isinstance(config, CaptureIntegrityDiagnosticsConfig):
        raise DomainValidationError(
            "config must be CaptureIntegrityDiagnosticsConfig"
        )

    located_events: list[_LocatedEvent] = []
    total_jsonl_lines = 0
    invalid_line_count = 0
    read_block_reasons: list[str] = []
    for input_index, read_config in enumerate(config.read_configs):
        read_result = read_public_trade_capture_jsonl(read_config)
        total_jsonl_lines += read_result.total_jsonl_lines
        invalid_line_count += read_result.invalid_line_count
        read_block_reasons.extend(read_result.block_reasons)
        for line_number, event in enumerate(read_result.stream_events, start=1):
            located_events.append(
                _LocatedEvent(
                    input_index=input_index,
                    input_path=read_result.input_path,
                    line_number=line_number,
                    event=event,
                )
            )

    issues = _detect_integrity_issues(located_events)
    stored_duplicate_count = sum(1 for item in located_events if item.event.is_duplicate)
    stored_out_of_order_count = sum(
        1 for item in located_events if item.event.is_out_of_order
    )
    detected_duplicate_count = sum(
        1 for issue in issues if issue.issue_type == "DUPLICATE_PAYLOAD"
    )
    detected_out_of_order_count = sum(
        1 for issue in issues if issue.issue_type == "OUT_OF_ORDER_TIMESTAMP"
    )
    issue_count = len(issues)
    issue_event_count = len(
        {
            (issue.input_index, str(issue.input_path), issue.line_number)
            for issue in issues
        }
    )
    clean_event_count = max(0, len(located_events) - issue_event_count)
    block_reasons = list(read_block_reasons)
    if invalid_line_count:
        block_reasons.append("capture contains invalid JSONL lines")
    if detected_duplicate_count:
        block_reasons.append("capture contains duplicate public trade payloads")
    if detected_out_of_order_count:
        block_reasons.append("capture contains out-of-order public trade timestamps")

    return CaptureIntegrityDiagnosticsResult(
        config=config,
        input_count=len(config.read_configs),
        total_jsonl_lines=total_jsonl_lines,
        valid_event_count=len(located_events),
        invalid_line_count=invalid_line_count,
        stored_duplicate_count=stored_duplicate_count,
        stored_out_of_order_count=stored_out_of_order_count,
        detected_duplicate_payload_count=detected_duplicate_count,
        detected_out_of_order_timestamp_count=detected_out_of_order_count,
        issue_count=issue_count,
        issue_event_count=issue_event_count,
        clean_event_count=clean_event_count,
        completed_bars_by_tick_size_if_clean={
            tick_size: clean_event_count // tick_size for tick_size in SUPPORTED_TICK_SIZES
        },
        safe_for_strategy_analysis=not block_reasons,
        issues=issues,
        reasons=[
            "local capture integrity diagnostics completed",
            "network not used",
            "capture files not modified",
        ],
        block_reasons=block_reasons,
    )


def render_capture_integrity_diagnostics_report(
    result: CaptureIntegrityDiagnosticsResult,
    *,
    show_rows: bool = False,
    max_rows: int = 20,
) -> str:
    if not isinstance(result, CaptureIntegrityDiagnosticsResult):
        raise DomainValidationError(
            "result must be CaptureIntegrityDiagnosticsResult"
        )
    _require_positive_int(max_rows, "max_rows")
    lines = [
        "Capture Integrity Diagnostics Report",
        "Public data only. Local JSONL diagnostics only.",
        "Capture files are not modified.",
        (
            "No API keys, private API, orders, account access, database, "
            "paper runtime, simulated fills, positions, DCA, or live trading."
        ),
        "",
        f"input_count: {result.input_count}",
        f"total_jsonl_lines: {result.total_jsonl_lines}",
        f"valid_event_count: {result.valid_event_count}",
        f"invalid_line_count: {result.invalid_line_count}",
        f"stored_duplicate_count: {result.stored_duplicate_count}",
        f"stored_out_of_order_count: {result.stored_out_of_order_count}",
        (
            "detected_duplicate_payload_count: "
            f"{result.detected_duplicate_payload_count}"
        ),
        (
            "detected_out_of_order_timestamp_count: "
            f"{result.detected_out_of_order_timestamp_count}"
        ),
        f"issue_count: {result.issue_count}",
        f"issue_event_count: {result.issue_event_count}",
        f"clean_event_count: {result.clean_event_count}",
        "completed_bars_by_tick_size_if_clean:",
        *_render_int_map(result.completed_bars_by_tick_size_if_clean),
        f"safe_for_strategy_analysis: {result.safe_for_strategy_analysis}",
        "block_reasons:",
        *_format_items(result.block_reasons),
    ]
    if show_rows:
        lines.extend(
            [
                "integrity_issue_rows:",
                *_render_issue_rows(result.issues[:max_rows]),
            ]
        )
    lines.append("")
    return "\n".join(lines)


def _detect_integrity_issues(
    located_events: list[_LocatedEvent],
) -> list[CaptureIntegrityIssue]:
    issues: list[CaptureIntegrityIssue] = []
    seen_payloads: dict[tuple[tuple[str, str], ...], _LocatedEvent] = {}
    previous: _LocatedEvent | None = None
    for located in located_events:
        public_trade_identity = _public_trade_identity(located.event)
        previous_payload = seen_payloads.get(public_trade_identity)
        if previous_payload is not None:
            issues.append(
                _issue(
                    "DUPLICATE_PAYLOAD",
                    located,
                    previous_payload,
                )
            )
        else:
            seen_payloads[public_trade_identity] = located

        if (
            previous is not None
            and _event_timestamp(located.event) is not None
            and _event_timestamp(previous.event) is not None
            and _event_timestamp(located.event) < _event_timestamp(previous.event)
        ):
            issues.append(
                _issue(
                    "OUT_OF_ORDER_TIMESTAMP",
                    located,
                    previous,
                )
            )
        previous = located
    return issues


def _read_located_events(
    config: CaptureIntegrityDiagnosticsConfig,
) -> list[_LocatedEvent]:
    located_events: list[_LocatedEvent] = []
    for input_index, read_config in enumerate(config.read_configs):
        read_result = read_public_trade_capture_jsonl(read_config)
        for line_number, event in enumerate(read_result.stream_events, start=1):
            located_events.append(
                _LocatedEvent(
                    input_index=input_index,
                    input_path=read_result.input_path,
                    line_number=line_number,
                    event=event,
                )
            )
    return located_events


def _issue(
    issue_type: str,
    located: _LocatedEvent,
    previous: _LocatedEvent,
) -> CaptureIntegrityIssue:
    return CaptureIntegrityIssue(
        issue_type=issue_type,
        input_index=located.input_index,
        input_path=located.input_path,
        line_number=located.line_number,
        event_sequence=located.event.sequence,
        timestamp=_event_timestamp(located.event),
        previous_input_path=previous.input_path,
        previous_line_number=previous.line_number,
        previous_event_sequence=previous.event.sequence,
        previous_timestamp=_event_timestamp(previous.event),
    )


def _event_timestamp(event: PublicTradeStreamEvent) -> datetime | None:
    if event.mapped_trade_tick is None:
        return None
    return event.mapped_trade_tick.timestamp


def _public_trade_identity(
    event: PublicTradeStreamEvent,
) -> tuple[tuple[str, str], ...]:
    raw_payload = event.raw_payload
    upbit_identity_fields = (
        "market",
        "sequential_id",
        "trade_timestamp",
        "trade_price",
        "trade_volume",
        "ask_bid",
    )
    if "sequential_id" in raw_payload:
        return tuple(
            (field_name, repr(raw_payload.get(field_name)))
            for field_name in upbit_identity_fields
        )
    return event.payload_identity


def _render_issue_rows(issues: list[CaptureIntegrityIssue]) -> list[str]:
    if not issues:
        return ["- none"]
    rows: list[str] = []
    for issue in issues:
        rows.extend(
            [
                f"- issue_type: {issue.issue_type}",
                f"  input_index: {issue.input_index}",
                f"  input_path: {issue.input_path}",
                f"  line_number: {issue.line_number}",
                f"  event_sequence: {issue.event_sequence}",
                f"  timestamp: {_format_timestamp(issue.timestamp)}",
                f"  previous_input_path: {issue.previous_input_path}",
                f"  previous_line_number: {issue.previous_line_number}",
                f"  previous_event_sequence: {issue.previous_event_sequence}",
                f"  previous_timestamp: {_format_timestamp(issue.previous_timestamp)}",
            ]
        )
    return rows


def _render_int_map(values: dict[int, int]) -> list[str]:
    if not values:
        return ["- none"]
    return [f"- {key}: {values[key]}" for key in sorted(values)]


def _format_items(items: list[str]) -> list[str]:
    if not items:
        return ["- none"]
    return [f"- {item}" for item in items]


def _format_timestamp(value: datetime | None) -> str:
    return "none" if value is None else value.isoformat()


def _require_non_negative_int(value: int, field_name: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise DomainValidationError(f"{field_name} must be a non-negative integer")


def _require_positive_int(value: int, field_name: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise DomainValidationError(f"{field_name} must be a positive integer")


def _require_string_list(value: list[str], field_name: str) -> None:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise DomainValidationError(f"{field_name} must be a list of strings")
