"""
策略基类 - 所有策略的父类
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
from datetime import datetime, date
import pandas as pd


@dataclass
class Trade:
    """单笔交易记录"""
    date: str          # 交易日期 YYYY-MM-DD
    stock_code: str    # 股票代码
    stock_name: str   # 股票名称
    action: str        # 'buy' | 'sell'
    price: float       # 成交价
    shares: int        # 成交数量
    amount: float      # 成交金额
    reason: str        # 触发原因
    pnl: float = 0     # 盈亏（仅卖出时填充）
    holding_days: int = 0  # 持有天数


@dataclass
class BacktestResult:
    """回测结果"""
    strategy_name: str
    params: Dict[str, Any]
    start_date: str
    end_date: str
    total_trades: int
    win_trades: int
    lose_trades: int
    success_rate: float
    total_return_pct: float    # 总收益率 %
    avg_return_pct: float      # 平均单笔收益率 %
    max_profit: float          # 最大单笔盈利 %
    max_loss: float            # 最大单笔亏损 %
    sharpe_ratio: float        # 夏普比率
    max_drawdown: float        # 最大回撤 %
    annual_return: float        # 年化收益率 %
    trades: List[Trade]         # 所有交易记录
    equity_curve: List[Dict]    # 每日资金曲线
    monthly_returns: Dict[str, float]  # 月度收益率

    def to_dict(self) -> Dict[str, Any]:
        return {
            'strategy_name': self.strategy_name,
            'params': self.params,
            'period': {'start': self.start_date, 'end': self.end_date},
            'metrics': {
                'total_trades': self.total_trades,
                'win_trades': self.win_trades,
                'lose_trades': self.lose_trades,
                'success_rate': round(self.success_rate, 2),         # 已经是百分比形式(0-100)
                'total_return_pct': round(self.total_return_pct, 2),  # 已经是百分比形式
                'avg_return_pct': round(self.avg_return_pct, 2),       # 已经是百分比形式
                'max_profit': round(self.max_profit, 2),
                'max_loss': round(self.max_loss, 2),
                'sharpe_ratio': round(self.sharpe_ratio, 2),
                'max_drawdown': round(self.max_drawdown, 2),         # 已经是百分比形式
                'annual_return': round(self.annual_return, 2),        # 已经是百分比形式
            },
            'trades': [
                {
                    **t.__dict__,
                    'pnl_pct': round(t.pnl * 100, 2) if t.pnl != 0 else 0
                } for t in self.trades
            ],
            'equity_curve': self.equity_curve,
            'monthly_returns': {k: round(v * 100, 2) for k, v in self.monthly_returns.items()}
        }


class BaseStrategy(ABC):
    """策略基类"""

    name: str = "基础策略"
    description: str = ""
    params_schema: List[Dict] = []  # 参数定义 [{name, type, default, min, max, step, label, description}]

    def __init__(self, initial_capital: float = 100000):
        self.initial_capital = initial_capital
        self.cash = initial_capital
        self.position: Optional[Dict] = None  # 当前持仓 {stock_code, shares, avg_cost, date}
        self.trades: List[Trade] = []
        self.equity_curve: List[Dict] = []
        self.daily_returns: List[float] = []

    @abstractmethod
    def run(self, stock_code: str, start_date: str, end_date: str, params: Dict[str, Any]) -> BacktestResult:
        """运行回测，返回结果"""
        pass

    def reset(self):
        """重置状态"""
        self.cash = self.initial_capital
        self.position = None
        self.trades = []
        self.equity_curve = []
        self.daily_returns = []

    def buy(self, date_str: str, stock_code: str, stock_name: str, price: float,
            reason: str = '', max_shares: Optional[int] = None):
        """买入"""
        if self.position:
            return  # 已有持仓，不重复买入

        available = self.cash * 0.95  # 留5%手续费缓冲
        shares = int(available / price / 100) * 100  # 整手
        if max_shares:
            shares = min(shares, max_shares)
        if shares < 100:
            return

        amount = shares * price
        self.cash -= amount
        self.position = {
            'stock_code': stock_code,
            'stock_name': stock_name,
            'shares': shares,
            'avg_cost': price,
            'buy_date': date_str,
            'buy_reason': reason
        }
        self.trades.append(Trade(
            date=date_str, stock_code=stock_code, stock_name=stock_name,
            action='buy', price=price, shares=shares, amount=amount, reason=reason
        ))

    def sell(self, date_str: str, price: float, reason: str = ''):
        """卖出"""
        if not self.position:
            return

        pos = self.position
        shares = pos['shares']
        amount = shares * price
        cost = shares * pos['avg_cost']
        pnl = (price - pos['avg_cost']) / pos['avg_cost']
        holding_days = (datetime.strptime(date_str, '%Y-%m-%d') -
                        datetime.strptime(pos['buy_date'], '%Y-%m-%d')).days

        self.cash += amount
        self.trades.append(Trade(
            date=date_str, stock_code=pos['stock_code'], stock_name=pos['stock_name'],
            action='sell', price=price, shares=shares, amount=amount,
            reason=reason, pnl=pnl, holding_days=holding_days
        ))
        self.position = None
        return pnl

    def get_equity(self, date_str: str) -> float:
        """获取当日总权益"""
        equity = self.cash
        if self.position:
            # 用买入成本估算（简化处理）
            equity += self.position['shares'] * self.position['avg_cost']
        return equity

    def finalize_result(self, strategy_name: str, params: Dict, start_date: str, end_date: str) -> BacktestResult:
        """计算最终指标"""
        closed_trades = [t for t in self.trades if t.action == 'sell']
        win_trades = [t for t in closed_trades if t.pnl > 0]
        lose_trades = [t for t in closed_trades if t.pnl <= 0]

        pnls = [t.pnl for t in closed_trades]
        success_rate = len(win_trades) / len(pnls) * 100 if pnls else 0
        avg_return = sum(pnls) / len(pnls) if pnls else 0
        max_profit = max(pnls) if pnls else 0
        max_loss = min(pnls) if pnls else 0

        # 计算夏普比率
        if len(self.daily_returns) > 1:
            import numpy as np
            mean_ret = np.mean(self.daily_returns)
            std_ret = np.std(self.daily_returns) if np.std(self.daily_returns) > 0 else 1e-6
            sharpe = (mean_ret / std_ret) * (252 ** 0.5) if std_ret > 0 else 0
        else:
            sharpe = 0

        # 计算最大回撤
        equity_values = [e['equity'] for e in self.equity_curve]
        max_dd = 0
        peak = 0
        for eq in equity_values:
            if eq > peak:
                peak = eq
            dd = (peak - eq) / peak if peak > 0 else 0
            if dd > max_dd:
                max_dd = dd

        final_equity = self.get_equity(end_date)
        total_return = (final_equity - self.initial_capital) / self.initial_capital

        # 年化收益率
        days = (datetime.strptime(end_date, '%Y-%m-%d') -
                datetime.strptime(start_date, '%Y-%m-%d')).days
        years = max(days / 365, 1/365)
        annual_return = (final_equity / self.initial_capital) ** (1/years) - 1 if years > 0 else 0

        # 月度收益率
        monthly = {}
        for e in self.equity_curve:
            ym = e['date'][:7]
            monthly.setdefault(ym, []).append(e['equity'])
        monthly_returns = {}
        months = sorted(monthly.keys())
        for i in range(1, len(months)):
            if monthly[months[i-1]] and monthly[months[i]]:
                ret = (monthly[months[i]][-1] - monthly[months[i-1]][-1]) / monthly[months[i-1]][-1]
                monthly_returns[months[i]] = ret

        return BacktestResult(
            strategy_name=strategy_name,
            params=params,
            start_date=start_date,
            end_date=end_date,
            total_trades=len(closed_trades),
            win_trades=len(win_trades),
            lose_trades=len(lose_trades),
            success_rate=success_rate,
            total_return_pct=total_return * 100,
            avg_return_pct=avg_return * 100,
            max_profit=max_profit * 100,
            max_loss=max_loss * 100,
            sharpe_ratio=sharpe,
            max_drawdown=max_dd * 100,
            annual_return=annual_return * 100,
            trades=closed_trades,
            equity_curve=self.equity_curve,
            monthly_returns=monthly_returns
        )
