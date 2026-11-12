"""Interpreter v1 condition algebra and the episode gate.

Streaming boolean building blocks the event assembler composes:
directional crossings with registered strict semantics, consecutive
true runs, band and buffer tests, and the episode gate that spaces
events. Each ships with a hand-scenario oracle test before use.

Research diagnostics only.
"""

from __future__ import annotations

from decimal import Decimal

from strategy_verifier.domain.errors import DomainValidationError

_REGISTRY: dict[str, type] = {}


def register_condition(name: str):
    def wrap(cls):
        if name in _REGISTRY:
            raise DomainValidationError(f"duplicate condition: {name}")
        _REGISTRY[name] = cls
        return cls
    return wrap


def create_condition(name: str, **kwargs):
    if name not in _REGISTRY:
        raise DomainValidationError(f"unknown condition: {name}")
    return _REGISTRY[name](**kwargs)


@register_condition("crosses")
class Crosses:
    """Fires on the update where a moves to the direction side of b,
    having been at or beyond the other side on the previous pair:
    above means previous a <= previous b and now a > b."""

    def __init__(self, direction: str):
        if direction not in ("above", "below"):
            raise DomainValidationError(
                "direction must be above or below"
            )
        self.direction = direction
        self._previous: tuple | None = None

    def update(self, value_a, value_b) -> bool:
        if value_a is None or value_b is None:
            self._previous = None
            return False
        fired = False
        if self._previous is not None:
            previous_a, previous_b = self._previous
            if self.direction == "above":
                fired = previous_a <= previous_b and value_a > value_b
            else:
                fired = previous_a >= previous_b and value_a < value_b
        self._previous = (value_a, value_b)
        return fired


@register_condition("true_run")
class TrueRun:
    """Counts consecutive true updates; exposes met() against the
    minimum, evaluated on the run before or after this update as the
    caller chooses via the returned counts."""

    def __init__(self, minimum: int):
        if not isinstance(minimum, int) or isinstance(minimum, bool) or minimum <= 0:
            raise DomainValidationError("minimum must be a positive integer")
        self.minimum = minimum
        self.run = 0

    def update(self, flag: bool) -> int:
        self.run = self.run + 1 if flag else 0
        return self.run

    def met(self) -> bool:
        return self.run >= self.minimum

    def reset(self) -> None:
        self.run = 0


@register_condition("within")
class Within:
    def __init__(self, threshold):
        self.threshold = Decimal(str(threshold))
        if self.threshold <= 0:
            raise DomainValidationError("threshold must be positive")

    def update(self, value) -> bool:
        return value is not None and value <= self.threshold


@register_condition("beyond_by")
class BeyondBy:
    """Signed buffer test: direction times (value minus reference),
    normalized by the denominator, at or beyond the buffer."""

    def __init__(self, buffer):
        self.buffer = Decimal(str(buffer))
        if self.buffer <= 0:
            raise DomainValidationError("buffer must be positive")

    def update(self, value, reference, direction, denominator) -> bool:
        if value is None or reference is None or denominator is None:
            return False
        if denominator <= 0:
            raise DomainValidationError("denominator must be positive")
        return direction * (value - reference) / denominator >= self.buffer


@register_condition("order_block")
class OrderBlock:
    """Bullish (blue) order-block re-entry, candle + displacement.

    A down bar (close < open) is a candidate. If within push_window
    later bars price rises at least displacement times the down bar's
    range above its high, the down bar's high-low band becomes an
    active order block. Fires True on a later bar whose close re-enters
    a band it was outside of on the previous bar. A band is invalidated
    and dropped once a close falls below its low (the strategy's exit
    level). Reads open, high, low, close per bar; the current bar never
    confirms or re-enters itself because it is registered as a
    candidate only after this bar's checks. Deterministic and causal:
    no future bar is consulted. Full high-low band in this version; a
    body-only band is a possible refinement.
    """

    def __init__(self, push_window: int, displacement):
        if (
            not isinstance(push_window, int)
            or isinstance(push_window, bool)
            or push_window <= 0
        ):
            raise DomainValidationError(
                "push_window must be a positive integer"
            )
        self.push_window = push_window
        self.displacement = Decimal(str(displacement))
        if self.displacement <= 0:
            raise DomainValidationError("displacement must be positive")
        self._pending: list[dict] = []
        self._active: list[dict] = []

    def update(self, open_, high, low, close) -> bool:
        if open_ is None or high is None or low is None or close is None:
            return False
        # 1) age candidates; promote on displacement, else expire
        survivors = []
        for cand in self._pending:
            cand["age"] += 1
            if high >= cand["high"] + self.displacement * cand["range"]:
                self._active.append(
                    {"high": cand["high"], "low": cand["low"],
                     "inside": False}
                )
            elif cand["age"] < self.push_window:
                survivors.append(cand)
        self._pending = survivors
        # 2) invalidate order blocks a close has fallen below
        self._active = [ob for ob in self._active if close >= ob["low"]]
        # 3) re-entry: transition from outside to inside a band
        fired = False
        for ob in self._active:
            now_inside = ob["low"] <= close <= ob["high"]
            if now_inside and not ob["inside"]:
                fired = True
            ob["inside"] = now_inside
        # 4) register this bar as a candidate if it is a down bar
        if close < open_:
            self._pending.append(
                {"high": high, "low": low, "range": high - low, "age": 0}
            )
        return fired


class EpisodeGate:
    """Spaces events: blocked until minimum bars since the last fire."""

    def __init__(self, minimum_bars: int):
        if not isinstance(minimum_bars, int) or isinstance(minimum_bars, bool) or minimum_bars <= 0:
            raise DomainValidationError(
                "minimum_bars must be a positive integer"
            )
        self.minimum_bars = minimum_bars
        self.bars_since = 10**9

    def tick(self) -> None:
        self.bars_since += 1

    def open(self) -> bool:
        return self.bars_since >= self.minimum_bars

    def fire(self) -> None:
        self.bars_since = 0
