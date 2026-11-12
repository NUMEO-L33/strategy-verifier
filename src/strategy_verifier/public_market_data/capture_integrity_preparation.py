"""Canonical capture input preparation for diagnostics-only analysis."""

from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from strategy_verifier.domain.errors import DomainValidationError
from strategy_verifier.public_market_data.capture_integrity_diagnostics import (
    CaptureIntegrityDiagnosticsConfig,
    CaptureIntegrityDiagnosticsResult,
    run_capture_integrity_diagnostics,
)
from strategy_verifier.public_market_data.capture_session_continuity import (
    capture_session_group_id,
    validate_input_session_mode,
)
from strategy_verifier.public_market_data.public_trade_capture_reader import (
    PublicTradeCaptureReadConfig,
    PublicTradeCaptureStreamCounters,
    _record_to_event,
    iter_public_trade_capture_events,
)


INTEGRITY_MODES = ("raw", "gated")
CANONICAL_FILE_ORDERING_POLICY = "path_name_lexicographic"


@dataclass(frozen=True, slots=True)
class CaptureIntegrityManifest:
    integrity_mode: str
    input_session_mode: str
    canonical_file_ordering_policy: str
    raw_input_file_count: int
    raw_valid_event_count: int
    invalid_line_count: int
    duplicate_issue_count: int
    out_of_order_issue_count: int
    unique_excluded_issue_event_count: int
    gated_clean_event_count: int
    first_timestamp: str | None
    last_timestamp: str | None
    raw_input_manifest_sha256: str
    gated_event_stream_sha256: str | None
    block_reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.integrity_mode not in INTEGRITY_MODES:
            raise DomainValidationError("integrity_mode must be raw or gated")
        validate_input_session_mode(self.input_session_mode)
        if self.canonical_file_ordering_policy != CANONICAL_FILE_ORDERING_POLICY:
            raise DomainValidationError("unsupported canonical ordering policy")
        for value, name in (
            (self.raw_input_file_count, "raw_input_file_count"),
            (self.raw_valid_event_count, "raw_valid_event_count"),
            (self.invalid_line_count, "invalid_line_count"),
            (self.duplicate_issue_count, "duplicate_issue_count"),
            (self.out_of_order_issue_count, "out_of_order_issue_count"),
            (
                self.unique_excluded_issue_event_count,
                "unique_excluded_issue_event_count",
            ),
            (self.gated_clean_event_count, "gated_clean_event_count"),
        ):
            _require_non_negative_int(value, name)
        if not isinstance(self.raw_input_manifest_sha256, str):
            raise DomainValidationError("raw_input_manifest_sha256 must be a string")
        if self.gated_event_stream_sha256 is not None and not isinstance(
            self.gated_event_stream_sha256,
            str,
        ):
            raise DomainValidationError("gated_event_stream_sha256 must be a string")
        if not isinstance(self.block_reasons, tuple) or not all(
            isinstance(reason, str) for reason in self.block_reasons
        ):
            raise DomainValidationError("block_reasons must contain strings")


@dataclass(frozen=True, slots=True)
class PreparedCaptureInputs:
    input_paths: tuple[Path, ...]
    manifest: CaptureIntegrityManifest

    def __post_init__(self) -> None:
        if not isinstance(self.input_paths, tuple) or not self.input_paths:
            raise DomainValidationError("input_paths must be a non-empty tuple")
        if not all(isinstance(path, Path) for path in self.input_paths):
            raise DomainValidationError("input_paths must contain Path")
        if not isinstance(self.manifest, CaptureIntegrityManifest):
            raise DomainValidationError("manifest must be CaptureIntegrityManifest")


