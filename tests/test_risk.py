import pytest

from fx_strategy.risk import RiskLimits, drawdown_from_peak, position_notional, should_halt


def test_position_sizing():
    limits = RiskLimits(risk_per_trade=0.01)
    assert position_notional(10_000, 0.02, limits) == 5_000


def test_drawdown_and_halt():
    limits = RiskLimits(max_drawdown=0.2)
    assert drawdown_from_peak(8_000, 10_000) == 0.2
    assert should_halt(8_000, 10_000, limits)


def test_invalid_inputs():
    with pytest.raises(ValueError):
        RiskLimits(risk_per_trade=1.0)
    with pytest.raises(ValueError):
        position_notional(100, 0, RiskLimits())

