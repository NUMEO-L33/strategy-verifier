"""Binance UM daily klines conversion to the standard capture schema.

One converted event per day: the identifier is the day number of the
open time, the price is the daily close, the quantity is the daily
base volume, and the timestamp is the kline close time. Each kline is
mapped through the existing aggTrades row mapper, so the output is
byte-compatible with every downstream reader and the integrity gate.

Research tooling only. No orders, no keys, no trading paths.
"""

from __future__ import annotations

import csv
import io
import zipfile
from dataclasses import dataclass
from pathlib import Path

from strategy_verifier.domain.errors import DomainValidationError
from strategy_verifier.public_market_data.binance_um_aggtrade_history import (
    BINANCE_UM_AGGTRADE_SOURCE,
    BinanceUmAggTradeMappingError,
    map_binance_um_aggtrade_row,
)
from strategy_verifier.public_market_data.public_trade_capture_writer import (
    _event_to_jsonl_line,
)
from strategy_verifier.public_market_data.public_trade_stream_contract import (
    PublicTradeStreamEvent,
)


_DAY_MS = 86400000
_HEADER_PREFIX = "open_time"


@dataclass(frozen=True, slots=True)
class BinanceUmDailyKlinesConfig:
    input_path: Path
    output_path: Path
    symbol: str
    overwrite: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.input_path, Path):
            raise DomainValidationError("input_path must be a Path")
        if not isinstance(self.output_path, Path):
            raise DomainValidationError("output_path must be a Path")
        if self.output_path.suffix.lower() != ".jsonl":
            raise DomainValidationError("output_path must end in .jsonl")
        if not isinstance(self.symbol, str) or not self.symbol.strip():
            raise DomainValidationError("symbol must be a non-empty string")
        if not isinstance(self.overwrite, bool):
            raise DomainValidationError("overwrite must be a bool")


@dataclass(frozen=True, slots=True)
class BinanceUmKlinesConfig:
    """Interval-agnostic kline conversion: the identifier is the open
    time in milliseconds, so any bar interval stays unique and ordered."""

    input_path: Path
    output_path: Path
    symbol: str
    overwrite: bool = False
    bar_path: str = "close"

    def __post_init__(self) -> None:
        BinanceUmDailyKlinesConfig.__post_init__(self)  # same checks


@dataclass(frozen=True, slots=True)
class BinanceUmDailyKlinesResult:
    input_path: Path
    output_path: Path
    row_count: int
    converted_count: int
    malformed_count: int
    out_of_order_count: int


def run_binance_um_klines_conversion(
    config: BinanceUmKlinesConfig,
) -> BinanceUmDailyKlinesResult:
    if not isinstance(config, BinanceUmKlinesConfig):
        raise DomainValidationError("config must be BinanceUmKlinesConfig")
    return _convert(config, identifier_mode="open_ms")


def run_binance_um_daily_klines_conversion(
    config: BinanceUmDailyKlinesConfig,
) -> BinanceUmDailyKlinesResult:
    if not isinstance(config, BinanceUmDailyKlinesConfig):
        raise DomainValidationError("config must be BinanceUmDailyKlinesConfig")
    return _convert(config, identifier_mode="day")


def _convert(config, identifier_mode: str) -> BinanceUmDailyKlinesResult:
    if not config.input_path.exists():
        raise DomainValidationError(
            f"input does not exist: {config.input_path}"
        )
    if config.output_path.exists() and not config.overwrite:
        raise DomainValidationError(
            f"output exists and overwrite is false: {config.output_path}"
        )

    if config.input_path.suffix.lower() == ".zip":
        with zipfile.ZipFile(config.input_path) as archive:
            names = [n for n in archive.namelist() if n.endswith(".csv")]
            if len(names) != 1:
                raise DomainValidationError(
                    "kline zip must contain exactly one csv"
                )
            raw_text = archive.read(names[0]).decode("utf-8")
    else:
        raw_text = config.input_path.read_text(encoding="utf-8")

    row_count = 0
    converted = 0
    malformed = 0
    out_of_order = 0
    last_open_ms: int | None = None
    lines: list[str] = []
    for row in csv.reader(io.StringIO(raw_text)):
        if not row or all(not str(cell).strip() for cell in row):
            continue
        if str(row[0]).strip().lower().startswith(_HEADER_PREFIX):
            continue
        row_count += 1
        try:
            open_ms = int(str(row[0]).strip())
            close_ms = int(str(row[6]).strip())
            if close_ms <= open_ms:
                raise ValueError("close before open")
            identifier = (
                open_ms // _DAY_MS if identifier_mode == "day" else open_ms
            )
            tick, raw_payload = map_binance_um_aggtrade_row(
                [
                    str(identifier),
                    str(row[4]).strip(),
                    str(row[5]).strip(),
                    str(identifier),
                    str(identifier),
                    str(close_ms),
                    "false",
                ],
                config.symbol,
            )
        except (ValueError, IndexError, BinanceUmAggTradeMappingError):
            malformed += 1
            continue
        if last_open_ms is not None and open_ms <= last_open_ms:
            out_of_order += 1
            continue
        last_open_ms = open_ms
        bar_path = getattr(config, "bar_path", "close")
        if bar_path == "ohlc" and identifier_mode == "open_ms":
            legs = (
                (0, str(row[1]).strip(), "0", close_ms - 3),
                (1, str(row[2]).strip(), "0", close_ms - 2),
                (2, str(row[3]).strip(), "0", close_ms - 1),
                (3, str(row[4]).strip(), str(row[5]).strip(), close_ms),
            )
            try:
                mapped_legs = [
                    map_binance_um_aggtrade_row(
                        [
                            str(identifier + offset),
                            price_text,
                            quantity_text,
                            str(identifier + offset),
                            str(identifier + offset),
                            str(leg_ms),
                            "false",
                        ],
                        config.symbol,
                    )
                    for offset, price_text, quantity_text, leg_ms in legs
                ]
            except BinanceUmAggTradeMappingError:
                malformed += 1
                continue
            for leg_tick, leg_payload in mapped_legs:
                lines.append(
                    _event_to_jsonl_line(
                        PublicTradeStreamEvent(
                            sequence=converted,
                            received_at=leg_tick.timestamp,
                            source=BINANCE_UM_AGGTRADE_SOURCE,
                            raw_payload=leg_payload,
                            mapped_trade_tick=leg_tick,
                            mapping_error=None,
                            is_duplicate=False,
                            is_out_of_order=False,
                        )
                    )
                )
                converted += 1
            continue
        lines.append(
            _event_to_jsonl_line(
                PublicTradeStreamEvent(
                    sequence=converted,
                    received_at=tick.timestamp,
                    source=BINANCE_UM_AGGTRADE_SOURCE,
                    raw_payload=raw_payload,
                    mapped_trade_tick=tick,
                    mapping_error=None,
                    is_duplicate=False,
                    is_out_of_order=False,
                )
            )
        )
        converted += 1

    config.output_path.parent.mkdir(parents=True, exist_ok=True)
    with config.output_path.open("w", encoding="utf-8") as file_obj:
        for line in lines:
            file_obj.write(line)

    return BinanceUmDailyKlinesResult(
        input_path=config.input_path,
        output_path=config.output_path,
        row_count=row_count,
        converted_count=converted,
        malformed_count=malformed,
        out_of_order_count=out_of_order,
    )
