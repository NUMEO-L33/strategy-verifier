"""Convert Binance USDT-M futures aggTrades history files to capture JSONL.

This module converts locally downloaded, public Binance USDT-M futures
aggregated-trade history files (daily CSV or ZIP archives from the public
Binance data repository) into the existing public trade capture JSONL format.

It is a local, offline converter only. It performs no network calls, uses no
API keys or private APIs, accesses no accounts, creates no orders, and adds no
paper runtime, simulated fills, positions, DCA automation, database
persistence, or live trading paths.
"""

from __future__ import annotations

import csv
import io
import zipfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import (
    Any,
    TextIO,
)

from strategy_verifier.domain.enums import TradeSide
from strategy_verifier.domain.errors import DomainValidationError
from strategy_verifier.domain.models import TradeTick
from strategy_verifier.public_market_data.public_trade_capture_writer import (
    _event_to_jsonl_line,
)
from strategy_verifier.public_market_data.public_trade_stream_contract import (
    PublicTradeStreamEvent,
)


BINANCE_UM_AGGTRADE_SOURCE = "binance_um_futures_aggtrade_history"
_EXPECTED_COLUMN_COUNT = 7
_EPOCH_UTC = datetime(1970, 1, 1, tzinfo=timezone.utc)
_MAX_STORED_ERROR_MESSAGES = 20


class BinanceUmAggTradeMappingError(ValueError):
    """Raised when an aggTrades row cannot be mapped deterministically."""


@dataclass(frozen=True, slots=True)
class BinanceUmAggTradeHistoryConfig:
    input_path: Path
    output_path: Path
    symbol: str
    fail_on_malformed: bool = False
    overwrite: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.input_path, Path):
            raise DomainValidationError("input_path must be a Path")
        suffix = self.input_path.suffix.lower()
        if suffix not in (".csv", ".zip"):
            raise DomainValidationError(
                "input_path must be a local .csv or .zip file path"
            )
        if not isinstance(self.output_path, Path):
            raise DomainValidationError("output_path must be a Path")
        _validate_local_jsonl_path(self.output_path)
        if not isinstance(self.symbol, str) or not self.symbol.strip():
            raise DomainValidationError("symbol must be a non-empty string")
        if not isinstance(self.fail_on_malformed, bool):
            raise DomainValidationError("fail_on_malformed must be a bool")
        if not isinstance(self.overwrite, bool):
            raise DomainValidationError("overwrite must be a bool")


@dataclass(frozen=True, slots=True)
class BinanceUmAggTradeHistoryResult:
    input_path: Path
    output_path: Path
    symbol: str
    total_rows: int
    header_rows_skipped: int
    converted_event_count: int
    malformed_row_count: int
    bytes_written: int
    first_event_timestamp: datetime | None
    last_event_timestamp: datetime | None
    error_messages: list[str]
    reasons: list[str]
    block_reasons: list[str]

    def __post_init__(self) -> None:
        if not isinstance(self.input_path, Path):
            raise DomainValidationError("input_path must be a Path")
        if not isinstance(self.output_path, Path):
            raise DomainValidationError("output_path must be a Path")
        if not isinstance(self.symbol, str) or not self.symbol.strip():
            raise DomainValidationError("symbol must be a non-empty string")
        for name in (
            "total_rows",
            "header_rows_skipped",
            "converted_event_count",
            "malformed_row_count",
            "bytes_written",
        ):
            _require_non_negative_int(getattr(self, name), name)
        if self.total_rows != (
            self.header_rows_skipped
            + self.converted_event_count
            + self.malformed_row_count
        ):
            raise DomainValidationError("row counts are inconsistent")
        for name in ("first_event_timestamp", "last_event_timestamp"):
            value = getattr(self, name)
            if value is not None and not isinstance(value, datetime):
                raise DomainValidationError(f"{name} must be a datetime or None")
        _require_string_list(self.error_messages, "error_messages")
        _require_string_list(self.reasons, "reasons")
        _require_string_list(self.block_reasons, "block_reasons")
        object.__setattr__(self, "error_messages", list(self.error_messages))
        object.__setattr__(self, "reasons", list(self.reasons))
        object.__setattr__(self, "block_reasons", list(self.block_reasons))


