"""In-memory public trade stream contract types."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Protocol

from strategy_verifier.domain.errors import DomainValidationError
from strategy_verifier.domain.models import TradeTick
from strategy_verifier.public_market_data.upbit_trade_mapper import (
    UpbitPublicTradeMappingError,
    map_upbit_public_trade_payload,
)


class PublicTradeStreamContractError(ValueError):
    """Raised when public trade stream contract inputs are invalid."""


class PublicTradeStreamState(Enum):
    DISCONNECTED = "DISCONNECTED"
    CONNECTING = "CONNECTING"
    CONNECTED = "CONNECTED"
    RECEIVING = "RECEIVING"
    STALE = "STALE"
    RECONNECTING = "RECONNECTING"
    STOPPED = "STOPPED"
    FAILED_CLOSED = "FAILED_CLOSED"


@dataclass(frozen=True, slots=True)
class PublicTradeStreamStatus:
    state: PublicTradeStreamState
    symbol: str
    received_count: int
    mapped_count: int
    malformed_count: int
    duplicate_count: int
    out_of_order_count: int
    stale: bool
    reasons: list[str]
    block_reasons: list[str]

    def __post_init__(self) -> None:
        if not isinstance(self.state, PublicTradeStreamState):
            raise DomainValidationError("state must be PublicTradeStreamState")
        _require_string(self.symbol, "symbol")
        _require_non_negative_int(self.received_count, "received_count")
        _require_non_negative_int(self.mapped_count, "mapped_count")
        _require_non_negative_int(self.malformed_count, "malformed_count")
        _require_non_negative_int(self.duplicate_count, "duplicate_count")
        _require_non_negative_int(self.out_of_order_count, "out_of_order_count")
        if not isinstance(self.stale, bool):
            raise DomainValidationError("stale must be a bool")
        _require_string_list(self.reasons, "reasons")
        _require_string_list(self.block_reasons, "block_reasons")
        object.__setattr__(self, "symbol", self.symbol.strip())
        object.__setattr__(self, "reasons", list(self.reasons))
        object.__setattr__(self, "block_reasons", list(self.block_reasons))


@dataclass(frozen=True, slots=True)
class PublicTradeStreamEvent:
    sequence: int
    received_at: datetime
    source: str
    raw_payload: dict[str, Any]
    mapped_trade_tick: TradeTick | None
    mapping_error: str | None
    is_duplicate: bool
    is_out_of_order: bool

    def __post_init__(self) -> None:
        _require_non_negative_int(self.sequence, "sequence")
        if not isinstance(self.received_at, datetime):
            raise DomainValidationError("received_at must be a datetime")
        _require_string(self.source, "source")
        if not isinstance(self.raw_payload, dict):
            raise DomainValidationError("raw_payload must be a dict")
        if _contains_float(self.raw_payload):
            raise DomainValidationError("raw_payload must not contain floats")
        if self.mapped_trade_tick is not None and not isinstance(
            self.mapped_trade_tick,
            TradeTick,
        ):
            raise DomainValidationError("mapped_trade_tick must be TradeTick or None")
        if self.mapping_error is not None and not isinstance(self.mapping_error, str):
            raise DomainValidationError("mapping_error must be a string or None")
        if not isinstance(self.is_duplicate, bool):
            raise DomainValidationError("is_duplicate must be a bool")
        if not isinstance(self.is_out_of_order, bool):
            raise DomainValidationError("is_out_of_order must be a bool")
        object.__setattr__(self, "source", self.source.strip())
        object.__setattr__(self, "raw_payload", dict(self.raw_payload))

    @property
    def payload_identity(self) -> tuple[tuple[str, str], ...]:
        return tuple(
            sorted(
                (str(key), repr(value))
                for key, value in self.raw_payload.items()
            )
        )


class PublicTradeStreamConsumer(Protocol):
    def __call__(self, event: PublicTradeStreamEvent) -> None:
        """Accept a public trade stream event."""


def build_public_trade_stream_event(
    *,
    raw_payload: dict[str, Any],
    sequence: int,
    received_at: datetime,
    source: str,
    is_duplicate: bool = False,
    is_out_of_order: bool = False,
    fail_on_mapping_error: bool = False,
) -> PublicTradeStreamEvent:
    if not isinstance(raw_payload, dict):
        raise DomainValidationError("raw_payload must be a dict")
    if _contains_float(raw_payload):
        raise DomainValidationError("raw_payload must not contain floats")

    mapped_trade_tick: TradeTick | None
    mapping_error: str | None
    try:
        mapped_trade_tick = map_upbit_public_trade_payload(raw_payload)
        mapping_error = None
    except UpbitPublicTradeMappingError as exc:
        if fail_on_mapping_error:
            raise PublicTradeStreamContractError(str(exc)) from exc
        mapped_trade_tick = None
        mapping_error = str(exc)

    return PublicTradeStreamEvent(
        sequence=sequence,
        received_at=received_at,
        source=source,
        raw_payload=raw_payload,
        mapped_trade_tick=mapped_trade_tick,
        mapping_error=mapping_error,
        is_duplicate=is_duplicate,
        is_out_of_order=is_out_of_order,
    )


def detect_trade_stream_event_issue(
    event: PublicTradeStreamEvent,
    *,
    previous_sequence: int | None = None,
    seen_sequences: set[int] | None = None,
    seen_payload_identities: set[tuple[tuple[str, str], ...]] | None = None,
) -> list[str]:
    if not isinstance(event, PublicTradeStreamEvent):
        raise DomainValidationError("event must be PublicTradeStreamEvent")
    if previous_sequence is not None:
        _require_non_negative_int(previous_sequence, "previous_sequence")

    reasons: list[str] = []
    if seen_sequences is not None and event.sequence in seen_sequences:
        reasons.append(f"duplicate_sequence:{event.sequence}")
    if (
        seen_payload_identities is not None
        and event.payload_identity in seen_payload_identities
    ):
        reasons.append("duplicate_payload")
    if previous_sequence is not None and event.sequence < previous_sequence:
        reasons.append(f"out_of_order_sequence:{event.sequence}<{previous_sequence}")
    return reasons


def _require_string(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise DomainValidationError(f"{field_name} must be a non-empty string")


def _require_non_negative_int(value: int, field_name: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise DomainValidationError(f"{field_name} must be a non-negative integer")


def _require_string_list(value: list[str], field_name: str) -> None:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise DomainValidationError(f"{field_name} must be a list of strings")


def _contains_float(value: Any) -> bool:
    if isinstance(value, float):
        return True
    if isinstance(value, dict):
        return any(_contains_float(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_float(item) for item in value)
    return False