class PreparedCaptureInputContext(AbstractContextManager[PreparedCaptureInputs]):
    def __init__(
        self,
        *,
        input_paths: tuple[Path, ...],
        integrity_gated: bool,
        require_integrity_safe: bool,
        input_session_mode: str,
    ) -> None:
        validate_input_session_mode(input_session_mode)
        if not isinstance(input_paths, tuple) or not input_paths:
            raise DomainValidationError("input_paths must be a non-empty tuple")
        if not all(isinstance(path, Path) for path in input_paths):
            raise DomainValidationError("input_paths must contain Path")
        if not isinstance(integrity_gated, bool):
            raise DomainValidationError("integrity_gated must be a bool")
        if not isinstance(require_integrity_safe, bool):
            raise DomainValidationError("require_integrity_safe must be a bool")
        self._raw_paths = tuple(sorted(input_paths, key=_path_sort_key))
        self._integrity_gated = integrity_gated
        self._require_integrity_safe = require_integrity_safe
        self._input_session_mode = input_session_mode
        self._temporary_directory: TemporaryDirectory[str] | None = None
        self._prepared: PreparedCaptureInputs | None = None

    def __enter__(self) -> PreparedCaptureInputs:
        diagnostics = _run_diagnostics(self._raw_paths)
        manifest_base = _manifest_base(
            raw_paths=self._raw_paths,
            diagnostics=diagnostics,
            input_session_mode=self._input_session_mode,
        )
        if (
            self._require_integrity_safe
            and not self._integrity_gated
            and diagnostics.block_reasons
        ):
            raise DomainValidationError(
                "capture integrity issues found; rerun with --integrity-gated "
                "or remove --require-integrity-safe"
            )
        if not self._integrity_gated:
            self._prepared = PreparedCaptureInputs(
                input_paths=self._raw_paths,
                manifest=CaptureIntegrityManifest(
                    **manifest_base,
                    integrity_mode="raw",
                    gated_event_stream_sha256=None,
                ),
            )
            return self._prepared

        self._temporary_directory = TemporaryDirectory(
            prefix="verifier_integrity_gated_",
        )
        output_dir = Path(self._temporary_directory.name)
        if (
            diagnostics.issue_count == 0
            and diagnostics.invalid_line_count == 0
        ):
            # Zero-copy clean passthrough: with no issues and no invalid
            # lines, the gated stream is byte-identical to the raw files
            # in order, so nothing is materialized. The stream hash is
            # computed by the same normalization the writer would apply,
            # and downstream consumers iterate the raw paths with
            # continuous state, which is exactly the single-session
            # semantics of one concatenated gated file.
            gated_paths = self._raw_paths
            gated_sha = _clean_stream_sha(self._raw_paths)
        else:
            gated_paths, gated_sha = _write_gated_files(
                raw_paths=self._raw_paths,
                diagnostics=diagnostics,
                output_dir=output_dir,
                input_session_mode=self._input_session_mode,
            )
        self._prepared = PreparedCaptureInputs(
            input_paths=gated_paths,
            manifest=CaptureIntegrityManifest(
                **manifest_base,
                integrity_mode="gated",
                gated_event_stream_sha256=gated_sha,
            ),
        )
        return self._prepared

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        if self._temporary_directory is not None:
            self._temporary_directory.cleanup()
            self._temporary_directory = None


def prepare_capture_inputs(
    *,
    input_paths: tuple[Path, ...],
    integrity_gated: bool,
    require_integrity_safe: bool,
    input_session_mode: str,
) -> PreparedCaptureInputContext:
    return PreparedCaptureInputContext(
        input_paths=input_paths,
        integrity_gated=integrity_gated,
        require_integrity_safe=require_integrity_safe,
        input_session_mode=input_session_mode,
    )


def render_integrity_manifest(manifest: CaptureIntegrityManifest) -> str:
    lines = [
        "capture_integrity_manifest:",
        f"  integrity_mode: {manifest.integrity_mode}",
        f"  input_session_mode: {manifest.input_session_mode}",
        (
            "  canonical_file_ordering_policy: "
            f"{manifest.canonical_file_ordering_policy}"
        ),
        f"  raw_input_file_count: {manifest.raw_input_file_count}",
        f"  raw_valid_event_count: {manifest.raw_valid_event_count}",
        f"  invalid_line_count: {manifest.invalid_line_count}",
        f"  duplicate_issue_count: {manifest.duplicate_issue_count}",
        f"  out_of_order_issue_count: {manifest.out_of_order_issue_count}",
        (
            "  unique_excluded_issue_event_count: "
            f"{manifest.unique_excluded_issue_event_count}"
        ),
        f"  gated_clean_event_count: {manifest.gated_clean_event_count}",
        f"  first_timestamp: {manifest.first_timestamp or 'none'}",
        f"  last_timestamp: {manifest.last_timestamp or 'none'}",
        f"  raw_input_manifest_sha256: {manifest.raw_input_manifest_sha256}",
        (
            "  gated_event_stream_sha256: "
            f"{manifest.gated_event_stream_sha256 or 'none'}"
        ),
        "  block_reasons:",
    ]
    if manifest.block_reasons:
        lines.extend(f"  - {reason}" for reason in manifest.block_reasons)
    else:
        lines.append("  - none")
    return "\n".join(lines) + "\n"


