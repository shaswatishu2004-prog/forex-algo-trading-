"""Small, deterministic risk controls used by the research harness."""

from dataclasses import dataclass


@dataclass(frozen=True)
class RiskLimits:
    risk_per_trade: float = 0.0025
    max_daily_loss: float = 0.01
    max_drawdown: float = 0.15

    def __post_init__(self) -> None:
        for name, value in vars(self).items():
            if not 0 < value < 1:
                raise ValueError(f"{name} must be between 0 and 1")


def position_notional(
    equity: float,
    stop_distance: float,
    limits: RiskLimits,
) -> float:
    """Return notional sized to risk limits; stop_distance is a fractional move."""
    if equity <= 0 or stop_distance <= 0:
        raise ValueError("equity and stop_distance must be positive")
    return equity * limits.risk_per_trade / stop_distance


def drawdown_from_peak(equity: float, peak_equity: float) -> float:
    """Return drawdown as a positive fraction."""
    if equity < 0 or peak_equity <= 0 or equity > peak_equity:
        raise ValueError("equity must be non-negative and no greater than a positive peak")
    return 1.0 - equity / peak_equity


def should_halt(equity: float, peak_equity: float, limits: RiskLimits) -> bool:
    """Independent portfolio halt condition."""
    return drawdown_from_peak(equity, peak_equity) >= limits.max_drawdown

