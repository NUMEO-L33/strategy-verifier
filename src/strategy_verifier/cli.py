"""Command line interface for the strategy verifier (research diagnostics only)."""

from __future__ import annotations

import argparse
import atexit
import sys
from decimal import InvalidOperation
from pathlib import Path
from typing import (
    Sequence,
    TextIO,
)

from strategy_verifier.domain.errors import DomainValidationError
from strategy_verifier.verifier import (
    advance_family_state,
    render_certificate,
    render_reading_report,
    run_reading,
    render_equation_block,
    list_family_states,
    load_family_state,
    load_verifier_definition,
    render_family_status,
    render_registration_report,
    build_attestation,
    check_attestation,
    render_attestation,
    render_diagnosis_report,
    render_verdict_report,
    run_diagnosis,
    run_registration,
    run_verdict,
    render_claim_triage_report,
    run_claim_triage,
    run_verifier_definition,
)
from strategy_verifier.public_market_data import (
    BinanceUmAggTradeHistoryConfig,
    BinanceUmDailyKlinesConfig,
    BinanceUmKlinesConfig,
    render_binance_um_aggtrade_history_report,
    run_binance_um_aggtrade_history_conversion,
    run_binance_um_daily_klines_conversion,
    run_binance_um_klines_conversion,
)
from strategy_verifier.public_market_data.capture_integrity_preparation import (
    CaptureIntegrityManifest,
    prepare_capture_inputs,
    render_integrity_manifest,
)
from strategy_verifier.safety import run_safety_review


_CAPTURE_INPUT_COMMANDS = {
    "verify-read",
    "verify-run",
}


def _add_integrity_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--integrity-gated",
        action="store_true",
        help=(
            "Exclude invalid, duplicate, and out-of-order capture rows through "
            "the deterministic internal integrity gate before analysis."
        ),
    )
    parser.add_argument(
        "--require-integrity-safe",
        action="store_true",
        help=(
            "Fail closed when raw capture integrity issues exist unless "
            "--integrity-gated is used."
        ),
    )


class _IntegrityManifestWriter:
    def __init__(self, wrapped: TextIO, manifest: CaptureIntegrityManifest) -> None:
        self._wrapped = wrapped
        self._manifest = manifest
        self._started = False

    def write(self, value: str) -> int:
        if not self._started:
            self._started = True
            prefix = render_integrity_manifest(self._manifest)
            if self._manifest.integrity_mode == "raw" and self._manifest.block_reasons:
                prefix += (
                    "integrity_warning: raw capture contains integrity issues; "
                    "use --integrity-gated for canonical OOS/checkpoint diagnostics "
                    "or --require-integrity-safe to fail closed.\n"
                )
            self._wrapped.write(prefix)
        return self._wrapped.write(value)

    def flush(self) -> None:
        self._wrapped.flush()


