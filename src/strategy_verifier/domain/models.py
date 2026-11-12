"""Serializable domain models for public market data."""

from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from strategy_verifier.domain.enums import TradeSide
from strategy_verifier.domain.errors import DomainValidationError


def _require_non_empty_string(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise DomainValidationError(f"{field_name} must be a non-empty string")


def _require_datetime(value: datetime, field_name: str) -> None:
    if not isinstance(value, datetime):
        raise DomainValidationError(f"{field_name} must be a datetime")


def _require_decimal(value: Decimal, field_name: str) -> None:
    if not isinstance(value, Decimal):
        raise DomainValidationError(f"{field_name} must be a Decimal")


def _require_positive_decimal(value: Decimal, field_name: str) -> None:
    _require_decimal(value, field_name)
    if value <= 0:
        raise DomainValidationError(f"{field_name} must be greater than zero")


def _require_non_negative_decimal(value: Decimal, field_name: str) -> None:
    _require_decimal(value, field_name)
    if value < 0:
        raise DomainValidationError(f"{field_name} must not be negative")


def _require_optional_positive_decimal(value: Decimal | None, field_name: str) -> None:
    if value is not None:
        _require_positive_decimal(value, field_name)


def _require_positive_int(value: int, field_name: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise DomainValidationError(f"{field_name} must be a positive integer")


def _require_enum(value: Enum, enum_type: type[Enum], field_name: str) -> None:
    if not isinstance(value, enum_type):
        raise DomainValidationError(f"{field_name} must be a {enum_type.__name__}")


def _serialize(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, SerializableModel):
        return value.to_dict()
    if isinstance(value, dict):
        return {key: _serialize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_serialize(item) for item in value]
    return value


class SerializableModel:
    """Small serialization helper for deterministic research artifacts."""

    def to_dict(self) -> dict[str, Any]:
        return {
            field.name: _serialize(getattr(self, field.name))
            for field in fields(self)
        }


@dataclass(frozen=True, slots=True)
class TradeTick(SerializableModel):
    symbol: str
    timestamp: datetime
    price: Decimal
    quantity: Decimal
    side: TradeSide

    def __post_init__(self) -> None:
        _require_non_empty_string(self.symbol, "symbol")
        _require_datetime(self.timestamp, "timestamp")
        _require_positive_decimal(self.price, "price")
        _require_non_negative_decimal(self.quantity, "quantity")
        _require_enum(self.side, TradeSide, "side")


