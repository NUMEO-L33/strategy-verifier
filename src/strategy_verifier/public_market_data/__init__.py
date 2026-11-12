"""Public market data: capture reading, integrity gating, sessions, and Binance converters."""

from strategy_verifier.public_market_data.binance_um_aggtrade_history import (
    BINANCE_UM_AGGTRADE_SOURCE,
    BinanceUmAggTradeHistoryConfig,
    BinanceUmAggTradeHistoryResult,
    BinanceUmAggTradeMappingError,
    map_binance_um_aggtrade_row,
    render_binance_um_aggtrade_history_report,
    run_binance_um_aggtrade_history_conversion,
)
from strategy_verifier.public_market_data.binance_um_daily_klines import (
    BinanceUmDailyKlinesConfig,
    BinanceUmKlinesConfig,
    run_binance_um_klines_conversion,
    BinanceUmDailyKlinesResult,
    run_binance_um_daily_klines_conversion,
)
from strategy_verifier.public_market_data.capture_integrity_diagnostics import (
    CaptureIntegrityDiagnosticsConfig,
    CaptureIntegrityDiagnosticsResult,
    CaptureIntegrityIssue,
    render_capture_integrity_diagnostics_report,
    run_capture_integrity_diagnostics,
)
from strategy_verifier.public_market_data.capture_integrity_preparation import (
    CaptureIntegrityManifest,
    PreparedCaptureInputContext,
    PreparedCaptureInputs,
    prepare_capture_inputs,
    render_integrity_manifest,
)
from strategy_verifier.public_market_data.public_trade_capture_reader import (
    PublicTradeCaptureReadConfig,
    PublicTradeCaptureReadResult,
    PublicTradeCaptureStreamCounters,
    iter_public_trade_capture_events,
    read_public_trade_capture_jsonl,
)
from strategy_verifier.public_market_data.public_trade_stream_contract import (
    PublicTradeStreamContractError,
    PublicTradeStreamConsumer,
    PublicTradeStreamEvent,
    PublicTradeStreamState,
    PublicTradeStreamStatus,
    build_public_trade_stream_event,
    detect_trade_stream_event_issue,
)

__all__ = [
    "BINANCE_UM_AGGTRADE_SOURCE",
    "BinanceUmAggTradeHistoryConfig",
    "BinanceUmAggTradeHistoryResult",
    "BinanceUmAggTradeMappingError",
    "BinanceUmDailyKlinesConfig",
    "BinanceUmDailyKlinesResult",
    "BinanceUmKlinesConfig",
    "CaptureIntegrityDiagnosticsConfig",
    "CaptureIntegrityDiagnosticsResult",
    "CaptureIntegrityIssue",
    "CaptureIntegrityManifest",
    "PreparedCaptureInputContext",
    "PreparedCaptureInputs",
    "PublicTradeCaptureReadConfig",
    "PublicTradeCaptureReadResult",
    "PublicTradeCaptureStreamCounters",
    "PublicTradeStreamConsumer",
    "PublicTradeStreamContractError",
    "PublicTradeStreamEvent",
    "PublicTradeStreamState",
    "PublicTradeStreamStatus",
    "build_public_trade_stream_event",
    "detect_trade_stream_event_issue",
    "iter_public_trade_capture_events",
    "map_binance_um_aggtrade_row",
    "prepare_capture_inputs",
    "read_public_trade_capture_jsonl",
    "render_binance_um_aggtrade_history_report",
    "render_capture_integrity_diagnostics_report",
    "render_integrity_manifest",
    "run_binance_um_aggtrade_history_conversion",
    "run_binance_um_daily_klines_conversion",
    "run_binance_um_klines_conversion",
    "run_capture_integrity_diagnostics",
]