def _run_diagnostics(
    raw_paths: tuple[Path, ...],
) -> CaptureIntegrityDiagnosticsResult:
    read_configs = tuple(
        PublicTradeCaptureReadConfig(input_path=path) for path in raw_paths
    )
    return run_capture_integrity_diagnostics(
        CaptureIntegrityDiagnosticsConfig(read_configs=read_configs)
    )


def _manifest_base(
    *,
    raw_paths: tuple[Path, ...],
    diagnostics: CaptureIntegrityDiagnosticsResult,
    input_session_mode: str,
) -> dict[str, object]:
    timestamps = _first_last_timestamps(raw_paths)
    return {
        "input_session_mode": input_session_mode,
        "canonical_file_ordering_policy": CANONICAL_FILE_ORDERING_POLICY,
        "raw_input_file_count": len(raw_paths),
        "raw_valid_event_count": diagnostics.valid_event_count,
        "invalid_line_count": diagnostics.invalid_line_count,
        "duplicate_issue_count": diagnostics.detected_duplicate_payload_count,
        "out_of_order_issue_count": diagnostics.detected_out_of_order_timestamp_count,
        "unique_excluded_issue_event_count": diagnostics.issue_event_count,
        "gated_clean_event_count": diagnostics.clean_event_count,
        "first_timestamp": timestamps[0],
        "last_timestamp": timestamps[1],
        "raw_input_manifest_sha256": _raw_manifest_sha(raw_paths),
        "block_reasons": tuple(diagnostics.block_reasons),
    }


def _clean_stream_sha(raw_paths: tuple[Path, ...]) -> str:
    """Hash the clean stream exactly as the gated writer would.

    Used only on the zero-issue path, where every non-empty line is a
    valid event and the writer would emit each line unchanged with a
    trailing newline, in raw path order.
    """
    stream_sha = sha256()
    for path in raw_paths:
        with path.open("r", encoding="utf-8") as file_obj:
            for raw_line in file_obj:
                if not raw_line.strip():
                    continue
                line = raw_line if raw_line.endswith("\n") else raw_line + "\n"
                stream_sha.update(line.encode("utf-8"))
    return stream_sha.hexdigest()


def _write_gated_files(
    *,
    raw_paths: tuple[Path, ...],
    diagnostics: CaptureIntegrityDiagnosticsResult,
    output_dir: Path,
    input_session_mode: str,
) -> tuple[tuple[Path, ...], str]:
    """Stream clean lines into per-group gated files with bounded memory.

    Lines flow file by file in raw path order directly into the gated
    output for their session group, and the stream hash updates in that
    same raw order, so the produced bytes and the recorded digest are
    identical to the retained in-memory reference.
    """
    issue_event_keys = {
        (issue.input_index, issue.line_number)
        for issue in diagnostics.issues
    }
    stream_sha = sha256()
    group_ids = [
        capture_session_group_id(path, input_session_mode)
        for path in raw_paths
    ]
    output_path_by_group: dict[str, Path] = {
        group_id: output_dir
        / f"{_safe_file_stem(group_id)}-gated-100-{group_index:03d}.jsonl"
        for group_index, group_id in enumerate(sorted(set(group_ids)), start=1)
    }
    handles = {}
    try:
        wrote_any = False
        for input_index, path in enumerate(raw_paths):
            group_id = group_ids[input_index]
            handle = handles.get(group_id)
            if handle is None:
                handle = output_path_by_group[group_id].open(
                    "w", encoding="utf-8"
                )
                handles[group_id] = handle
            valid_ordinal = 0
            with path.open("r", encoding="utf-8") as file_obj:
                for raw_line in file_obj:
                    if not raw_line.strip():
                        continue
                    try:
                        record = json.loads(
                            raw_line,
                            parse_float=Decimal,
                            parse_int=int,
                        )
                        _record_to_event(record)
                    except (
                        json.JSONDecodeError,
                        DomainValidationError,
                        TypeError,
                        ValueError,
                    ):
                        continue
                    valid_ordinal += 1
                    if (input_index, valid_ordinal) in issue_event_keys:
                        continue
                    line = (
                        raw_line
                        if raw_line.endswith("\n")
                        else raw_line + "\n"
                    )
                    handle.write(line)
                    stream_sha.update(line.encode("utf-8"))
                    wrote_any = True
    finally:
        for handle in handles.values():
            handle.close()
    if not wrote_any:
        raise DomainValidationError("integrity-gated input produced no clean events")
    output_paths = tuple(
        output_path_by_group[group_id]
        for group_id in sorted(output_path_by_group)
    )
    return output_paths, stream_sha.hexdigest()


