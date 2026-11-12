"""Condition-graph composer (engine graph_v1).

Definition files wire registry primitives directly: declared series
feed on close, volume, or earlier series; condition nodes compare,
cross, or count runs; a direction rule and a gated event assemble the
signal; stride controls ride directional bars; outcomes race on the
shared sweep. References may only point backward, every schema key is
strict, and calibrate mode histograms any declared series before
outcomes exist. A new family needs a file, not a module.

Registered semantics: series and conditions evaluate in declaration
order once per completed bar; the direction rule checks its long
conditions first; an event fires when every event condition holds,
the direction is nonzero, and the episode gate is open; on
non-event directional bars a stride counter drives controls, blocked
within the minimum distance of the last event. Run conditions may
evaluate before or after their own update, declared per node.

Research diagnostics only. No strategy logic, no orders, no trading
paths.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path

from strategy_verifier.domain.errors import DomainValidationError
from strategy_verifier.public_market_data.capture_session_continuity import (
    capture_session_group_id,
    validate_input_session_mode,
)
from strategy_verifier.public_market_data.public_trade_capture_reader import (
    CaptureInputReadSummary,
)
from strategy_verifier.public_market_data.public_trade_capture_reader import (
    PublicTradeCaptureReadConfig,
    PublicTradeCaptureStreamCounters,
    iter_public_trade_capture_events,
)
from strategy_verifier.verifier.conditions import EpisodeGate, create_condition
from strategy_verifier.verifier.primitives import create_primitive

_SERIES_PRIMITIVES = {
    "sma": ("period",),
    "smma": ("period",),
    "rolling_max": ("period",),
    "rolling_min": ("period",),
    "rolling_median": ("period",),
    "mean_abs_return": ("window",),
    "lag": ("k",),
}
_OPS = (">", "<", ">=", "<=")
_BASE_VALUES = {
    "close", "volume", "open", "high", "low",
    "body", "body_abs", "upper_wick", "lower_wick", "range",
}
_TOP_KEYS = {
    "family", "engine", "frame", "params", "series", "conditions",
    "direction", "event", "controls", "outcomes", "calibrate",
    "patterns", "splits",
}


@dataclass(frozen=True, slots=True)
class _Compare:
    left: str
    op: str
    right: str


@dataclass(frozen=True, slots=True)
class GraphDefinition:
    family: str
    bar_seconds: int
    params: dict[str, Decimal]
    series: tuple[dict, ...]
    conditions: tuple[dict, ...]
    long_when: tuple[_Compare | str, ...]
    short_when: tuple[_Compare | str, ...]
    event_when: tuple[str, ...]
    gate_bars: int
    event_pattern: str | None
    from_pattern: str | None
    patterns: tuple[dict, ...]
    splits: tuple[dict, ...]
    control_stride: int
    control_offset: int
    control_min_distance: int
    control_blocked_by: tuple[str, ...]
    horizons: tuple[int, ...]
    threshold_pcts: tuple[Decimal, ...]
    calibrate_series: str


def _parse_compare(text: str, names: set[str], where: str) -> _Compare:
    for op in (">=", "<=", ">", "<"):
        if f" {op} " in text:
            left, right = text.split(f" {op} ", 1)
            left, right = left.strip(), right.strip()
            for token in (left, right):
                if token not in names:
                    raise DomainValidationError(
                        f"{where}: unknown reference '{token}' in "
                        f"'{text}'"
                    )
            return _Compare(left=left, op=op, right=right)
    raise DomainValidationError(
        f"{where}: comparison must look like 'a > b': '{text}'"
    )


def load_graph_definition(raw: dict, source: Path) -> GraphDefinition:
    unknown = set(raw) - _TOP_KEYS
    if unknown:
        raise DomainValidationError(
            f"unknown graph keys: {sorted(unknown)}"
        )
    family = raw.get("family")
    if not isinstance(family, str) or not family.strip():
        raise DomainValidationError("family must be a non-empty string")

    frame = raw.get("frame", {})
    if not isinstance(frame, dict) or set(frame) - {"bar_seconds"}:
        raise DomainValidationError("frame allows only bar_seconds")
    bar_seconds = frame.get("bar_seconds", 0)
    if not isinstance(bar_seconds, int) or bar_seconds < 0:
        raise DomainValidationError(
            "bar_seconds must be a non-negative integer"
        )

    params_raw = raw.get("params", {})
    if not isinstance(params_raw, dict):
        raise DomainValidationError("params must be a table")
    params: dict[str, Decimal] = {}
    for key, value in params_raw.items():
        try:
            params[key] = Decimal(str(value))
        except InvalidOperation:
            raise DomainValidationError(
                f"param {key} is not a decimal: {value}"
            )

    names: set[str] = _BASE_VALUES | set(params)
    series_specs = raw.get("series", [])
    if not isinstance(series_specs, list):
        raise DomainValidationError("series must be an array of tables")
    series: list[dict] = []
    for spec in series_specs:
        if not isinstance(spec, dict):
            raise DomainValidationError("each series must be a table")
        name = spec.get("name")
        primitive = spec.get("primitive")
        if not isinstance(name, str) or not name or name in names:
            raise DomainValidationError(
                f"series name missing or duplicate: {name}"
            )
        if primitive in ("pair_max", "pair_min", "ratio"):
            allowed = {"name", "primitive", "a", "b"}
            if set(spec) - allowed:
                raise DomainValidationError(
                    f"series {name}: unknown keys "
                    f"{sorted(set(spec) - allowed)}"
                )
            for role in ("a", "b"):
                ref = spec.get(role)
                if ref not in names or ref in params:
                    raise DomainValidationError(
                        f"series {name}: {role} must reference close, "
                        f"volume, a base value, or an earlier series"
                    )
        elif primitive == "distance":
            allowed = {"name", "primitive", "a", "b", "denominator"}
            if set(spec) - allowed:
                raise DomainValidationError(
                    f"series {name}: unknown keys {sorted(set(spec) - allowed)}"
                )
            for role in ("a", "b", "denominator"):
                ref = spec.get(role)
                if ref not in names or ref in params:
                    raise DomainValidationError(
                        f"series {name}: {role} must reference close, "
                        f"volume, or an earlier series"
                    )
        elif primitive == "cmf":
            allowed = {"name", "primitive", "period"}
            if set(spec) - allowed:
                raise DomainValidationError(
                    f"series {name}: unknown keys "
                    f"{sorted(set(spec) - allowed)}"
                )
            period = spec.get("period")
            if not isinstance(period, int) or isinstance(period, bool) or (
                period <= 0
            ):
                raise DomainValidationError(
                    f"series {name}: cmf period must be a positive integer"
                )
        elif primitive in _SERIES_PRIMITIVES:
            (kwarg,) = _SERIES_PRIMITIVES[primitive]
            allowed = {"name", "primitive", "input", kwarg, "feed"}
            feed = spec.get("feed", "immediate")
            if feed not in ("immediate", "delayed"):
                raise DomainValidationError(
                    f"series {name}: feed must be immediate or delayed"
                )
            if feed == "delayed" and primitive not in (
                "rolling_max", "rolling_min", "rolling_median"
            ):
                raise DomainValidationError(
                    f"series {name}: delayed feed applies to rolling "
                    f"primitives only"
                )
            if set(spec) - allowed:
                raise DomainValidationError(
                    f"series {name}: unknown keys {sorted(set(spec) - allowed)}"
                )
            ref = spec.get("input")
            if ref not in names or ref in params:
                raise DomainValidationError(
                    f"series {name}: input must reference close, volume, "
                    f"or an earlier series"
                )
            if not isinstance(spec.get(kwarg), int):
                raise DomainValidationError(
                    f"series {name}: {kwarg} must be an integer"
                )
        else:
            raise DomainValidationError(
                f"series {name}: unknown primitive {primitive}"
            )
        names.add(name)
        series.append(dict(spec))

    condition_specs = raw.get("conditions", [])
    if not isinstance(condition_specs, list):
        raise DomainValidationError("conditions must be an array of tables")
    condition_names: set[str] = set()
    post_condition_names: set[str] = set()
    conditions: list[dict] = []
    for spec in condition_specs:
        if not isinstance(spec, dict):
            raise DomainValidationError("each condition must be a table")
        name = spec.get("name")
        kind = spec.get("type")
        if (
            not isinstance(name, str) or not name
            or name in names or name in condition_names
            or name in post_condition_names
        ):
            raise DomainValidationError(
                f"condition name missing or duplicate: {name}"
            )
        parsed = dict(spec)
        if kind == "compare":
            allowed = {"name", "type", "left", "op", "right"}
            if set(spec) - allowed or spec.get("op") not in _OPS:
                raise DomainValidationError(
                    f"condition {name}: compare needs left, op, right"
                )
            for token in (spec.get("left"), spec.get("right")):
                if token not in names:
                    raise DomainValidationError(
                        f"condition {name}: unknown reference '{token}'"
                    )
        elif kind == "crosses":
            allowed = {"name", "type", "a", "b", "direction"}
            if set(spec) - allowed or spec.get("direction") not in (
                "above", "below"
            ):
                raise DomainValidationError(
                    f"condition {name}: crosses needs a, b, direction"
                )
            for token in (spec.get("a"), spec.get("b")):
                if token not in names or token in params:
                    raise DomainValidationError(
                        f"condition {name}: unknown series '{token}'"
                    )
        elif kind == "run":
            allowed = {"name", "type", "on", "minimum", "evaluate"}
            if set(spec) - allowed:
                raise DomainValidationError(
                    f"condition {name}: unknown keys"
                )
            if spec.get("on") not in condition_names:
                raise DomainValidationError(
                    f"condition {name}: on must reference an earlier "
                    f"condition"
                )
            if not isinstance(spec.get("minimum"), int) or spec["minimum"] <= 0:
                raise DomainValidationError(
                    f"condition {name}: minimum must be a positive integer"
                )
            parsed["evaluate"] = spec.get("evaluate", "after")
            if parsed["evaluate"] not in ("before", "after"):
                raise DomainValidationError(
                    f"condition {name}: evaluate must be before or after"
                )
        elif kind == "ratio_compare":
            allowed = {"name", "type", "a", "b", "scale", "op", "right"}
            if set(spec) - allowed or spec.get("op") not in _OPS:
                raise DomainValidationError(
                    f"condition {name}: ratio_compare needs a, b, "
                    f"scale, op, right"
                )
            for token in (
                spec.get("a"), spec.get("b"), spec.get("scale"),
                spec.get("right"),
            ):
                if token not in names:
                    raise DomainValidationError(
                        f"condition {name}: unknown reference '{token}'"
                    )
        elif kind == "signed_compare":
            allowed = {
                "name", "type", "value", "reference",
                "long_reference", "short_reference", "scale", "minimum",
            }
            if set(spec) - allowed:
                raise DomainValidationError(
                    f"condition {name}: unknown keys"
                )
            has_single = "reference" in spec
            has_pair = (
                "long_reference" in spec and "short_reference" in spec
            )
            if has_single == has_pair:
                raise DomainValidationError(
                    f"condition {name}: exactly one of reference or "
                    f"the long/short reference pair"
                )
            parsed["scale"] = spec.get("scale", spec.get("value"))
            refs = [spec.get("value"), parsed["scale"], spec.get("minimum")]
            if has_single:
                refs.append(spec.get("reference"))
            else:
                refs.extend(
                    [spec.get("long_reference"), spec.get("short_reference")]
                )
            for token in refs:
                if token not in names:
                    raise DomainValidationError(
                        f"condition {name}: unknown reference '{token}'"
                    )
            post_condition_names.add(name)
            conditions.append(parsed)
            continue
        elif kind == "side_gate":
            allowed = {"name", "type", "long_when", "short_when"}
            if set(spec) - allowed:
                raise DomainValidationError(
                    f"condition {name}: unknown keys"
                )
            for key in ("long_when", "short_when"):
                entries = spec.get(key)
                if not isinstance(entries, list) or not entries or not all(
                    item in condition_names or item in post_condition_names
                    for item in entries
                ):
                    raise DomainValidationError(
                        f"condition {name}: {key} must list earlier "
                        f"condition names"
                    )
            post_condition_names.add(name)
            conditions.append(parsed)
            continue
        elif kind == "order_block":
            allowed = {"name", "type", "push_window", "displacement"}
            if set(spec) - allowed:
                raise DomainValidationError(
                    f"condition {name}: unknown keys"
                )
            window = spec.get("push_window")
            if not isinstance(window, int) or isinstance(window, bool) or (
                window <= 0
            ):
                raise DomainValidationError(
                    f"condition {name}: push_window must be a positive "
                    f"integer"
                )
            try:
                displacement = Decimal(str(spec.get("displacement")))
            except (ArithmeticError, ValueError, TypeError):
                raise DomainValidationError(
                    f"condition {name}: displacement must be a number"
                )
            if displacement <= 0:
                raise DomainValidationError(
                    f"condition {name}: displacement must be positive"
                )
        else:
            raise DomainValidationError(
                f"condition {name}: unknown type {kind}"
            )
        condition_names.add(name)
        conditions.append(parsed)

    pattern_specs = raw.get("patterns", [])
    if not isinstance(pattern_specs, list):
        raise DomainValidationError("patterns must be an array of tables")
    pattern_names: set[str] = set()
    patterns: list[dict] = []
    for spec in pattern_specs:
        if not isinstance(spec, dict) or spec.get("type") != "head_shoulders":
            raise DomainValidationError(
                "each pattern must be a table with type head_shoulders"
            )
        allowed = {
            "name", "type", "confirm_bars", "head_depth_min",
            "shoulder_balance_max", "completion_window_bars",
            "episode_gate_bars", "volume_window_bars",
            "volume_ratio_split", "breakout_buffer",
        }
        if set(spec) - allowed:
            raise DomainValidationError(
                f"pattern: unknown keys {sorted(set(spec) - allowed)}"
            )
        name = spec.get("name")
        if (
            not isinstance(name, str) or not name or name in names
            or name in condition_names or name in post_condition_names
            or name in pattern_names
        ):
            raise DomainValidationError(
                f"pattern name missing or duplicate: {name}"
            )
        for key in (
            "confirm_bars", "completion_window_bars",
            "episode_gate_bars", "volume_window_bars",
        ):
            if not isinstance(spec.get(key), int) or spec[key] <= 0:
                raise DomainValidationError(
                    f"pattern {name}: {key} must be a positive integer"
                )
        parsed = dict(spec)
        for key in (
            "head_depth_min", "shoulder_balance_max",
            "volume_ratio_split", "breakout_buffer",
        ):
            try:
                parsed[key] = Decimal(str(spec.get(key)))
            except InvalidOperation:
                raise DomainValidationError(
                    f"pattern {name}: {key} must be a decimal"
                )
        pattern_names.add(name)
        patterns.append(parsed)

    direction = raw.get("direction")
    if not isinstance(direction, dict) or set(direction) - {
        "long_when", "short_when", "from_pattern"
    }:
        raise DomainValidationError(
            "direction needs long_when and short_when lists, or "
            "from_pattern"
        )
    from_pattern = direction.get("from_pattern")
    if from_pattern is not None:
        if set(direction) != {"from_pattern"}:
            raise DomainValidationError(
                "direction.from_pattern excludes long_when and "
                "short_when"
            )
        if from_pattern not in pattern_names:
            raise DomainValidationError(
                f"direction.from_pattern: unknown pattern "
                f"'{from_pattern}'"
            )

    def _rule(key: str):
        items = direction.get(key)
        if not isinstance(items, list) or not items:
            raise DomainValidationError(f"direction.{key} must be a list")
        out = []
        for item in items:
            if not isinstance(item, str):
                raise DomainValidationError(
                    f"direction.{key} entries must be strings"
                )
            if item in post_condition_names:
                raise DomainValidationError(
                    f"direction.{key}: '{item}' evaluates after the "
                    f"direction and cannot define it"
                )
            if item in condition_names:
                out.append(item)
            else:
                out.append(
                    _parse_compare(item, names, f"direction.{key}")
                )
        return tuple(out)

    if from_pattern is None:
        long_when = _rule("long_when")
        short_when = _rule("short_when")
    else:
        long_when = ()
        short_when = ()

    event = raw.get("event")
    if not isinstance(event, dict) or set(event) - {
        "when", "gate_bars", "pattern"
    }:
        raise DomainValidationError(
            "event needs when and gate_bars, or pattern"
        )
    event_pattern = event.get("pattern")
    if event_pattern is not None:
        if set(event) != {"pattern"}:
            raise DomainValidationError(
                "event.pattern excludes when and gate_bars; the "
                "pattern carries its own gate"
            )
        if event_pattern not in pattern_names:
            raise DomainValidationError(
                f"event.pattern: unknown pattern '{event_pattern}'"
            )
        if from_pattern != event_pattern:
            raise DomainValidationError(
                "a pattern event requires direction.from_pattern on "
                "the same pattern"
            )
        event_when = ()
        gate_bars = 0
    else:
        if from_pattern is not None:
            raise DomainValidationError(
                "direction.from_pattern requires event.pattern"
            )
        event_when = event.get("when")
        if not isinstance(event_when, list) or not event_when or not all(
            item in condition_names or item in post_condition_names
            for item in event_when
        ):
            raise DomainValidationError(
                "event.when must list declared condition names"
            )
        event_when = tuple(event_when)
        gate_bars = event.get("gate_bars")
        if not isinstance(gate_bars, int) or gate_bars <= 0:
            raise DomainValidationError(
                "gate_bars must be a positive integer"
            )

    controls = raw.get("controls")
    blocked_by: tuple[str, ...] = ()
    if controls is None:
        stride, offset, min_distance = 0, 0, 0
    else:
        if not isinstance(controls, dict) or set(controls) - {
            "stride", "offset", "min_distance_bars", "blocked_by"
        }:
            raise DomainValidationError(
                "controls needs stride, offset, min_distance_bars"
            )
        stride = controls.get("stride")
        offset = controls.get("offset")
        min_distance = controls.get("min_distance_bars")
        for value, label in (
            (stride, "stride"), (min_distance, "min_distance_bars"),
        ):
            if not isinstance(value, int) or value <= 0:
                raise DomainValidationError(
                    f"controls.{label} must be a positive integer"
                )
        if not isinstance(offset, int) or not 0 <= offset < stride:
            raise DomainValidationError(
                "controls.offset must lie inside the stride"
            )
        raw_blocked = controls.get("blocked_by", [])
        if not isinstance(raw_blocked, list) or not all(
            item in condition_names for item in raw_blocked
        ):
            raise DomainValidationError(
                "controls.blocked_by must list pre-direction "
                "condition names"
            )
        blocked_by = tuple(raw_blocked)

    outcomes = raw.get("outcomes")
    if not isinstance(outcomes, dict) or set(outcomes) - {
        "horizons", "threshold_pcts"
    }:
        raise DomainValidationError(
            "outcomes needs horizons and threshold_pcts"
        )
    horizons = outcomes.get("horizons")
    thresholds_raw = outcomes.get("threshold_pcts")
    if not isinstance(horizons, list) or not horizons or not all(
        isinstance(h, int) and h > 0 for h in horizons
    ):
        raise DomainValidationError("horizons must be positive integers")
    if not isinstance(thresholds_raw, list) or not thresholds_raw:
        raise DomainValidationError("threshold_pcts is required")
    try:
        thresholds = tuple(
            Decimal(str(threshold)) for threshold in thresholds_raw
        )
    except InvalidOperation:
        raise DomainValidationError("threshold_pcts must be decimals")

    split_specs = raw.get("splits", [])
    if not isinstance(split_specs, list):
        raise DomainValidationError("splits must be an array of tables")
    axis_names: set[str] = set()
    splits: list[dict] = []
    for spec in split_specs:
        if not isinstance(spec, dict) or spec.get("type") != (
            "series_threshold"
        ):
            raise DomainValidationError(
                "each split must be a table with type series_threshold"
            )
        allowed = {"name", "type", "series", "split"}
        if set(spec) - allowed:
            raise DomainValidationError(
                f"split: unknown keys {sorted(set(spec) - allowed)}"
            )
        name = spec.get("name")
        if not isinstance(name, str) or not name or name in axis_names:
            raise DomainValidationError(
                f"split name missing or duplicate: {name}"
            )
        if event_pattern is not None and name in ("pattern", "volume"):
            raise DomainValidationError(
                f"split name '{name}' is reserved by the pattern event"
            )
        for role in ("series", "split"):
            if spec.get(role) not in names:
                raise DomainValidationError(
                    f"split {name}: {role} must reference a declared "
                    f"value"
                )
        axis_names.add(name)
        splits.append(dict(spec))

    calibrate = raw.get("calibrate", {})
    if not isinstance(calibrate, dict) or set(calibrate) - {"series"}:
        raise DomainValidationError("calibrate allows only series")
    calibrate_series = calibrate.get("series", "close")
    if calibrate_series not in names or calibrate_series in params:
        raise DomainValidationError(
            "calibrate.series must reference close, volume, or a series"
        )

    return GraphDefinition(
        family=family.strip(),
        bar_seconds=bar_seconds,
        params=params,
        series=tuple(series),
        conditions=tuple(conditions),
        long_when=long_when,
        short_when=short_when,
        event_when=tuple(event_when),
        gate_bars=gate_bars,
        event_pattern=event_pattern,
        from_pattern=from_pattern,
        patterns=tuple(patterns),
        splits=tuple(splits),
        control_stride=stride,
        control_offset=offset,
        control_min_distance=min_distance,
        control_blocked_by=blocked_by,
        horizons=tuple(sorted(set(horizons))),
        threshold_pcts=tuple(sorted(set(thresholds))),
        calibrate_series=calibrate_series,
    )


@dataclass(slots=True)
class _GridTally:
    favorable: int = 0
    adverse: int = 0
    untouched: int = 0

    def share_text(self) -> str:
        resolved = self.favorable + self.adverse
        if resolved == 0:
            return "none"
        return str(
            (Decimal(self.favorable) / Decimal(resolved)).quantize(
                Decimal("0.0001")
            )
        )


@dataclass(slots=True)
class _ActiveSubject:
    kind: str
    side: str
    entry_price: Decimal
    direction: Decimal
    start_event_index: int
    unresolved: set = field(default_factory=set)
    labels: dict = field(default_factory=dict)
    reading_index: int | None = None


@dataclass(frozen=True, slots=True)
class GraphRunResult:
    definition: GraphDefinition
    calibrate_only: bool
    capture_read_results: list[CaptureInputReadSummary]
    input_count: int
    total_jsonl_lines: int
    valid_event_count: int
    invalid_line_count: int
    bar_count: int
    directional_bar_count: int
    event_count: int
    long_event_count: int
    short_event_count: int
    control_count: int
    control_skipped_near_count: int
    calibrate_value_count: int
    calibrate_percentiles: tuple[tuple[int, Decimal], ...]
    base_grid: dict
    control_grid: dict
    side_grids: dict[str, dict]
    axis_grids: dict[str, dict[str, dict]]
    reading_markers: tuple[dict, ...]
    reading_last_bars: tuple[dict, ...]


def _empty_grid(definition: GraphDefinition):
    return {
        (horizon, threshold): _GridTally()
        for horizon in definition.horizons
        for threshold in definition.threshold_pcts
    }


def _advance_subject(
    subject, price, raw_index, base, control, sides, axes,
    markers=None, annotate_cell=None,
):
    if subject.entry_price <= 0:
        subject.unresolved.clear()
        return
    move = (price / subject.entry_price - Decimal("1")) * subject.direction
    elapsed = raw_index - subject.start_event_index
    for cell in list(subject.unresolved):
        horizon, threshold = cell
        if move >= threshold:
            outcome = "favorable"
        elif move <= -threshold:
            outcome = "adverse"
        elif elapsed >= horizon:
            outcome = "untouched"
        else:
            continue
        subject.unresolved.discard(cell)
        if (
            markers is not None
            and subject.reading_index is not None
            and cell == annotate_cell
        ):
            markers[subject.reading_index]["outcome"] = outcome
        targets = [
            (base if subject.kind == "event" else control)[cell]
        ]
        if subject.kind == "event":
            targets.append(sides[subject.side][cell])
            for axis, label in subject.labels.items():
                targets.append(axes[axis][label][cell])
        for tally in targets:
            setattr(tally, outcome, getattr(tally, outcome) + 1)


def _graph_engine(
    definition: GraphDefinition,
    calibrate_only: bool,
    collect_reading: bool = False,
    reading_last_bars: int = 3,
):
    if not isinstance(definition, GraphDefinition):
        raise DomainValidationError("definition must be GraphDefinition")

    series_instances = []
    for spec in definition.series:
        primitive = spec["primitive"]
        if primitive in ("pair_max", "pair_min", "ratio"):
            series_instances.append((spec, None))
        elif primitive == "distance":
            series_instances.append((spec, create_primitive("distance")))
        elif primitive == "cmf":
            series_instances.append(
                (spec, create_primitive("cmf", period=spec["period"]))
            )
        else:
            (kwarg,) = _SERIES_PRIMITIVES[primitive]
            series_instances.append(
                (spec, create_primitive(primitive, **{kwarg: spec[kwarg]}))
            )
    condition_instances = []
    for spec in definition.conditions:
        if spec["type"] == "crosses":
            condition_instances.append(
                (spec, create_condition("crosses", direction=spec["direction"]))
            )
        elif spec["type"] == "run":
            condition_instances.append(
                (spec, create_condition("true_run", minimum=spec["minimum"]))
            )
        elif spec["type"] == "order_block":
            condition_instances.append(
                (spec, create_condition(
                    "order_block",
                    push_window=spec["push_window"],
                    displacement=spec["displacement"],
                ))
            )
        else:  # compare, ratio_compare, signed_compare, side_gate
            condition_instances.append((spec, None))
    gate = (
        EpisodeGate(minimum_bars=definition.gate_bars)
        if definition.gate_bars > 0 else None
    )

    bar_ms = definition.bar_seconds * 1000
    current_bar_id = None
    bar_close_ts = None
    bar_last_price: Decimal | None = None
    bar_open: Decimal | None = None
    bar_high: Decimal | None = None
    bar_low: Decimal | None = None
    bar_volume = Decimal("0")
    bar_count = 0
    directional_bars = 0
    event_count = long_events = short_events = 0
    control_count = control_skipped = 0
    last_block_bar = -(10**9)
    calibrate_values: list[Decimal] = []
    base_grid = _empty_grid(definition)
    control_grid = _empty_grid(definition)
    side_grids = {
        "long": _empty_grid(definition), "short": _empty_grid(definition)
    }
    axis_grids: dict[str, dict[str, dict]] = {}
    if definition.event_pattern is not None:
        axis_grids["pattern"] = {
            "ihs": _empty_grid(definition), "top": _empty_grid(definition)
        }
        axis_grids["volume"] = {
            "high": _empty_grid(definition), "low": _empty_grid(definition)
        }
    for split_spec in definition.splits:
        axis_grids[split_spec["name"]] = {
            "high": _empty_grid(definition), "low": _empty_grid(definition)
        }
    pattern_nodes = {
        spec["name"]: _HeadShouldersNode(spec)
        for spec in definition.patterns
    }
    markers: list[dict] = []
    last_bars_snapshots: deque = deque(maxlen=max(1, reading_last_bars))
    annotate_cell = (
        definition.horizons[len(definition.horizons) // 2],
        definition.threshold_pcts[len(definition.threshold_pcts) // 2],
    )
    active_subjects: list[_ActiveSubject] = []
    raw_index = 0

    def _compare_true(compare: _Compare, values: dict) -> bool:
        left = values.get(compare.left)
        right = values.get(compare.right)
        if left is None or right is None:
            return False
        if compare.op == ">":
            return left > right
        if compare.op == "<":
            return left < right
        if compare.op == ">=":
            return left >= right
        return left <= right

    def _finalize_bar() -> None:
        nonlocal bar_count, directional_bars, event_count
        nonlocal long_events, short_events, control_count
        nonlocal control_skipped, last_block_bar
        if bar_last_price is None:
            return
        bar_count += 1
        values: dict = dict(definition.params)
        values["close"] = bar_last_price
        values["volume"] = bar_volume
        bar_open_value = (
            bar_open if bar_open is not None else bar_last_price
        )
        bar_high_value = (
            bar_high if bar_high is not None else bar_last_price
        )
        bar_low_value = bar_low if bar_low is not None else bar_last_price
        values["open"] = bar_open_value
        values["high"] = bar_high_value
        values["low"] = bar_low_value
        values["body"] = bar_last_price - bar_open_value
        values["body_abs"] = abs(bar_last_price - bar_open_value)
        values["upper_wick"] = bar_high_value - max(
            bar_open_value, bar_last_price
        )
        values["lower_wick"] = (
            min(bar_open_value, bar_last_price) - bar_low_value
        )
        values["range"] = bar_high_value - bar_low_value
        for spec, instance in series_instances:
            primitive = spec["primitive"]
            if primitive in ("pair_max", "pair_min", "ratio"):
                a = values.get(spec["a"])
                b = values.get(spec["b"])
                if a is None or b is None:
                    values[spec["name"]] = None
                elif primitive == "pair_max":
                    values[spec["name"]] = max(a, b)
                elif primitive == "pair_min":
                    values[spec["name"]] = min(a, b)
                else:
                    values[spec["name"]] = a / b if b > 0 else None
                continue
            if primitive == "distance":
                a = values.get(spec["a"])
                b = values.get(spec["b"])
                denominator = values.get(spec["denominator"])
                if a is None or b is None or denominator is None or (
                    denominator <= 0
                ):
                    values[spec["name"]] = None
                else:
                    values[spec["name"]] = instance.update(a, b, denominator)
            elif primitive == "cmf":
                high = values.get("high")
                low = values.get("low")
                close = values.get("close")
                volume = values.get("volume")
                if high is None or low is None or close is None or (
                    volume is None
                ):
                    values[spec["name"]] = None
                else:
                    values[spec["name"]] = instance.update(
                        high, low, close, volume
                    )
            else:
                source = values.get(spec["input"])
                if source is None:
                    values[spec["name"]] = None
                elif spec.get("feed", "immediate") == "delayed":
                    values[spec["name"]] = getattr(
                        instance, "_last_output", None
                    )
                    instance._last_output = instance.update(source)
                else:
                    values[spec["name"]] = instance.update(source)
        flags: dict[str, bool] = {}
        post_specs: list[dict] = []
        for spec, instance in condition_instances:
            name = spec["name"]
            kind = spec["type"]
            if kind in ("signed_compare", "side_gate"):
                post_specs.append(spec)
                flags[name] = False
                continue
            if kind == "compare":
                flags[name] = _compare_true(
                    _Compare(spec["left"], spec["op"], spec["right"]),
                    values,
                )
            elif kind == "ratio_compare":
                a = values.get(spec["a"])
                b = values.get(spec["b"])
                scale = values.get(spec["scale"])
                right = values.get(spec["right"])
                if (
                    a is None or b is None or scale is None
                    or right is None or scale <= 0
                ):
                    flags[name] = False
                else:
                    flags[name] = _compare_true(
                        _Compare("__ratio__", spec["op"], "__right__"),
                        {"__ratio__": (a - b) / scale, "__right__": right},
                    )
            elif kind == "crosses":
                flags[name] = instance.update(
                    values.get(spec["a"]), values.get(spec["b"])
                )
            elif kind == "order_block":
                flags[name] = instance.update(
                    values.get("open"), values.get("high"),
                    values.get("low"), values.get("close"),
                )
            else:
                underlying = flags[spec["on"]]
                if spec["evaluate"] == "before":
                    flags[name] = instance.met()
                    instance.update(underlying)
                else:
                    instance.update(underlying)
                    flags[name] = instance.met()

        def _holds(rule) -> bool:
            for item in rule:
                if isinstance(item, str):
                    if not flags[item]:
                        return False
                elif not _compare_true(item, values):
                    return False
            return True

        node_hit = None
        if definition.from_pattern is not None:
            node_hit = pattern_nodes[definition.from_pattern].update(
                bar_last_price, bar_volume
            )
            direction = (
                node_hit["direction"] if node_hit is not None
                else Decimal("0")
            )
        elif _holds(definition.long_when):
            direction = Decimal("1")
        elif _holds(definition.short_when):
            direction = Decimal("-1")
        else:
            direction = Decimal("0")

        if direction != 0:
            for spec in post_specs:
                name = spec["name"]
                if spec["type"] == "signed_compare":
                    value = values.get(spec["value"])
                    scale = values.get(spec["scale"])
                    minimum = values.get(spec["minimum"])
                    if "reference" in spec:
                        reference = values.get(spec["reference"])
                    elif direction > 0:
                        reference = values.get(spec["long_reference"])
                    else:
                        reference = values.get(spec["short_reference"])
                    if (
                        value is None or reference is None
                        or scale is None or minimum is None
                        or scale <= 0
                    ):
                        flags[name] = False
                    else:
                        flags[name] = (
                            direction * (value - reference) / scale
                            >= minimum
                        )
                else:  # side_gate
                    entries = (
                        spec["long_when"] if direction > 0
                        else spec["short_when"]
                    )
                    flags[name] = all(flags[item] for item in entries)

        for blocked_name in definition.control_blocked_by:
            if flags.get(blocked_name):
                last_block_bar = bar_count
                break
        if gate is not None:
            gate.tick()
        if direction == 0:
            return
        directional_bars += 1
        calibrate_value = values.get(definition.calibrate_series)
        if calibrate_only:
            if calibrate_value is not None:
                calibrate_values.append(calibrate_value)
            return
        fired = False
        if definition.event_pattern is not None:
            fired = node_hit is not None
        else:
            fired = all(
                flags[name] for name in definition.event_when
            ) and gate.open()
        if collect_reading:
            last_bars_snapshots.append({
                "timestamp": (
                    bar_close_ts.isoformat()
                    if bar_close_ts is not None else "?"
                ),
                "close": str(bar_last_price),
                "series": {
                    spec["name"]: (
                        str(values[spec["name"]])
                        if values.get(spec["name"]) is not None
                        else "warmup"
                    )
                    for spec in definition.series
                },
                "conditions": {
                    spec["name"]: bool(flags.get(spec["name"]))
                    for spec, _ in condition_instances
                },
                "direction": (
                    "long" if direction > 0
                    else "short" if direction < 0 else "none"
                ),
                "occurred": bool(fired),
            })
        if fired:
            side = "long" if direction > 0 else "short"
            event_count += 1
            if direction > 0:
                long_events += 1
            else:
                short_events += 1
            if gate is not None:
                gate.fire()
            last_block_bar = bar_count
            subject_labels: dict = {}
            if node_hit is not None:
                subject_labels["pattern"] = node_hit["kind"]
                subject_labels["volume"] = node_hit["split"]
            for split_spec in definition.splits:
                split_value = values.get(split_spec["series"])
                split_point = values.get(split_spec["split"])
                if split_value is not None and split_point is not None:
                    subject_labels[split_spec["name"]] = (
                        "high" if split_value >= split_point else "low"
                    )
            reading_index = None
            if collect_reading:
                reading_index = len(markers)
                markers.append({
                    "timestamp": (
                        bar_close_ts.isoformat()
                        if bar_close_ts is not None else "?"
                    ),
                    "side": side,
                    "price": str(bar_last_price),
                    "outcome": "unresolved",
                    "conditions": {
                        spec["name"]: bool(flags.get(spec["name"]))
                        for spec, _ in condition_instances
                    },
                    "series": {
                        spec["name"]: (
                            str(values[spec["name"]])
                            if values.get(spec["name"]) is not None
                            else "warmup"
                        )
                        for spec in definition.series
                    },
                })
            active_subjects.append(_ActiveSubject(
                kind="event", side=side, entry_price=bar_last_price,
                direction=direction, start_event_index=raw_index,
                unresolved=set(base_grid.keys()),
                labels=subject_labels,
                reading_index=reading_index,
            ))
        elif definition.control_stride > 0 and (
            directional_bars % definition.control_stride
            == definition.control_offset
        ):
            if bar_count - last_block_bar < definition.control_min_distance:
                control_skipped += 1
            else:
                control_count += 1
                active_subjects.append(_ActiveSubject(
                    kind="control", side="none",
                    entry_price=bar_last_price, direction=direction,
                    start_event_index=raw_index,
                    unresolved=set(base_grid.keys()),
                ))

    def _feed(tick) -> None:
        nonlocal raw_index, active_subjects, current_bar_id
        nonlocal bar_last_price, bar_open, bar_high, bar_low
        nonlocal bar_volume, bar_close_ts
        raw_index += 1
        price = tick.price
        if not calibrate_only and active_subjects:
            still_active = []
            for subject in active_subjects:
                _advance_subject(
                    subject, price, raw_index,
                    base_grid, control_grid, side_grids, axis_grids,
                    markers if collect_reading else None,
                    annotate_cell,
                )
                if subject.unresolved:
                    still_active.append(subject)
            active_subjects = still_active
        if bar_ms > 0:
            bar_id = int(tick.timestamp.timestamp() * 1000) // bar_ms
            if bar_id != current_bar_id:
                if current_bar_id is not None:
                    _finalize_bar()
                current_bar_id = bar_id
                bar_last_price = None
                bar_open = None
                bar_high = None
                bar_low = None
                bar_volume = Decimal("0")
            if bar_open is None:
                bar_open = price
            bar_high = (
                price if bar_high is None else max(bar_high, price)
            )
            bar_low = (
                price if bar_low is None else min(bar_low, price)
            )
            bar_last_price = price
            bar_close_ts = tick.timestamp
            bar_volume += tick.quantity
        else:
            bar_last_price = price
            bar_close_ts = tick.timestamp
            bar_open = price
            bar_high = price
            bar_low = price
            bar_volume = tick.quantity
            _finalize_bar()
            bar_last_price = None
            bar_open = None
            bar_high = None
            bar_low = None
            bar_volume = Decimal("0")

    def _finish(input_summaries):
        if bar_ms > 0:
            _finalize_bar()

        for subject in active_subjects:
            for cell in list(subject.unresolved):
                targets = [
                    (
                        base_grid if subject.kind == "event" else control_grid
                    )[cell]
                ]
                if subject.kind == "event":
                    targets.append(side_grids[subject.side][cell])
                    for axis, label in subject.labels.items():
                        targets.append(axis_grids[axis][label][cell])
                    if (
                        collect_reading
                        and subject.reading_index is not None
                        and cell == annotate_cell
                        and markers[subject.reading_index]["outcome"]
                        == "unresolved"
                    ):
                        markers[subject.reading_index]["outcome"] = (
                            "untouched"
                        )
                for tally in targets:
                    tally.untouched += 1
            subject.unresolved.clear()

        percentiles: list[tuple[int, Decimal]] = []
        if calibrate_values:
            ordered = sorted(calibrate_values)
            count = len(ordered)
            for quantile in range(5, 100, 5):
                index = max(0, -(-quantile * count // 100) - 1)
                percentiles.append((quantile, ordered[index]))

        return GraphRunResult(
            definition=definition,
            calibrate_only=calibrate_only,
            capture_read_results=input_summaries,
            input_count=len(input_summaries),
            total_jsonl_lines=sum(s.total_jsonl_lines for s in input_summaries),
            valid_event_count=sum(s.valid_event_count for s in input_summaries),
            invalid_line_count=sum(s.invalid_line_count for s in input_summaries),
            bar_count=bar_count,
            directional_bar_count=directional_bars,
            event_count=event_count,
            long_event_count=long_events,
            short_event_count=short_events,
            control_count=control_count,
            control_skipped_near_count=control_skipped,
            calibrate_value_count=len(calibrate_values),
            calibrate_percentiles=tuple(percentiles),
            base_grid=base_grid,
            control_grid=control_grid,
            side_grids=side_grids,
            axis_grids=axis_grids,
            reading_markers=tuple(markers),
            reading_last_bars=tuple(last_bars_snapshots),
        )

    def _peek():
        return {
            "markers": tuple(markers),
            "latest_bars": tuple(last_bars_snapshots),
            "event_count": event_count,
            "bar_count": bar_count,
        }

    return _feed, _finish, _peek


def render_graph_report(result: GraphRunResult) -> str:
    if not isinstance(result, GraphRunResult):
        raise DomainValidationError("result must be GraphRunResult")
    definition = result.definition
    params_text = " ".join(
        f"{key}={definition.params[key]}"
        for key in sorted(definition.params)
    ) or "none"
    lines = [
        "Capture Graph Report",
        "Public data only. Research diagnostics only.",
        (
            "No API keys, private API, orders, account access, database, "
            "paper runtime, simulated fills, positions, DCA, or live trading."
        ),
        "",
        f"mode: {'calibrate_only' if result.calibrate_only else 'measurement'}",
        f"family: {definition.family}",
        f"engine: graph_v1",
        f"input_count: {result.input_count}",
        f"total_jsonl_lines: {result.total_jsonl_lines}",
        f"valid_event_count: {result.valid_event_count}",
        f"invalid_line_count: {result.invalid_line_count}",
        (
            "graph_rule: bar_seconds="
            f"{definition.bar_seconds} gate_bars={definition.gate_bars} "
            f"stride={definition.control_stride} offset="
            f"{definition.control_offset} min_distance="
            f"{definition.control_min_distance} params: {params_text}"
        ),
        (
            "bars: total="
            f"{result.bar_count} directional={result.directional_bar_count}"
        ),
    ]
    if result.calibrate_only:
        lines.append(
            f"calibrate_series: {definition.calibrate_series} values="
            f"{result.calibrate_value_count}"
        )
        lines.append("calibrate_percentiles:")
        for quantile, value in result.calibrate_percentiles:
            lines.append(f"- p{quantile:02d}: {value}")
        lines.extend([
            "calibration_note: percentile ladder by the nearest-rank "
            "rule over directional bars; no outcomes were computed",
            "",
        ])
        return "\n".join(lines)
    lines.extend([
        (
            "events: rated="
            f"{result.event_count} long={result.long_event_count} "
            f"short={result.short_event_count}"
        ),
        (
            "controls: rated="
            f"{result.control_count} skipped_near="
            f"{result.control_skipped_near_count}"
        ),
        "primary_reading_note: exploration output; grids inform stage C "
        "selection only and carry no verdict",
        "base_grid (favorable/adverse/untouched share):",
    ])
    lines.extend(_render_graph_grid(result.base_grid, definition))
    lines.append("control_grid:")
    lines.extend(_render_graph_grid(result.control_grid, definition))
    for side in ("long", "short"):
        lines.append(f"side={side}:")
        lines.extend(_render_graph_grid(result.side_grids[side], definition))
    for axis, label_grids in result.axis_grids.items():
        for label, grid in label_grids.items():
            lines.append(f"{axis}={label}:")
            lines.extend(_render_graph_grid(grid, definition))
    lines.extend([
        "reasons:",
        "- local graph measurement completed",
        "- series and conditions evaluate in declaration order",
        "block_reasons:",
        "- none",
        "",
    ])
    return "\n".join(lines)


def _render_graph_grid(grid, definition: GraphDefinition) -> list[str]:
    lines = []
    for horizon in definition.horizons:
        parts = []
        for threshold in definition.threshold_pcts:
            tally = grid[(horizon, threshold)]
            parts.append(
                f"{threshold}: {tally.favorable}/{tally.adverse}/"
                f"{tally.untouched} ({tally.share_text()})"
            )
        lines.append(f"- horizon={horizon} " + " | ".join(parts))
    return lines


class _HeadShouldersNode:
    """Engine-registered five-point extremum template: alternating
    confirmed extrema, mechanical qualification, an armed deadline,
    neckline completion with a buffer, an internal episode gate, and a
    trailing-median volume split excluding the current bar (inverse
    head-and-shoulders, "ihs", and its mirror top)."""

    def __init__(self, spec: dict) -> None:
        self._spec = spec
        self._extrema_feed = create_primitive(
            "confirmed_extrema", confirm_bars=spec["confirm_bars"]
        )
        self._volume_roll = create_primitive(
            "rolling_median", period=spec["volume_window_bars"]
        )
        self._gate = EpisodeGate(minimum_bars=spec["episode_gate_bars"])
        self._extrema: list[tuple[str, Decimal]] = []
        self._armed: deque = deque()
        self._cached_median: Decimal | None = None
        self._bar_count = 0

    def _try_qualify(self) -> None:
        spec = self._spec
        if len(self._extrema) < 5:
            return
        (t1, e1), (t2, e2), (t3, e3), (t4, e4), (t5, e5) = (
            self._extrema[-5:]
        )
        if t1 == "min" and t3 == "min" and t5 == "min":
            if e3 < e1 and e3 < e5:
                neckline = max(e2, e4)
                depth = min(e1, e5) - e3
                if (
                    depth >= spec["head_depth_min"] * neckline
                    and abs(e1 - e5)
                    <= spec["shoulder_balance_max"] * depth
                ):
                    self._armed.append((
                        "ihs", Decimal("1"), neckline,
                        self._bar_count + spec["completion_window_bars"],
                    ))
        elif t1 == "max" and t3 == "max" and t5 == "max":
            if e3 > e1 and e3 > e5:
                neckline = min(e2, e4)
                depth = e3 - max(e1, e5)
                if (
                    depth >= spec["head_depth_min"] * neckline
                    and abs(e1 - e5)
                    <= spec["shoulder_balance_max"] * depth
                ):
                    self._armed.append((
                        "top", Decimal("-1"), neckline,
                        self._bar_count + spec["completion_window_bars"],
                    ))

    def update(self, close: Decimal, volume: Decimal):
        spec = self._spec
        self._bar_count += 1
        self._gate.tick()
        median_volume = self._cached_median
        self._cached_median = self._volume_roll.update(volume)

        emitted = self._extrema_feed.update(close)
        if emitted is not None:
            kind, center = emitted
            if self._extrema and self._extrema[-1][0] == kind:
                previous = self._extrema[-1][1]
                keep_new = (
                    center > previous if kind == "max"
                    else center < previous
                )
                if keep_new:
                    self._extrema[-1] = (kind, center)
                    self._try_qualify()
            else:
                self._extrema.append((kind, center))
                if len(self._extrema) > 8:
                    self._extrema.pop(0)
                self._try_qualify()

        while self._armed and self._bar_count > self._armed[0][3]:
            self._armed.popleft()
        if self._armed:
            pattern, direction, neckline, _deadline = self._armed[0]
            completed = (
                close >= neckline * (Decimal("1") + spec["breakout_buffer"])
                if direction > 0
                else close
                <= neckline * (Decimal("1") - spec["breakout_buffer"])
            )
            if completed:
                self._armed.popleft()
                if not self._gate.open():
                    return None
                if median_volume is None or median_volume <= 0:
                    return None
                ratio = volume / median_volume
                self._gate.fire()
                return {
                    "kind": pattern,
                    "direction": direction,
                    "split": (
                        "high"
                        if ratio >= spec["volume_ratio_split"]
                        else "low"
                    ),
                }
        return None


def run_graph(
    definition: GraphDefinition,
    input_paths: list[Path],
    input_session_mode: str,
    calibrate_only: bool,
    collect_reading: bool = False,
    reading_last_bars: int = 3,
) -> GraphRunResult:
    feed, finish, _peek = _graph_engine(
        definition, calibrate_only, collect_reading, reading_last_bars
    )
    input_summaries: list[CaptureInputReadSummary] = []
    for read_config in [
        PublicTradeCaptureReadConfig(input_path=path)
        for path in input_paths
    ]:
        counters = PublicTradeCaptureStreamCounters()
        for stream_event in iter_public_trade_capture_events(
            read_config, counters
        ):
            tick = stream_event.mapped_trade_tick
            if tick is None:
                continue
            feed(tick)
        input_summaries.append(CaptureInputReadSummary(
            input_path=read_config.input_path,
            total_jsonl_lines=counters.total_jsonl_lines,
            valid_event_count=counters.valid_event_count,
            invalid_line_count=counters.invalid_line_count,
            block_reasons=[],
        ))
    return finish(input_summaries)


def run_graph_grouped(
    definition: GraphDefinition,
    input_paths: list[Path],
    input_session_mode: str,
    calibrate_only: bool,
) -> GraphRunResult:
    """Run the graph engine with true session isolation.

    Inputs are partitioned into session groups by the shared
    capture_session_group_id rule (single -> one group; file -> one
    group per file; prefix -> one group per chunk-suffix prefix). Each
    group runs on its own fresh engine, so indicator warmup never
    carries over a boundary and an outcome horizon never reaches from
    one group's tail into the next group's head. The per-group results
    are then summed: occurrences pool, but every outcome was measured
    within a single group. With one group the single engine path is
    used unchanged, so single-session runs are byte-for-byte identical
    to before. Calibration is an outcome-free threshold pre-pass over
    the pooled distribution, so it stays a single continuous run.
    """
    validate_input_session_mode(input_session_mode)
    paths = [Path(path) for path in input_paths]
    groups: dict[str, list[Path]] = {}
    order: list[str] = []
    for path in paths:
        group_id = capture_session_group_id(path, input_session_mode)
        if group_id not in groups:
            groups[group_id] = []
            order.append(group_id)
        groups[group_id].append(path)
    if calibrate_only or len(order) <= 1:
        return run_graph(
            definition,
            input_paths=paths,
            input_session_mode=input_session_mode,
            calibrate_only=calibrate_only,
        )
    partials = [
        run_graph(
            definition,
            input_paths=groups[group_id],
            input_session_mode=input_session_mode,
            calibrate_only=False,
        )
        for group_id in order
    ]
    return _merge_graph_results(partials, definition)


def _merge_grids(grids: list[dict], definition: GraphDefinition) -> dict:
    merged = _empty_grid(definition)
    for grid in grids:
        for key, tally in grid.items():
            merged[key].favorable += tally.favorable
            merged[key].adverse += tally.adverse
            merged[key].untouched += tally.untouched
    return merged


def _merge_graph_results(
    partials: list[GraphRunResult], definition: GraphDefinition
) -> GraphRunResult:
    """Sum independent per-group results into one pooled result.

    Grid tallies add cell by cell; counters add; read summaries and
    reading markers concatenate in group order. Every partial shares
    this definition, so all grids carry identical keys.
    """
    side_keys = partials[0].side_grids.keys()
    axis_keys = {
        axis: list(partials[0].axis_grids[axis].keys())
        for axis in partials[0].axis_grids
    }
    capture_read_results: list[CaptureInputReadSummary] = []
    reading_markers: tuple[dict, ...] = ()
    reading_last_bars: tuple[dict, ...] = ()
    for partial in partials:
        capture_read_results.extend(partial.capture_read_results)
        reading_markers = reading_markers + partial.reading_markers
        reading_last_bars = reading_last_bars + partial.reading_last_bars
    return GraphRunResult(
        definition=definition,
        calibrate_only=False,
        capture_read_results=capture_read_results,
        input_count=sum(p.input_count for p in partials),
        total_jsonl_lines=sum(p.total_jsonl_lines for p in partials),
        valid_event_count=sum(p.valid_event_count for p in partials),
        invalid_line_count=sum(p.invalid_line_count for p in partials),
        bar_count=sum(p.bar_count for p in partials),
        directional_bar_count=sum(p.directional_bar_count for p in partials),
        event_count=sum(p.event_count for p in partials),
        long_event_count=sum(p.long_event_count for p in partials),
        short_event_count=sum(p.short_event_count for p in partials),
        control_count=sum(p.control_count for p in partials),
        control_skipped_near_count=sum(
            p.control_skipped_near_count for p in partials
        ),
        calibrate_value_count=0,
        calibrate_percentiles=(),
        base_grid=_merge_grids([p.base_grid for p in partials], definition),
        control_grid=_merge_grids(
            [p.control_grid for p in partials], definition
        ),
        side_grids={
            side: _merge_grids(
                [p.side_grids[side] for p in partials], definition
            )
            for side in side_keys
        },
        axis_grids={
            axis: {
                label: _merge_grids(
                    [p.axis_grids[axis][label] for p in partials], definition
                )
                for label in labels
            }
            for axis, labels in axis_keys.items()
        },
        reading_markers=reading_markers,
        reading_last_bars=reading_last_bars,
    )


class GraphSession:
    """One engine, two feeds: the same per-tick core behind both the
    batch runner above and the live reader below."""

    def __init__(
        self,
        definition: GraphDefinition,
        calibrate_only: bool = False,
        collect_reading: bool = False,
        reading_last_bars: int = 3,
    ) -> None:
        self._feed, self._finish, self._peek = _graph_engine(
            definition, calibrate_only, collect_reading,
            reading_last_bars,
        )

    def feed_tick(self, tick) -> None:
        self._feed(tick)

    def finish(self, input_summaries=()) -> GraphRunResult:
        return self._finish(list(input_summaries))

    def peek(self) -> dict:
        """Non-destructive view of markers, latest snapshots, and
        counters for live surfaces; finish stays a one-shot flush."""
        return self._peek()


class LiveReader:
    """Follows the recorder's session files (base, -s2, -s3) in
    order, parses each capture line with the batch reader's own
    mapper, and feeds the shared GraphSession. drain() consumes
    everything currently on disk and returns how many ticks it fed;
    growth between drains continues the same bar state."""

    def __init__(self, definition: GraphDefinition, base_path: Path,
                 collect_reading: bool = True, on_tick=None) -> None:
        self._on_tick = on_tick
        import json as _json

        from strategy_verifier.public_market_data.public_trade_capture_reader import (
            _mapped_trade_tick_from_record,
        )

        self._json = _json
        self._map = _mapped_trade_tick_from_record
        self.session = GraphSession(
            definition, collect_reading=collect_reading
        )
        self._base = base_path
        self._session_number = 1
        self._handle = None
        self.fed = 0

    def _path_for(self, number: int) -> Path:
        if number == 1:
            return self._base
        return self._base.with_name(
            f"{self._base.stem}-s{number}{self._base.suffix}"
        )

    def drain(self) -> int:
        fed_now = 0
        while True:
            if self._handle is None:
                path = self._path_for(self._session_number)
                if not path.exists():
                    return fed_now
                self._handle = path.open("r", encoding="utf-8")
            line = self._handle.readline()
            if line:
                if not line.endswith("\n"):
                    # 미완 라인: 되감고 다음 drain 에서 재시도
                    self._handle.seek(
                        self._handle.tell() - len(line)
                    )
                    return fed_now
                try:
                    record = self._json.loads(line)
                    tick = self._map(record)
                except (ValueError, KeyError, TypeError):
                    continue
                if tick is None:
                    continue
                self.session.feed_tick(tick)
                if self._on_tick is not None:
                    self._on_tick(tick)
                self.fed += 1
                fed_now += 1
                continue
            next_path = self._path_for(self._session_number + 1)
            if next_path.exists():
                self._handle.close()
                self._handle = None
                self._session_number += 1
                continue
            return fed_now

    def close(self) -> None:
        if self._handle is not None:
            self._handle.close()
            self._handle = None