def build_parser() -> argparse.ArgumentParser:
    from importlib import metadata as _metadata

    parser = argparse.ArgumentParser(
        prog="verifier",
        description="Pre-registered, calibrated verification of mechanical trading rules (research diagnostics only).",
    )
    try:
        _version = _metadata.version("strategy-verifier")
    except _metadata.PackageNotFoundError:
        _version = "unknown"
    parser.add_argument(
        "--version", action="version",
        version=f"verifier {_version}",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)


    klines_parser = subcommands.add_parser(
        "convert-binance-um-klines",
        help=(
            "Convert a Binance UM klines CSV or monthly zip of any "
            "interval into a one-event-per-bar capture JSONL."
        ),
    )
    klines_parser.add_argument(
        "--input", required=True, type=Path,
        help="Kline CSV or monthly zip path.",
    )
    klines_parser.add_argument(
        "--output", required=True, type=Path,
        help="Output capture JSONL path.",
    )
    klines_parser.add_argument(
        "--symbol", required=True,
        help="Symbol recorded on every event, e.g. BTCUSDT.",
    )
    klines_parser.add_argument(
        "--overwrite", action="store_true",
        help="Allow replacing an existing output file.",
    )
    klines_parser.add_argument(
        "--bar-path", choices=("close", "ohlc"), default="close",
        help=(
            "close: one event per bar at the close (legacy). ohlc: "
            "four events per bar in the fixed order open, high, low, "
            "close; volume rides the close leg."
        ),
    )

    verify_register_parser = subcommands.add_parser(
        "verify-register",
        help=(
            "Register an admitted claim: write the definition file "
            "and the registration document together, before any "
            "data. Exit 0 registered, 2 claim refused."
        ),
    )
    verify_register_parser.add_argument(
        "--file", required=True, type=Path,
        help="Registration TOML with [claim] and [registration].",
    )
    verify_register_parser.add_argument(
        "--definitions-dir", type=Path, default=Path("definitions"),
        help="Directory receiving the definition file.",
    )
    verify_register_parser.add_argument(
        "--docs-dir", type=Path, default=Path("docs"),
        help="Directory receiving the registration document.",
    )
    verify_register_parser.add_argument(
        "--state-dir", type=Path, default=Path("state"),
        help="Directory receiving the lifecycle state file.",
    )

    verify_advance_parser = subcommands.add_parser(
        "verify-advance",
        help=(
            "Advance a family's lifecycle stage; order violations are "
            "refused with the allowed next moves."
        ),
    )
    verify_advance_parser.add_argument(
        "--family", required=True, help="Registered family name."
    )
    verify_advance_parser.add_argument(
        "--to", required=True,
        help="Target stage: calibrated, explored, frozen, confirmed, closed.",
    )
    verify_advance_parser.add_argument(
        "--note", default="",
        help="Required memo for frozen, confirmed, and closed.",
    )
    verify_advance_parser.add_argument(
        "--state-dir", type=Path, default=Path("state"),
        help="Lifecycle state directory.",
    )

    verify_status_parser = subcommands.add_parser(
        "verify-status",
        help="Show a family's stage, history, and allowed next moves.",
    )
    verify_status_parser.add_argument(
        "--family", default=None,
        help="Family name; omit with --all to list every family.",
    )
    verify_status_parser.add_argument(
        "--all", action="store_true", help="List all families."
    )
    verify_status_parser.add_argument(
        "--state-dir", type=Path, default=Path("state"),
        help="Lifecycle state directory.",
    )

    verify_triage_parser = subcommands.add_parser(
        "verify-triage",
        help=(
            "Triage a claim file: admit it toward registration or "
            "refuse with named reasons. Exit 0 admissible, 2 refused."
        ),
    )
    verify_triage_parser.add_argument(
        "--claim", required=True, type=Path,
        help="Claim TOML path with a single [claim] table.",
    )

    verify_attest_parser = subcommands.add_parser(
        "verify-attest",
        help=(
            "Bind market, technique, order, and report by content "
            "hash; with --check, re-derive and name the changed term. "
            "Check exit: 0 identical, 2 mismatch."
        ),
    )
    verify_attest_parser.add_argument(
        "--definition", required=True, type=Path,
        help="Definition file (the technique term).",
    )
    verify_attest_parser.add_argument(
        "--input", required=True, nargs="+", type=Path,
        help="Capture inputs (the market term).",
    )
    verify_attest_parser.add_argument(
        "--order", required=True,
        help="The verbatim ask (the order term).",
    )
    verify_attest_parser.add_argument(
        "--report", required=True, type=Path,
        help="The produced report (the result term).",
    )
    verify_attest_parser.add_argument(
        "--check", type=Path, default=None,
        help="Existing attestation to verify against.",
    )

    verify_diagnose_parser = subcommands.add_parser(
        "verify-diagnose",
        help=(
            "Render the anatomy of a measurement: where the technique "
            "is strong, where it breaks, and what the shape means."
        ),
    )
    verify_diagnose_parser.add_argument(
        "--family", required=True, help="Family name."
    )
    verify_diagnose_parser.add_argument(
        "--report", required=True, nargs="+", type=Path,
        help="Measurement report paths, one per market.",
    )
    verify_diagnose_parser.add_argument(
        "--primary-horizon", required=True, type=int,
        help="Primary cell horizon from the registration.",
    )
    verify_diagnose_parser.add_argument(
        "--primary-threshold", required=True,
        help="Primary cell threshold from the registration.",
    )
    verify_diagnose_parser.add_argument(
        "--arm", default="base_grid",
        help="Report section carrying the main arm.",
    )
    verify_diagnose_parser.add_argument(
        "--control-arm", default=None,
        help="Report section carrying the control, if any.",
    )
    verify_diagnose_parser.add_argument(
        "--side-arms", default=None,
        help="Two sections split by side, comma separated, e.g. "
        "side=long,side=short.",
    )
    verify_diagnose_parser.add_argument(
        "--split", action="append", default=[],
        help="A split pair a:b; repeatable, e.g. "
        "regime=above:regime=below.",
    )

    verify_verdict_parser = subcommands.add_parser(
        "verify-verdict",
        help=(
            "Apply the registered bar to measurement reports and "
            "render the verdict. Exit 0 confirmed, 2 not confirmed."
        ),
    )
    verify_verdict_parser.add_argument(
        "--family", required=True, help="Registered family name."
    )
    verify_verdict_parser.add_argument(
        "--report", required=True, nargs="+", type=Path,
        help="Measurement report paths, one per market.",
    )
    verify_verdict_parser.add_argument(
        "--primary-horizon", required=True, type=int,
        help="Primary cell horizon from the registration.",
    )
    verify_verdict_parser.add_argument(
        "--primary-threshold", required=True,
        help="Primary cell threshold from the registration, e.g. 0.01.",
    )
    verify_verdict_parser.add_argument(
        "--arm", default="base_grid",
        help="Report section carrying the frozen arm.",
    )
    verify_verdict_parser.add_argument(
        "--control-arm", default=None,
        help=(
            "Report section carrying the control; omitted means the "
            "bar runs against the coin."
        ),
    )

    record_parser = subcommands.add_parser(
        "record",
        help=(
            "Record a Binance UM aggTrade stream as capture JSONL, "
            "byte-identical to the converter's output; each "
            "reconnect opens a new -sN session file and existing "
            "files are never overwritten."
        ),
    )
    record_parser.add_argument("--symbol", required=True)
    record_parser.add_argument(
        "--output", required=True, type=Path,
        help="Base capture path, e.g. data/captures/btcusdt-live.jsonl",
    )
    record_parser.add_argument(
        "--duration-seconds", type=int, default=None,
    )
    record_parser.add_argument(
        "--max-messages", type=int, default=None,
    )
    record_parser.add_argument(
        "--host", default="fstream.binance.com",
        help="Stream host, host or host:port (default UM futures).",
    )
    record_parser.add_argument(
        "--replay-file", type=Path, default=None,
        help=(
            "Deterministic test seam: read raw aggTrade JSON messages "
            "(one per line, __gap__ forces a session rotation) "
            "instead of the network."
        ),
    )

    serve_parser = subcommands.add_parser(
        "serve",
        help=(
            "Cloud-resident mode: one process that records a live "
            "stream, serves the web UI on the given bind and port, "
            "and auto-attaches the workspace so a redeploy resumes "
            "by itself. A fresh dated capture file is opened every "
            "boot; nothing is ever overwritten. Set "
            "VERIFIER_WEB_PASSWORD to require Basic Auth."
        ),
    )
    serve_parser.add_argument("--dir", type=Path, required=True)
    serve_parser.add_argument("--symbol", default=None)
    serve_parser.add_argument("--stream-host", default=None)
    serve_parser.add_argument("--definition", default=None)
    serve_parser.add_argument("--bind", default=None)
    serve_parser.add_argument("--port", type=int, default=None)

    ui_parser = subcommands.add_parser(
        "ui",
        help=(
            "Serve the local web UI (loopback only) over a working "
            "directory: the status board and the claim front door in "
            "part one."
        ),
    )
    ui_parser.add_argument(
        "--dir", type=Path, default=Path("."),
        help="Working directory (an init-scaffolded folder).",
    )
    ui_parser.add_argument(
        "--port", type=int, default=8787,
        help="Port on 127.0.0.1 (0 picks an ephemeral port).",
    )
    ui_parser.add_argument(
        "--no-browser", action="store_true",
        help="Do not open a browser automatically.",
    )

    init_parser = subcommands.add_parser(
        "init",
        help=(
            "Scaffold a working directory from the packaged product "
            "resources: demo definitions, the user guide, the "
            "disclaimer, the charter, the license, and empty data, "
            "reports, claims, and state directories. Existing files "
            "are never overwritten."
        ),
    )
    init_parser.add_argument(
        "--dir", type=Path, default=Path("."),
        help="Target working directory (created if missing).",
    )

    verify_certificate_parser = subcommands.add_parser(
        "verify-certificate",
        help=(
            "Fold a verdict report and a diagnosis report into one "
            "A4 print-ready HTML certificate; any browser saves it "
            "as PDF."
        ),
    )
    verify_certificate_parser.add_argument(
        "--family", required=True, help="Family name on the papers."
    )
    verify_certificate_parser.add_argument(
        "--verdict-report", required=True, type=Path,
        help="verify-verdict output file.",
    )
    verify_certificate_parser.add_argument(
        "--diagnosis-report", required=True, type=Path,
        help="verify-diagnose output file.",
    )
    verify_certificate_parser.add_argument(
        "--output", required=True, type=Path,
        help="Certificate HTML path to write.",
    )

    verify_read_parser = subcommands.add_parser(
        "verify-read",
        help=(
            "State reading: the occurrence points of a graph "
            "definition on the given data with resolved outcomes, "
            "the latest bars' condition states, and the family's "
            "adjudication always attached. Description only."
        ),
    )
    verify_read_parser.add_argument(
        "--definition", required=True, type=Path,
        help="Graph definition TOML path.",
    )
    verify_read_parser.add_argument(
        "--input", required=True, nargs="+", type=Path,
        help="Capture JSONL paths.",
    )
    verify_read_parser.add_argument(
        "--last-bars", type=int, default=3,
        help="How many latest bars to snapshot.",
    )
    verify_read_parser.add_argument(
        "--state-dir", type=Path, default=Path("state"),
        help="Lifecycle state directory for the adjudication block.",
    )
    verify_read_parser.add_argument(
        "--input-session-mode", choices=("prefix", "single"),
        default="prefix",
        help="Capture session mode.",
    )
    verify_read_parser.add_argument(
        "--integrity-gated", action="store_true",
        help="Run inputs through the integrity gate first.",
    )
    verify_read_parser.add_argument(
        "--require-integrity-safe", action="store_true",
        help="Refuse inputs that fail the integrity gate.",
    )

    verify_run_parser = subcommands.add_parser(
        "verify-run",
        help=(
            "Run a verifier definition file through its registered "
            "engine and print the report."
        ),
    )
    verify_run_parser.add_argument(
        "--definition", required=True, type=Path,
        help="Verifier definition TOML path.",
    )
    verify_run_parser.add_argument(
        "--input", required=True, nargs="+", type=Path,
        help="Capture JSONL input paths.",
    )
    verify_run_parser.add_argument(
        "--calibrate-only", action="store_true",
        help=(
            "Graph engine only: report the calibrate-series "
            "percentile ladder over directional bars, no outcomes."
        ),
    )
    verify_run_parser.add_argument(
        "--input-session-mode",
        choices=("file", "prefix", "single"),
        default="file",
        help="Capture grouping mode for the integrity gate.",
    )
    _add_integrity_args(verify_run_parser)


    daily_klines_parser = subcommands.add_parser(
        "convert-binance-um-daily-klines",
        help=(
            "Convert a Binance UM daily klines CSV or monthly zip into "
            "a one-event-per-day capture JSONL."
        ),
    )
    daily_klines_parser.add_argument(
        "--input", required=True, type=Path,
        help="Kline CSV or monthly zip path.",
    )
    daily_klines_parser.add_argument(
        "--output", required=True, type=Path,
        help="Output capture JSONL path.",
    )
    daily_klines_parser.add_argument(
        "--symbol", required=True,
        help="Symbol recorded on every event, e.g. BTCUSDT.",
    )
    daily_klines_parser.add_argument(
        "--overwrite", action="store_true",
        help="Allow replacing an existing output file.",
    )


    convert_binance_parser = subcommands.add_parser(
        "convert-binance-um-aggtrades",
        help=(
            "Convert a local Binance USDT-M futures aggTrades history file "
            "(.csv or .zip) to public trade capture JSONL."
        ),
    )
    convert_binance_parser.add_argument(
        "--input",
        required=True,
        type=Path,
        help="Local Binance aggTrades daily history file (.csv or .zip).",
    )
    convert_binance_parser.add_argument(
        "--output",
        required=True,
        type=Path,
        help="Local capture JSONL output path (.jsonl).",
    )
    convert_binance_parser.add_argument(
        "--symbol",
        required=True,
        help="Instrument symbol for the converted events, for example BTCUSDT.",
    )
    convert_binance_parser.add_argument(
        "--fail-on-malformed",
        action="store_true",
        help="Fail closed on the first malformed row instead of skipping it.",
    )
    convert_binance_parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace the output file if it already exists.",
    )

    subcommands.add_parser(
        "safety-review",
        help="Scan package source for disallowed modules and dangerous terms.",
    )
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    out = stdout or sys.stdout
    err = stderr or sys.stderr
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code)

    if args.command in _CAPTURE_INPUT_COMMANDS:
        input_session_mode = getattr(args, "input_session_mode", "prefix")
        try:
            prepared_context = prepare_capture_inputs(
                input_paths=tuple(args.input),
                integrity_gated=args.integrity_gated,
                require_integrity_safe=args.require_integrity_safe,
                input_session_mode=input_session_mode,
            )
            prepared = prepared_context.__enter__()
            atexit.register(prepared_context.__exit__, None, None, None)
            args.input = list(prepared.input_paths)
            out = _IntegrityManifestWriter(out, prepared.manifest)
        except (DomainValidationError, ValueError, OSError) as exc:
            err.write(f"error: {exc}\n")
            return 1

    if args.command == "convert-binance-um-klines":
        try:
            result = run_binance_um_klines_conversion(
                BinanceUmKlinesConfig(
                    input_path=args.input,
                    output_path=args.output,
                    symbol=args.symbol,
                    overwrite=args.overwrite,
                    bar_path=args.bar_path,
                )
            )
        except (DomainValidationError, OSError, ValueError) as exc:
            err.write(f"error: {exc}\n")
            return 1
        out.write(
            "Binance UM Klines Conversion Report\n"
            f"input: {result.input_path}\n"
            f"output: {result.output_path}\n"
            f"row_count: {result.row_count}\n"
            f"converted_count: {result.converted_count}\n"
            f"malformed_count: {result.malformed_count}\n"
            f"out_of_order_count: {result.out_of_order_count}\n"
        )
        return 0
    if args.command == "verify-advance":
        try:
            state = advance_family_state(
                args.state_dir, args.family, args.to, args.note
            )
            rendered = render_family_status(state)
        except (DomainValidationError, OSError, ValueError) as exc:
            err.write(f"error: {exc}\n")
            return 1
        out.write(rendered)
        return 0
    if args.command == "verify-status":
        try:
            if args.all:
                states = list_family_states(args.state_dir)
                rendered = (
                    "\n".join(
                        render_family_status(state) for state in states
                    )
                    if states
                    else "no registered families\n"
                )
            elif args.family:
                rendered = render_family_status(
                    load_family_state(args.state_dir, args.family)
                )
            else:
                err.write("error: provide --family or --all\n")
                return 1
        except (DomainValidationError, OSError, ValueError) as exc:
            err.write(f"error: {exc}\n")
            return 1
        out.write(rendered)
        return 0
    if args.command == "verify-attest":
        try:
            if args.check is not None:
                matched, mismatched = check_attestation(
                    args.check, args.definition, args.input,
                    args.order, args.report,
                )
                if matched:
                    out.write(
                        "attestation check: identical terms, "
                        "identical result\n"
                    )
                    return 0
                out.write(
                    "attestation check: changed terms: "
                    + ", ".join(mismatched) + "\n"
                )
                return 2
            rendered = render_attestation(
                build_attestation(
                    args.definition, args.input, args.order, args.report
                )
            )
        except (DomainValidationError, OSError, ValueError) as exc:
            err.write(f"error: {exc}\n")
            return 1
        out.write(rendered)
        return 0
    if args.command == "verify-diagnose":
        try:
            side_arms = None
            if args.side_arms:
                pieces = [
                    piece.strip() for piece in args.side_arms.split(",")
                ]
                if len(pieces) != 2 or not all(pieces):
                    raise DomainValidationError(
                        "side-arms must be two sections, comma separated"
                    )
                side_arms = (pieces[0], pieces[1])
            split_pairs = []
            for pair_text in args.split:
                halves = [half.strip() for half in pair_text.split(":")]
                if len(halves) != 2 or not all(halves):
                    raise DomainValidationError(
                        f"split must look like a:b, got '{pair_text}'"
                    )
                split_pairs.append((halves[0], halves[1]))
            result = run_diagnosis(
                family=args.family,
                report_paths=args.report,
                primary_horizon=args.primary_horizon,
                primary_threshold=args.primary_threshold,
                arm=args.arm,
                control_arm=args.control_arm,
                side_arms=side_arms,
                split_pairs=tuple(split_pairs),
            )
            rendered = render_diagnosis_report(result)
            rendered += render_equation_block(
                market_paths=list(args.report),
                technique_path=None,
                order=(
                    "verify-diagnose family="
                    f"{args.family} cell={args.primary_horizon}/"
                    f"{args.primary_threshold} arm={args.arm}"
                ),
                result_body=rendered,
            )
        except (DomainValidationError, OSError, ValueError) as exc:
            err.write(f"error: {exc}\n")
            return 1
        out.write(rendered)
        return 0
    if args.command == "verify-verdict":
        try:
            result = run_verdict(
                family=args.family,
                report_paths=args.report,
                horizon=args.primary_horizon,
                threshold=args.primary_threshold,
                arm=args.arm,
                control_arm=args.control_arm,
            )
            rendered = render_verdict_report(result)
            rendered += render_equation_block(
                market_paths=list(args.report),
                technique_path=None,
                order=(
                    "verify-verdict family="
                    f"{args.family} cell={args.primary_horizon}/"
                    f"{args.primary_threshold} arm={args.arm} "
                    f"control={args.control_arm}"
                ),
                result_body=rendered,
            )
        except (DomainValidationError, OSError, ValueError) as exc:
            err.write(f"error: {exc}\n")
            return 1
        out.write(rendered)
        return 0 if result.passed else 2
    if args.command == "verify-register":
        try:
            triage_result, registration = run_registration(
                args.file,
                definitions_dir=args.definitions_dir,
                docs_dir=args.docs_dir,
                state_dir=args.state_dir,
            )
            rendered = render_registration_report(
                triage_result, registration
            )
        except (DomainValidationError, OSError, ValueError) as exc:
            err.write(f"error: {exc}\n")
            return 1
        out.write(rendered)
        return 0 if registration is not None else 2
    if args.command == "verify-triage":
        try:
            result = run_claim_triage(args.claim)
            rendered = render_claim_triage_report(result)
        except (DomainValidationError, OSError, ValueError) as exc:
            err.write(f"error: {exc}\n")
            return 1
        out.write(rendered)
        return 0 if result.admissible else 2
    if args.command == "record":
        from strategy_verifier.verifier.recorder import run_record

        try:
            summary = run_record(
                symbol=args.symbol,
                output=args.output,
                duration_seconds=args.duration_seconds,
                max_messages=args.max_messages,
                replay_file=args.replay_file,
                host=args.host,
            )
        except (DomainValidationError, OSError, ValueError) as exc:
            err.write(f"error: {exc}\n")
            return 1
        out.write(
            "Recording finished\n"
            f"sessions: {summary.sessions}\n"
            f"events: {summary.events}\n"
            + "".join(f"- {name}\n" for name in summary.files)
            + "each session file is a capture; feed them with "
            "--input-session-mode prefix\n"
        )
        return 0
    if args.command == "serve":
        from strategy_verifier.ui.serve_mode import run_serve

        return run_serve(
            workdir=args.dir,
            symbol=args.symbol,
            stream_host=args.stream_host,
            definition=args.definition,
            bind=args.bind,
            port=args.port,
            out=out,
        )
    if args.command == "ui":
        from strategy_verifier.ui.server import run_ui

        try:
            return run_ui(
                workdir=args.dir,
                port=args.port,
                open_browser=not args.no_browser,
            )
        except OSError as exc:
            err.write(f"error: {exc}\n")
            return 1
    if args.command == "init":
        try:
            from importlib import resources as _resources

            root = _resources.files("strategy_verifier") / "product_resources"
            target = args.dir
            written: list[str] = []
            skipped: list[str] = []

            def _copy_tree(source, destination: Path) -> None:
                for item in source.iterdir():
                    out_path = destination / item.name
                    if item.is_dir():
                        out_path.mkdir(parents=True, exist_ok=True)
                        _copy_tree(item, out_path)
                    else:
                        if out_path.exists():
                            skipped.append(str(out_path))
                            continue
                        out_path.parent.mkdir(parents=True, exist_ok=True)
                        out_path.write_bytes(item.read_bytes())
                        written.append(str(out_path))

            for sub in ("definitions", "docs", "claims", "data"):
                (target / sub).mkdir(parents=True, exist_ok=True)
                _copy_tree(root / sub, target / sub)
            license_out = target / "LICENSE"
            if license_out.exists():
                skipped.append(str(license_out))
            else:
                license_out.parent.mkdir(parents=True, exist_ok=True)
                license_out.write_bytes((root / "LICENSE").read_bytes())
                written.append(str(license_out))
            for empty in ("data/captures", "reports", "state"):
                (target / empty).mkdir(parents=True, exist_ok=True)
        except (OSError, ValueError, DomainValidationError) as exc:
            err.write(f"error: {exc}\n")
            return 1
        out.write(
            "Verifier workspace initialized\n"
            f"dir: {target}\n"
            f"written: {len(written)} skipped_existing: {len(skipped)}\n"
            "start here: docs/USER_GUIDE.md (installation is done; "
            "begin at section 2)\n"
            "try the demo pair: verify-run --definition "
            "definitions/demo_quickstart.toml --input "
            "data/captures/demo-900s-1.jsonl --input-session-mode single\n"
            "boundaries: docs/DISCLAIMER.md and LICENSE apply\n"
        )
        return 0
    if args.command == "verify-certificate":
        try:
            for path in (args.verdict_report, args.diagnosis_report):
                if not path.exists():
                    raise DomainValidationError(
                        f"report does not exist: {path}"
                    )
            result = render_certificate(
                family=args.family,
                verdict_text=args.verdict_report.read_text(
                    encoding="utf-8"
                ),
                diagnosis_text=args.diagnosis_report.read_text(
                    encoding="utf-8"
                ),
                output_path=args.output,
            )
        except (DomainValidationError, OSError, ValueError) as exc:
            err.write(f"error: {exc}\n")
            return 1
        out.write(
            "Certificate written\n"
            f"family: {result.family}\n"
            f"verdict: {result.verdict_name}\n"
            f"output: {result.output_path}\n"
            "print to PDF from any browser; the equation hashes tie "
            "the paper to its reports\n"
        )
        return 0
    if args.command == "verify-read":
        try:
            definition = load_verifier_definition(args.definition)
            reading = run_reading(
                definition,
                input_paths=args.input,
                input_session_mode=args.input_session_mode,
                last_bars=args.last_bars,
                state_dir=args.state_dir,
            )
            rendered = render_reading_report(reading)
            rendered += render_equation_block(
                market_paths=list(args.input),
                technique_path=args.definition,
                order=(
                    "verify-read session="
                    f"{args.input_session_mode} last_bars="
                    f"{args.last_bars} inputs={len(args.input)}"
                ),
                result_body=rendered,
            )
        except (DomainValidationError, InvalidOperation, ValueError, OSError) as exc:
            err.write(f"error: {exc}\n")
            return 1
        out.write(rendered)
        return 0
    if args.command == "verify-run":
        try:
            definition = load_verifier_definition(args.definition)
            rendered = run_verifier_definition(
                definition,
                input_paths=args.input,
                input_session_mode=args.input_session_mode,
                calibrate_only=args.calibrate_only,
            )
            rendered += render_equation_block(
                market_paths=list(args.input),
                technique_path=args.definition,
                order=(
                    "verify-run session="
                    f"{args.input_session_mode} calibrate="
                    f"{args.calibrate_only} inputs={len(args.input)}"
                ),
                result_body=rendered,
            )
        except (DomainValidationError, InvalidOperation, ValueError, OSError) as exc:
            err.write(f"error: {exc}\n")
            return 1
        out.write(rendered)
        return 0
    if args.command == "convert-binance-um-daily-klines":
        try:
            result = run_binance_um_daily_klines_conversion(
                BinanceUmDailyKlinesConfig(
                    input_path=args.input,
                    output_path=args.output,
                    symbol=args.symbol,
                    overwrite=args.overwrite,
                )
            )
        except (DomainValidationError, OSError, ValueError) as exc:
            err.write(f"error: {exc}\n")
            return 1
        out.write(
            "Binance UM Daily Klines Conversion Report\n"
            f"input: {result.input_path}\n"
            f"output: {result.output_path}\n"
            f"row_count: {result.row_count}\n"
            f"converted_count: {result.converted_count}\n"
            f"malformed_count: {result.malformed_count}\n"
            f"out_of_order_count: {result.out_of_order_count}\n"
        )
        return 0
    if args.command == "convert-binance-um-aggtrades":
        return _run_convert_binance_um_aggtrades(
            input_path=args.input,
            output_path=args.output,
            symbol=args.symbol,
            fail_on_malformed=args.fail_on_malformed,
            overwrite=args.overwrite,
            stdout=out,
            stderr=err,
        )
    if args.command == "safety-review":
        return _run_safety_review(out, err)

    parser.error("unknown command")
    return 2


def _run_safety_review(stdout: TextIO, stderr: TextIO) -> int:
    result = run_safety_review()
    if result.passed:
        stdout.write("safety review passed\n")
        stdout.write(f"scanned_files={result.scanned_files}\n")
        return 0

    stderr.write("safety review failed\n")
    for finding in result.findings:
        stderr.write(f"- {finding.path}: {finding.reason}\n")
    return 1


def _run_convert_binance_um_aggtrades(
    *,
    input_path: Path,
    output_path: Path,
    symbol: str,
    fail_on_malformed: bool,
    overwrite: bool,
    stdout: TextIO,
    stderr: TextIO,
) -> int:
    try:
        result = run_binance_um_aggtrade_history_conversion(
            BinanceUmAggTradeHistoryConfig(
                input_path=input_path,
                output_path=output_path,
                symbol=symbol,
                fail_on_malformed=fail_on_malformed,
                overwrite=overwrite,
            )
        )
        rendered = render_binance_um_aggtrade_history_report(result)
    except (DomainValidationError, ValueError, OSError) as exc:
        stderr.write(f"error: {exc}\n")
        return 1

    stdout.write(rendered)
    return 0