def _write_gated_files_in_memory_reference(
    *,
    raw_paths: tuple[Path, ...],
    diagnostics: CaptureIntegrityDiagnosticsResult,
    output_dir: Path,
    input_session_mode: str,
) -> tuple[tuple[Path, ...], str]:
    """Whole-list reference retained only for equivalence tests."""
    issue_event_keys = {
        (issue.input_index, issue.line_number)
        for issue in diagnostics.issues
    }
    lines_by_group: dict[str, list[str]] = {}
    stream_sha = sha256()
    for input_index, path in enumerate(raw_paths):
        raw_nonempty_lines = _nonempty_lines(path)
        valid_ordinal = 0
        clean_lines: list[str] = []
        for raw_line in raw_nonempty_lines:
            try:
                record = json.loads(raw_line, parse_float=Decimal, parse_int=int)
                _record_to_event(record)
            except (json.JSONDecodeError, DomainValidationError, TypeError, ValueError):
                continue
            valid_ordinal += 1
            if (input_index, valid_ordinal) in issue_event_keys:
                continue
            line = raw_line if raw_line.endswith("\n") else raw_line + "\n"
            clean_lines.append(line)
            stream_sha.update(line.encode("utf-8"))
        group_id = capture_session_group_id(path, input_session_mode)
        lines_by_group.setdefault(group_id, []).extend(clean_lines)

    output_paths: list[Path] = []
    for group_index, group_id in enumerate(sorted(lines_by_group), start=1):
        safe_group_id = _safe_file_stem(group_id)
        output_path = output_dir / f"{safe_group_id}-gated-100-{group_index:03d}.jsonl"
        output_path.write_text("".join(lines_by_group[group_id]), encoding="utf-8")
        output_paths.append(output_path)
    if not output_paths:
        raise DomainValidationError("integrity-gated input produced no clean events")
    return tuple(output_paths), stream_sha.hexdigest()


def _nonempty_lines(path: Path) -> list[str]:
    lines: list[str] = []
    with path.open("r", encoding="utf-8") as file_obj:
        for raw_line in file_obj:
            if raw_line.strip():
                lines.append(raw_line)
    return lines


def _first_last_timestamps(raw_paths: tuple[Path, ...]) -> tuple[str | None, str | None]:
    first: str | None = None
    last: str | None = None
    for path in raw_paths:
        counters = PublicTradeCaptureStreamCounters()
        for event in iter_public_trade_capture_events(
            PublicTradeCaptureReadConfig(input_path=path),
            counters,
        ):
            if event.mapped_trade_tick is None:
                continue
            timestamp = event.mapped_trade_tick.timestamp.isoformat()
            if first is None:
                first = timestamp
            last = timestamp
    return first, last


def _raw_manifest_sha(raw_paths: tuple[Path, ...]) -> str:
    lines: list[str] = []
    for path in raw_paths:
        file_sha = sha256()
        byte_count = 0
        newline_count = 0
        ends_with_newline = False
        with path.open("rb") as file_obj:
            while True:
                chunk = file_obj.read(8 * 1024 * 1024)
                if not chunk:
                    break
                file_sha.update(chunk)
                byte_count += len(chunk)
                newline_count += chunk.count(b"\n")
                ends_with_newline = chunk.endswith(b"\n")
        line_count = 0
        if byte_count:
            line_count = newline_count + (0 if ends_with_newline else 1)
        lines.append(
            "|".join(
                (
                    str(path.resolve()),
                    str(line_count),
                    str(byte_count),
                    file_sha.hexdigest(),
                )
            )
        )
    return sha256("\n".join(lines).encode("utf-8")).hexdigest()


def _line_count(data: bytes) -> int:
    if not data:
        return 0
    count = data.count(b"\n")
    if not data.endswith(b"\n"):
        count += 1
    return count


def _path_sort_key(path: Path) -> str:
    return str(path.resolve())


def _safe_file_stem(value: str) -> str:
    safe = "".join(char if char.isalnum() or char in "-_" else "-" for char in value)
    return safe.strip("-") or "capture"


def _require_non_negative_int(value: int, field_name: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise DomainValidationError(f"{field_name} must be a non-negative integer")