def map_binance_um_aggtrade_row(
    row: list[str],
    symbol: str,
) -> tuple[TradeTick, dict[str, Any]]:
    """Map one aggTrades CSV row to a TradeTick and its raw payload dict.

    Expected columns: agg_trade_id, price, quantity, first_trade_id,
    last_trade_id, transact_time, is_buyer_maker.
    """
    if not isinstance(row, list) or len(row) != _EXPECTED_COLUMN_COUNT:
        raise BinanceUmAggTradeMappingError(
            f"row must have {_EXPECTED_COLUMN_COUNT} columns"
        )
    if not isinstance(symbol, str) or not symbol.strip():
        raise BinanceUmAggTradeMappingError("symbol must be a non-empty string")
    cells = [str(cell).strip() for cell in row]
    agg_trade_id = _required_int(cells[0], "agg_trade_id")
    price = _required_decimal(cells[1], "price")
    quantity = _required_decimal(cells[2], "quantity")
    first_trade_id = _required_int(cells[3], "first_trade_id")
    last_trade_id = _required_int(cells[4], "last_trade_id")
    timestamp = _timestamp_from_epoch_field(cells[5])
    side = _side_from_is_buyer_maker(cells[6])
    try:
        tick = TradeTick(
            symbol=symbol.strip(),
            timestamp=timestamp,
            price=price,
            quantity=quantity,
            side=side,
        )
    except DomainValidationError as exc:
        raise BinanceUmAggTradeMappingError(str(exc)) from exc
    raw_payload: dict[str, Any] = {
        "symbol": symbol.strip(),
        "agg_trade_id": agg_trade_id,
        "price": cells[1],
        "quantity": cells[2],
        "first_trade_id": first_trade_id,
        "last_trade_id": last_trade_id,
        "transact_time": cells[5],
        "is_buyer_maker": cells[6],
    }
    return tick, raw_payload


def run_binance_um_aggtrade_history_conversion(
    config: BinanceUmAggTradeHistoryConfig,
) -> BinanceUmAggTradeHistoryResult:
    if not isinstance(config, BinanceUmAggTradeHistoryConfig):
        raise DomainValidationError(
            "config must be BinanceUmAggTradeHistoryConfig"
        )
    if not config.input_path.exists():
        raise DomainValidationError(
            f"input does not exist: {config.input_path}"
        )
    if not config.input_path.is_file():
        raise DomainValidationError(
            f"input is not a file: {config.input_path}"
        )
    if config.output_path.exists() and not config.overwrite:
        raise DomainValidationError(
            "output already exists; pass overwrite to replace it: "
            f"{config.output_path}"
        )

    total_rows = 0
    header_rows_skipped = 0
    converted_event_count = 0
    malformed_row_count = 0
    bytes_written = 0
    first_event_timestamp: datetime | None = None
    last_event_timestamp: datetime | None = None
    error_messages: list[str] = []
    suppressed_error_count = 0

    config.output_path.parent.mkdir(parents=True, exist_ok=True)
    with _open_input_text(config.input_path) as text_stream:
        reader = csv.reader(text_stream)
        with config.output_path.open("w", encoding="utf-8") as output:
            for row_number, row in enumerate(reader, start=1):
                if not row or all(not str(cell).strip() for cell in row):
                    continue
                total_rows += 1
                if row_number == 1 and _looks_like_header(row):
                    header_rows_skipped += 1
                    continue
                try:
                    tick, raw_payload = map_binance_um_aggtrade_row(
                        row,
                        config.symbol,
                    )
                except BinanceUmAggTradeMappingError as exc:
                    malformed_row_count += 1
                    message = f"row {row_number}: {exc}"
                    if config.fail_on_malformed:
                        raise DomainValidationError(message) from exc
                    if len(error_messages) < _MAX_STORED_ERROR_MESSAGES:
                        error_messages.append(message)
                    else:
                        suppressed_error_count += 1
                    continue
                event = PublicTradeStreamEvent(
                    sequence=converted_event_count,
                    received_at=tick.timestamp,
                    source=BINANCE_UM_AGGTRADE_SOURCE,
                    raw_payload=raw_payload,
                    mapped_trade_tick=tick,
                    mapping_error=None,
                    is_duplicate=False,
                    is_out_of_order=False,
                )
                line = _event_to_jsonl_line(event)
                output.write(line)
                bytes_written += len(line.encode("utf-8"))
                converted_event_count += 1
                if first_event_timestamp is None:
                    first_event_timestamp = tick.timestamp
                last_event_timestamp = tick.timestamp

    reasons = [
        "local Binance USDT-M futures aggTrades history conversion completed",
        "public history file input only; network not used",
        "received_at is set to the historical transact time",
        "no API keys, private API, orders, account access, database, "
        "paper runtime, simulated fills, positions, DCA, or live trading",
    ]
    if suppressed_error_count > 0:
        reasons.append(
            f"{suppressed_error_count} additional malformed row messages "
            "suppressed"
        )
    return BinanceUmAggTradeHistoryResult(
        input_path=config.input_path,
        output_path=config.output_path,
        symbol=config.symbol.strip(),
        total_rows=total_rows,
        header_rows_skipped=header_rows_skipped,
        converted_event_count=converted_event_count,
        malformed_row_count=malformed_row_count,
        bytes_written=bytes_written,
        first_event_timestamp=first_event_timestamp,
        last_event_timestamp=last_event_timestamp,
        error_messages=error_messages,
        reasons=reasons,
        block_reasons=[],
    )


