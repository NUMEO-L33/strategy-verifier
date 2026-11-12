"""Map static Upbit-like public trade payloads to TradeTick objects."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from strategy_verifier.domain.enums import TradeSide
from strategy_verifier.domain.errors import DomainValidationError
from strategy_verifier.domain.models import TradeTick


class UpbitPublicTradeMappingError(ValueError):
    """Raised when a public trade payload cannot be mapped deterministically."""


def map_upbit_public_trade_payload(payload: dict[str, Any]) -> TradeTick:
    if not isinstance(payload, dict):
        raise UpbitPublicTradeMappingError("payload must be a dict")

    symbol = _required_string(payload, ("market", "symbol", "code"), "market")
    timestamp = _timestamp(payload)
    price = _decimal(payload, "trade_price")
    quantity = _decimal(payload, "trade_volume")
    side = _side(payload.get("ask_bid"))
    try:
        return TradeTick(
            symbol=symbol,
            timestamp=timestamp,
            price=price,
            quantity=quantity,
            side=side,
        )
    except DomainValidationError as exc:
        raise UpbitPublicTradeMappingError(str(exc)) from exc


def map_upbit_public_trade_payloads(payloads: list[dict[str, Any]]) -> list[TradeTick]:
    if not isinstance(payloads, list):
        raise UpbitPublicTradeMappingError("payloads must be a list")
    return [map_upbit_public_trade_payload(payload) for payload in payloads]


def _required_string(
    payload: dict[str, Any],
    keys: tuple[str, ...],
    field_name: str,
) -> str:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    raise UpbitPublicTradeMappingError(f"missing required {field_name}")


def _timestamp(payload: dict[str, Any]) -> datetime:
    raw_value = payload.get("trade_timestamp", payload.get("timestamp"))
    if raw_value is None:
        raise UpbitPublicTradeMappingError("missing required timestamp")
    if isinstance(raw_value, str) and raw_value.strip():
        try:
            return datetime.fromisoformat(raw_value.strip())
        except ValueError as exc:
            raise UpbitPublicTradeMappingError("timestamp is not parseable") from exc
    if isinstance(raw_value, bool):
        raise UpbitPublicTradeMappingError("timestamp is not parseable")
    if isinstance(raw_value, int):
        return _timestamp_from_milliseconds(raw_value)
    if isinstance(raw_value, Decimal):
        if raw_value != raw_value.to_integral_value():
            raise UpbitPublicTradeMappingError("timestamp is not parseable")
        return _timestamp_from_milliseconds(int(raw_value))
    raise UpbitPublicTradeMappingError("timestamp is not parseable")


def _timestamp_from_milliseconds(milliseconds: int) -> datetime:
    if milliseconds <= 0:
        raise UpbitPublicTradeMappingError("timestamp is not parseable")
    try:
        epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
        return epoch + timedelta(milliseconds=milliseconds)
    except OverflowError as exc:
        raise UpbitPublicTradeMappingError("timestamp is not parseable") from exc


def _decimal(payload: dict[str, Any], key: str) -> Decimal:
    raw_value = payload.get(key)
    if raw_value is None:
        raise UpbitPublicTradeMappingError(f"missing required {key}")
    if isinstance(raw_value, float):
        raise UpbitPublicTradeMappingError(f"{key} must not be a float")
    try:
        return Decimal(str(raw_value).strip())
    except (InvalidOperation, ValueError) as exc:
        raise UpbitPublicTradeMappingError(f"{key} is not parseable") from exc


def _side(raw_value: Any) -> TradeSide:
    if not isinstance(raw_value, str):
        return TradeSide.UNKNOWN
    normalized = raw_value.strip().upper()
    if normalized == "BID":
        return TradeSide.BUY
    if normalized == "ASK":
        return TradeSide.SELL
    return TradeSide.UNKNOWN
