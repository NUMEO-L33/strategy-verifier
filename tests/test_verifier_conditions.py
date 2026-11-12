"""Hand-scenario oracles for the condition algebra and episode gate."""

from decimal import Decimal

import pytest

from strategy_verifier.domain.errors import DomainValidationError
from strategy_verifier.verifier.conditions import EpisodeGate, create_condition


def test_crosses_strict_semantics():
    up = create_condition("crosses", direction="above")
    pairs = [(1, 2), (2, 2), (3, 2), (1, 2), (3, 2), (4, 2)]
    fired = [up.update(Decimal(a), Decimal(b)) for a, b in pairs]
    assert fired == [False, False, True, False, True, False]
    down = create_condition("crosses", direction="below")
    assert down.update(Decimal(3), Decimal(2)) is False
    assert down.update(Decimal(1), Decimal(2)) is True
    assert down.update(None, Decimal(2)) is False  # 워밍업 리셋
    assert down.update(Decimal(1), Decimal(2)) is False


def test_true_run_and_gate():
    run = create_condition("true_run", minimum=3)
    counts = [run.update(flag) for flag in (True, True, False, True, True, True)]
    assert counts == [1, 2, 0, 1, 2, 3]
    assert run.met() is True

    gate = EpisodeGate(minimum_bars=2)
    assert gate.open() is True
    gate.fire()
    gate.tick()
    assert gate.open() is False
    gate.tick()
    assert gate.open() is True


def test_within_beyond_and_validation():
    within = create_condition("within", threshold="0.004")
    assert within.update(Decimal("0.004")) is True
    assert within.update(Decimal("0.0041")) is False
    assert within.update(None) is False

    beyond = create_condition("beyond_by", buffer="0.0035")
    assert beyond.update(
        Decimal("100.36"), Decimal("100"), Decimal("1"), Decimal("100")
    ) is True
    assert beyond.update(
        Decimal("100.34"), Decimal("100"), Decimal("1"), Decimal("100")
    ) is False
    assert beyond.update(
        Decimal("99.64"), Decimal("100"), Decimal("-1"), Decimal("100")
    ) is True
    with pytest.raises(DomainValidationError):
        create_condition("crosses", direction="sideways")
    with pytest.raises(DomainValidationError):
        create_condition("nope")


def test_lag_primitive():
    from strategy_verifier.verifier.primitives import create_primitive

    lag = create_primitive("lag", k=3)
    outs = [lag.update(v) for v in (1, 2, 3, 4, 5)]
    assert outs == [None, None, None, 1, 2]


def _ob_bars(ob, bars):
    return [ob.update(*[Decimal(str(x)) for x in bar]) for bar in bars]


def test_order_block_reentry_fires_once_on_reentry():
    ob = create_condition("order_block", push_window=3, displacement="1.0")
    # (open, high, low, close)
    bars = [
        (110, 112, 99, 100),   # 0 down bar -> candidate, band [99,112]
        (101, 126, 101, 125),  # 1 high 126 >= 112 + 1.0*13 -> confirmed
        (125, 130, 124, 128),  # 2 above band, no re-entry
        (127, 127, 108, 110),  # 3 close 110 back inside [99,112] -> FIRE
        (110, 111, 109, 111),  # 4 still inside, no re-fire
        (111, 111, 95, 98),    # 5 close 98 < 99 -> band invalidated
    ]
    assert _ob_bars(ob, bars) == [False, False, False, True, False, False]


def test_order_block_candidate_expires_without_displacement():
    ob = create_condition("order_block", push_window=2, displacement="2.0")
    bars = [
        (110, 112, 100, 101),  # 0 down -> candidate, threshold 112+2*12=136
        (101, 120, 101, 119),  # 1 high 120 < 136, age 1
        (119, 130, 118, 129),  # 2 high 130 < 136, age 2 -> expired
        (129, 130, 104, 105),  # 3 close 105 in old band but no active OB
    ]
    assert _ob_bars(ob, bars) == [False, False, False, False]
    assert ob._active == []


def test_order_block_rejects_bad_params():
    with pytest.raises(DomainValidationError):
        create_condition("order_block", push_window=0, displacement="1.0")
    with pytest.raises(DomainValidationError):
        create_condition("order_block", push_window=3, displacement="0")
