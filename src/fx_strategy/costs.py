"""Transparent transaction-cost calculations for research and testing."""

from dataclasses import dataclass


@dataclass(frozen=True)
class TradeCosts:
    """Costs expressed as decimal returns on notional, e.g. 0.0001 = 1 bp."""

    spread: float = 0.0
    commission: float = 0.0
    slippage: float = 0.0
    financing: float = 0.0

    @property
    def total(self) -> float:
        return self.spread + self.commission + self.slippage + self.financing


def pip_cost_as_return(pips: float, price: float, pip_size: float = 0.0001) -> float:
    """Convert a quoted pip cost into a fractional return on base-currency notional."""
    if pips < 0 or price <= 0 or pip_size <= 0:
        raise ValueError("pips must be non-negative and price/pip_size must be positive")
    return pips * pip_size / price


def net_trade_return(gross_return: float, costs: TradeCosts) -> float:
    """Subtract all explicitly modelled costs from a gross trade return."""
    return gross_return - costs.total

