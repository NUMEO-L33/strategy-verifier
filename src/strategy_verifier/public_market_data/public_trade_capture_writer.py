"""Local JSONL capture writer for bounded public trade events."""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from strategy_verifier.domain.errors import DomainValidationError
from strategy_verifier.domain.models import TradeTick
from strategy_verifier.public_market_data.public_trade_stream_contract import (
    PublicTradeStreamEvent,
)


def _event_to_record(event: PublicTradeStreamEvent) -> dict[str, Any]:
    if not isinstance(event, PublicTradeStreamEvent):
        raise DomainValidationError("event must be PublicTradeStreamEvent")
    return {
        "sequence": event.sequence,
        "received_at": _serialize_datetime(event.received_at),
        "source": event.source,
        "raw_payload": _serialize_value(event.raw_payload),
        "mapped_trade_tick": _trade_tick_to_record(event.mapped_trade_tick),
        "mapping_error": event.mapping_error,
        "is_duplicate": event.is_duplicate,
        "is_out_of_order": event.is_out_of_order,
    }


def _event_to_jsonl_line(event: PublicTradeStreamEvent) -> str:
    return (
        json.dumps(
            _event_to_record(event),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    )


def _trade_tick_to_record(tick: TradeTick | None) -> dict[str, Any] | None:
    if tick is None:
        return None
    return {
        "symbol": tick.symbol,
        "timestamp": _serialize_datetime(tick.timestamp),
        "price": str(tick.price),
        "quantity": str(tick.quantity),
        "side": tick.side.value,
    }


def _serialize_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return _serialize_datetime(value)
    if isinstance(value, dict):
        return {str(key): _serialize_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_serialize_value(item) for item in value]
    if isinstance(value, tuple):
        return [_serialize_value(item) for item in value]
    return value


def _serialize_datetime(value: datetime) -> str:
    return value.isoformat()


def _validate_local_jsonl_path(path: Path) -> None:
    raw_path = str(path)
    first_part = path.parts[0] if path.parts else ""
    if "://" in raw_path or first_part.endswith(":") or not raw_path.strip():
        raise DomainValidationError("output_path must be a local JSONL file path")
    if path.suffix.lower() != ".jsonl":
        raise DomainValidationError("output_path must be a local JSONL file path")


def _require_non_negative_int(value: int, field_name: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise DomainValidationError(f"{field_name} must be a non-negative integer")


def _require_string_list(value: list[str], field_name: str) -> None:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise DomainValidationError(f"{field_name} must be a list of strings")


def _format_items(items: list[str]) -> list[str]:
    if not items:
        return ["- none"]
    return [f"- {item}" for item in items]