def render_binance_um_aggtrade_history_report(
    result: BinanceUmAggTradeHistoryResult,
) -> str:
    if not isinstance(result, BinanceUmAggTradeHistoryResult):
        raise DomainValidationError(
            "result must be BinanceUmAggTradeHistoryResult"
        )
    lines = [
        "Binance USDT-M Futures AggTrades History Conversion Report",
        "Public history file input only. Research data conversion only.",
        "Network not used. No API keys or private API.",
        (
            "No orders, account access, database, paper runtime, simulated "
            "fills, positions, DCA, or live trading."
        ),
        "",
        f"input_path: {result.input_path}",
        f"output_path: {result.output_path}",
        f"symbol: {result.symbol}",
        f"source: {BINANCE_UM_AGGTRADE_SOURCE}",
        f"total_rows: {result.total_rows}",
        f"header_rows_skipped: {result.header_rows_skipped}",
        f"converted_event_count: {result.converted_event_count}",
        f"malformed_row_count: {result.malformed_row_count}",
        f"bytes_written: {result.bytes_written}",
        (
            "first_event_timestamp: "
            f"{_render_timestamp(result.first_event_timestamp)}"
        ),
        (
            "last_event_timestamp: "
            f"{_render_timestamp(result.last_event_timestamp)}"
        ),
        "error_messages:",
        *_render_items(result.error_messages),
        "reasons:",
        *_render_items(result.reasons),
        "block_reasons:",
        *_render_items(result.block_reasons),
        "",
    ]
    return "\n".join(lines)


def _open_input_text(input_path: Path) -> TextIO:
    if input_path.suffix.lower() == ".csv":
        return input_path.open("r", encoding="utf-8", newline="")
    archive = zipfile.ZipFile(input_path)
    csv_members = [
        name for name in archive.namelist() if name.lower().endswith(".csv")
    ]
    if len(csv_members) != 1:
        archive.close()
        raise DomainValidationError(
            "zip input must contain exactly one .csv member"
        )
    binary = archive.open(csv_members[0], "r")
    return _ClosingTextWrapper(binary, archive)


class _ClosingTextWrapper(io.TextIOWrapper):
    """Text wrapper that also closes the owning zip archive."""

    def __init__(self, binary: Any, archive: zipfile.ZipFile) -> None:
        super().__init__(binary, encoding="utf-8", newline="")
        self._archive = archive

    def close(self) -> None:
        try:
            super().close()
        finally:
            self._archive.close()


def _looks_like_header(row: list[str]) -> bool:
    first_cell = str(row[0]).strip()
    return not first_cell.lstrip("-").isdigit()


def _required_int(value: str, field_name: str) -> int:
    cell = value.strip()
    if not cell.lstrip("-").isdigit():
        raise BinanceUmAggTradeMappingError(
            f"{field_name} must be an integer"
        )
    parsed = int(cell)
    if parsed < 0:
        raise BinanceUmAggTradeMappingError(
            f"{field_name} must be non-negative"
        )
    return parsed


def _required_decimal(value: str, field_name: str) -> Decimal:
    cell = value.strip()
    if not cell:
        raise BinanceUmAggTradeMappingError(f"missing required {field_name}")
    try:
        return Decimal(cell)
    except InvalidOperation as exc:
        raise BinanceUmAggTradeMappingError(
            f"{field_name} must be a decimal number"
        ) from exc


def _timestamp_from_epoch_field(value: str) -> datetime:
    epoch_value = _required_int(value, "transact_time")
    if epoch_value >= 10**14:
        microseconds = epoch_value
    elif epoch_value >= 10**11:
        microseconds = epoch_value * 1_000
    else:
        microseconds = epoch_value * 1_000_000
    return _EPOCH_UTC + timedelta(microseconds=microseconds)


def _side_from_is_buyer_maker(value: str) -> TradeSide:
    flag = value.strip().lower()
    if flag == "true":
        return TradeSide.SELL
    if flag == "false":
        return TradeSide.BUY
    raise BinanceUmAggTradeMappingError(
        "is_buyer_maker must be true or false"
    )


def _validate_local_jsonl_path(path: Path) -> None:
    raw_path = str(path)
    first_part = path.parts[0] if path.parts else ""
    if "://" in raw_path or first_part.endswith(":") or not raw_path.strip():
        raise DomainValidationError("output_path must be a local JSONL file path")
    if path.suffix.lower() != ".jsonl":
        raise DomainValidationError("output_path must be a local JSONL file path")


def _render_timestamp(value: datetime | None) -> str:
    if value is None:
        return "none"
    return value.isoformat()


def _render_items(items: list[str]) -> list[str]:
    if not items:
        return ["- none"]
    return [f"- {item}" for item in items]


def _require_non_negative_int(value: int, field_name: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise DomainValidationError(f"{field_name} must be a non-negative integer")


def _require_string_list(values: list[str], field_name: str) -> None:
    if not isinstance(values, list) or not all(
        isinstance(value, str) for value in values
    ):
        raise DomainValidationError(f"{field_name} must contain strings")
