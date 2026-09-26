"""
Reporting -- formatted console output for backtest results.

Follows the same visual style as ingestion/report.py so the team
sees a consistent reporting pattern across M0.1 and M0.2.
"""

from backtesting.core.metrics import BacktestMetrics


def print_report(
    metrics: BacktestMetrics,
    strategy_name: str,
    symbol: str,
    start_date: str,
    end_date: str,
) -> str:
    """
    Format and print the backtest report.

    Returns the formatted string (useful for testing / logging).
    """
    report = f"""
========================================
BACKTEST REPORT -- {strategy_name}
========================================

Symbol:            {symbol}
Period:            {start_date} to {end_date}
Initial capital:   {metrics.initial_capital:,.2f}
Final equity:      {metrics.final_equity:,.2f}

Total return:      {metrics.total_return_pct:+.2f}%
CAGR:              {metrics.cagr_pct:+.2f}%
Sharpe ratio:      {metrics.sharpe_ratio:.2f}
Volatility (ann):  {metrics.annualized_volatility_pct:.2f}%
Max drawdown:      {metrics.max_drawdown_pct:.2f}%

Avg exposure:      {metrics.avg_exposure_pct:.2f}%
Turnover:          {metrics.turnover:.4f}x
Total costs:       {metrics.total_costs:,.2f}

Trades:            {metrics.total_trades}
Win rate:          {metrics.win_rate_pct:.2f}%
Profit factor:     {metrics.profit_factor:.2f}

Status:            COMPLETE
========================================
""".strip()

    print(report)
    return report

