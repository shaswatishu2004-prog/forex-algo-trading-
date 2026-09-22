from fx_strategy.costs import TradeCosts, net_trade_return, pip_cost_as_return


def test_pip_cost_conversion():
    assert round(pip_cost_as_return(1, 1.0), 6) == 0.0001


def test_costs_are_subtracted():
    costs = TradeCosts(spread=0.0001, commission=0.00002, slippage=0.00003)
    assert round(net_trade_return(0.001, costs), 6) == 0.00085

