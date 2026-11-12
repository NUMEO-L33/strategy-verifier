"""Oracle tests for interpreter v1 series primitives."""

import random
from decimal import Decimal

import pytest

from strategy_verifier.domain.errors import DomainValidationError
from strategy_verifier.verifier.primitives import create_primitive


def test_primitives_match_independent_recomputation():
    random.seed(3)
    values = [Decimal(f"{100 + random.uniform(-5, 5):.4f}") for _ in range(200)]
    for name, period, oracle in (
        ("sma", 7, lambda w: sum(w) / Decimal(7)),
        ("rolling_max", 9, max),
        ("rolling_min", 5, min),
    ):
        primitive = create_primitive(name, period=period)
        for index, value in enumerate(values):
            out = primitive.update(value)
            if index + 1 < period:
                assert out is None, (name, index)
            else:
                window = values[index - period + 1: index + 1]
                assert out == oracle(window), (name, index)


def test_registry_validation():
    with pytest.raises(DomainValidationError):
        create_primitive("nope")
    with pytest.raises(DomainValidationError):
        create_primitive("sma", period=0)


def test_wave_two_primitives_match_independent_recomputation():
    import statistics

    random.seed(11)
    values = [Decimal(f"{100 + random.uniform(-4, 4):.4f}") for _ in range(160)]

    smma = create_primitive("smma", period=6)
    expected = None
    for index, value in enumerate(values):
        out = smma.update(value)
        if index + 1 < 6:
            assert out is None
            continue
        if index + 1 == 6:
            expected = sum(values[:6]) / Decimal(6)
        else:
            expected = (expected * 5 + value) / Decimal(6)
        assert out == expected, index

    median = create_primitive("rolling_median", period=8)
    for index, value in enumerate(values):
        out = median.update(value)
        if index + 1 < 8:
            assert out is None
        else:
            window = values[index - 7: index + 1]
            assert out == Decimal(str(statistics.median(window))), index

    vol = create_primitive("mean_abs_return", window=10)
    for index, value in enumerate(values):
        out = vol.update(value)
        if index < 10:
            assert out is None
        else:
            moves = [
                abs(values[k] / values[k - 1] - Decimal("1"))
                for k in range(index - 9, index + 1)
            ]
            assert out == sum(moves) / Decimal(10), index

    extrema = create_primitive("confirmed_extrema", confirm_bars=3)
    emitted = []
    for index, value in enumerate(values):
        out = extrema.update(value)
        if out is not None:
            emitted.append((index - 3, *out))
    expected_extrema = []
    for center in range(3, len(values) - 3):
        neighborhood = values[center - 3: center + 4]
        middle = neighborhood[3]
        others = neighborhood[:3] + neighborhood[4:]
        if all(middle > other for other in others):
            expected_extrema.append((center, "max", middle))
        elif all(middle < other for other in others):
            expected_extrema.append((center, "min", middle))
    assert emitted == expected_extrema
    assert len(emitted) > 0

    distance = create_primitive("distance")
    assert distance.update(
        Decimal("102"), Decimal("100"), Decimal("100")
    ) == Decimal("0.02")
    raw = create_primitive("distance", normalized=False, absolute=False)
    assert raw.update(Decimal("98"), Decimal("100")) == Decimal("-2")
    with pytest.raises(DomainValidationError):
        distance.update(Decimal("1"), Decimal("2"), Decimal("0"))


def test_cmf_matches_independent_recomputation():
    """Chaikin Money Flow, recomputed from scratch over each window,
    including a zero-range bar (multiplier pinned to zero)."""
    random.seed(23)
    bars = []
    for _ in range(120):
        low = Decimal(f"{100 + random.uniform(-5, 5):.4f}")
        high = low + Decimal(f"{random.uniform(0.1, 3):.4f}")
        close = low + (high - low) * Decimal(f"{random.uniform(0, 1):.4f}")
        volume = Decimal(f"{random.uniform(1, 50):.4f}")
        bars.append((high, low, close, volume))
    # force a zero-range bar to exercise the no-flow branch
    flat = Decimal("102.0000")
    bars[60] = (flat, flat, flat, Decimal("7.5"))

    period = 20
    primitive = create_primitive("cmf", period=period)

    def mfv(bar):
        high, low, close, volume = bar
        rng = high - low
        mult = ((close - low) - (high - close)) / rng if rng > 0 else Decimal("0")
        return mult * volume

    for index, bar in enumerate(bars):
        out = primitive.update(*bar)
        if index + 1 < period:
            assert out is None, index
            continue
        window = bars[index - period + 1: index + 1]
        vol_sum = sum(b[3] for b in window)
        expected = (
            sum(mfv(b) for b in window) / vol_sum if vol_sum > 0 else None
        )
        assert out == expected, index
        assert out is None or Decimal("-1") <= out <= Decimal("1"), index


def test_cmf_zero_volume_window_is_none():
    period = 3
    primitive = create_primitive("cmf", period=period)
    zero_bar = (Decimal("10"), Decimal("9"), Decimal("10"), Decimal("0"))
    outs = [primitive.update(*zero_bar) for _ in range(period)]
    assert outs[-1] is None


def test_cmf_rejects_bad_period():
    with pytest.raises(DomainValidationError):
        create_primitive("cmf", period=0)
