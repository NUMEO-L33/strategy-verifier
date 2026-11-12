"""Interpreter v1 series primitives (streaming, oracle-pinned).

Each primitive consumes one value per bar and yields the current
series value or None during warmup. The registry is the plugin seam
named in the v1 spec; every primitive ships with an independent
recomputation test before use.

Research diagnostics only.
"""

from __future__ import annotations

from collections import deque
from decimal import Decimal

from strategy_verifier.domain.errors import DomainValidationError

_REGISTRY: dict[str, type] = {}


def register_primitive(name: str):
    def wrap(cls):
        if name in _REGISTRY:
            raise DomainValidationError(f"duplicate primitive: {name}")
        _REGISTRY[name] = cls
        return cls
    return wrap


def create_primitive(name: str, **kwargs):
    if name not in _REGISTRY:
        raise DomainValidationError(f"unknown primitive: {name}")
    return _REGISTRY[name](**kwargs)


def _positive_int(value, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise DomainValidationError(f"{name} must be a positive integer")
    return value


@register_primitive("sma")
class Sma:
    def __init__(self, period: int):
        self.period = _positive_int(period, "period")
        self._window: deque[Decimal] = deque(maxlen=self.period)
        self._sum = Decimal("0")

    def update(self, value: Decimal) -> Decimal | None:
        if len(self._window) == self._window.maxlen:
            self._sum -= self._window[0]
        self._window.append(value)
        self._sum += value
        if len(self._window) < self.period:
            return None
        return self._sum / Decimal(self.period)


@register_primitive("rolling_max")
class RollingMax:
    def __init__(self, period: int):
        self.period = _positive_int(period, "period")
        self._window: deque[Decimal] = deque(maxlen=self.period)

    def update(self, value: Decimal) -> Decimal | None:
        self._window.append(value)
        if len(self._window) < self.period:
            return None
        return max(self._window)


@register_primitive("rolling_min")
class RollingMin:
    def __init__(self, period: int):
        self.period = _positive_int(period, "period")
        self._window: deque[Decimal] = deque(maxlen=self.period)

    def update(self, value: Decimal) -> Decimal | None:
        self._window.append(value)
        if len(self._window) < self.period:
            return None
        return min(self._window)


@register_primitive("smma")
class Smma:
    """Wilder smoothing: seeded by the simple average of the first
    period values, then (previous * (period - 1) + value) / period."""

    def __init__(self, period: int):
        self.period = _positive_int(period, "period")
        self._seed: list[Decimal] = []
        self._value: Decimal | None = None

    def update(self, value: Decimal) -> Decimal | None:
        if self._value is None:
            self._seed.append(value)
            if len(self._seed) < self.period:
                return None
            self._value = sum(self._seed) / Decimal(self.period)
            return self._value
        self._value = (
            self._value * (self.period - 1) + value
        ) / Decimal(self.period)
        return self._value


@register_primitive("rolling_median")
class RollingMedian:
    def __init__(self, period: int):
        import statistics

        self.period = _positive_int(period, "period")
        self._window: deque[Decimal] = deque(maxlen=self.period)
        self._median = statistics.median

    def update(self, value: Decimal) -> Decimal | None:
        self._window.append(value)
        if len(self._window) < self.period:
            return None
        return Decimal(str(self._median(list(self._window))))


@register_primitive("cmf")
class Cmf:
    """Chaikin Money Flow over `period` bars.

    Per bar the money-flow multiplier is
    ((close - low) - (high - close)) / (high - low), and is zero when
    the bar has no range (high == low): no range, no flow. Money-flow
    volume is that multiplier times volume, and CMF is the rolling
    sum of money-flow volume divided by the rolling sum of volume.
    Returns None during warmup, and None when the volume window sums
    to zero (the ratio is undefined). Reads high, low, close, and
    volume per bar; it is the one primitive with a four-value update.
    """

    def __init__(self, period: int):
        self.period = _positive_int(period, "period")
        self._mfv: deque[Decimal] = deque(maxlen=self.period)
        self._vol: deque[Decimal] = deque(maxlen=self.period)
        self._mfv_sum = Decimal("0")
        self._vol_sum = Decimal("0")

    def update(
        self,
        high: Decimal,
        low: Decimal,
        close: Decimal,
        volume: Decimal,
    ) -> Decimal | None:
        bar_range = high - low
        if bar_range > 0:
            multiplier = ((close - low) - (high - close)) / bar_range
        else:
            multiplier = Decimal("0")
        money_flow_volume = multiplier * volume
        if len(self._mfv) == self._mfv.maxlen:
            self._mfv_sum -= self._mfv[0]
            self._vol_sum -= self._vol[0]
        self._mfv.append(money_flow_volume)
        self._vol.append(volume)
        self._mfv_sum += money_flow_volume
        self._vol_sum += volume
        if len(self._mfv) < self.period:
            return None
        if self._vol_sum <= 0:
            return None
        return self._mfv_sum / self._vol_sum


@register_primitive("distance")
class Distance:
    """Gap between two series values, optionally normalized by a
    denominator supplied per update."""

    def __init__(self, normalized: bool = True, absolute: bool = True):
        if not isinstance(normalized, bool) or not isinstance(absolute, bool):
            raise DomainValidationError(
                "normalized and absolute must be bools"
            )
        self.normalized = normalized
        self.absolute = absolute

    def update(
        self,
        value_a: Decimal,
        value_b: Decimal,
        denominator: Decimal | None = None,
    ) -> Decimal | None:
        if value_a is None or value_b is None:
            return None
        gap = value_a - value_b
        if self.absolute:
            gap = abs(gap)
        if not self.normalized:
            return gap
        if denominator is None or denominator <= 0:
            raise DomainValidationError(
                "normalized distance requires a positive denominator"
            )
        return gap / denominator


@register_primitive("mean_abs_return")
class MeanAbsReturn:
    def __init__(self, window: int):
        self.window = _positive_int(window, "window")
        self._returns: deque[Decimal] = deque(maxlen=self.window)
        self._sum = Decimal("0")
        self._previous: Decimal | None = None

    def update(self, value: Decimal) -> Decimal | None:
        if self._previous is not None and self._previous > 0:
            move = abs(value / self._previous - Decimal("1"))
            if len(self._returns) == self._returns.maxlen:
                self._sum -= self._returns[0]
            self._returns.append(move)
            self._sum += move
        self._previous = value
        if len(self._returns) < self.window:
            return None
        return self._sum / Decimal(self.window)


@register_primitive("confirmed_extrema")
class ConfirmedExtrema:
    """Emits ("max"|"min", value) when the bar confirm_bars ago is a
    strict extremum against every neighbor in the symmetric window,
    else None. Alternation belongs to the pattern layer."""

    def __init__(self, confirm_bars: int):
        self.confirm_bars = _positive_int(confirm_bars, "confirm_bars")
        self._window: deque[Decimal] = deque(
            maxlen=2 * self.confirm_bars + 1
        )

    def update(self, value: Decimal):
        self._window.append(value)
        if len(self._window) < self._window.maxlen:
            return None
        center = self._window[self.confirm_bars]
        others = [
            self._window[k]
            for k in range(len(self._window))
            if k != self.confirm_bars
        ]
        if all(center > other for other in others):
            return ("max", center)
        if all(center < other for other in others):
            return ("min", center)
        return None


@register_primitive("lag")
class Lag:
    """Returns the value seen k updates ago, None during warmup."""

    def __init__(self, k: int):
        self.k = _positive_int(k, "k")
        self._window: deque = deque(maxlen=self.k + 1)

    def update(self, value):
        self._window.append(value)
        if len(self._window) < self._window.maxlen:
            return None
        return self._window[0]
