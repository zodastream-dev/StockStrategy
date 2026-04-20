"""
技术指标策略集
"""
import sys
import io
import time
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict
from . import BaseStrategy, BacktestResult, Trade

# 编码修复在 Flask 环境下由框架处理

import akshare as ak


# ============================================================
# MACD金叉死叉策略
# ============================================================
class MACDCrossStrategy(BaseStrategy):
    name = "MACD金叉死叉"
    description = "DIF上穿DEA（金叉）买入，下穿（死叉）卖出。MACD参数：12, 26, 9"
    params_schema = [
        {'name': 'fast', 'type': 'number', 'default': 12, 'min': 5, 'max': 30, 'step': 1, 'label': '快线周期', 'description': 'EMA快线周期'},
        {'name': 'slow', 'type': 'number', 'default': 26, 'min': 10, 'max': 60, 'step': 1, 'label': '慢线周期', 'description': 'EMA慢线周期'},
        {'name': 'signal', 'type': 'number', 'default': 9, 'min': 3, 'max': 20, 'step': 1, 'label': '信号线周期', 'description': 'MACD信号线周期'},
        {'name': 'stop_loss', 'type': 'number', 'default': 5.0, 'min': 1, 'max': 20, 'step': 0.5, 'label': '止损 %', 'description': '止损幅度'},
        {'name': 'take_profit', 'type': 'number', 'default': 15.0, 'min': 3, 'max': 50, 'step': 1, 'label': '止盈 %', 'description': '止盈幅度'},
    ]

    def _calculate_macd(self, df: pd.DataFrame, fast: int, slow: int, signal: int) -> pd.DataFrame:
        """计算MACD"""
        df = df.copy()
        df['ema_fast'] = df['close'].ewm(span=fast, adjust=False).mean()
        df['ema_slow'] = df['close'].ewm(span=slow, adjust=False).mean()
        df['dif'] = df['ema_fast'] - df['ema_slow']
        df['dea'] = df['dif'].ewm(span=signal, adjust=False).mean()
        df['macd'] = (df['dif'] - df['dea']) * 2
        return df

    def run(self, stock_code: str, start_date: str, end_date: str, params: Dict) -> BacktestResult:
        self.reset()
        p = params
        fast, slow, signal = int(p['fast']), int(p['slow']), int(p['signal'])
        stop_loss = p.get('stop_loss', 5.0) / 100
        take_profit = p.get('take_profit', 15.0) / 100

        df = self._load_stock_data(stock_code, start_date, end_date)
        if df is None or df.empty:
            return self._empty_result(p, start_date, end_date)

        df = self._calculate_macd(df, fast, slow, signal)
        df['prev_dif'] = df['dif'].shift(1)
        df['prev_dea'] = df['dea'].shift(1)

        prev_golden = False
        for i, row in df.iterrows():
            date_str = str(row['date'])[:10]
            price = row['close']

            if not self.position:
                # 金叉买入条件：前一天DIF<=DEA，今天DIF>DEA
                if prev_golden is False and row['prev_dif'] <= row['prev_dea'] and row['dif'] > row['dea']:
                    self.buy(date_str, stock_code, stock_code, price, reason='MACD金叉')
            else:
                pos = self.position
                cost = pos['avg_cost']
                pnl = (price - cost) / cost

                # 止损/止盈
                if pnl <= -stop_loss:
                    self.sell(date_str, price, reason=f'止损(-{stop_loss*100:.1f}%)')
                elif pnl >= take_profit:
                    self.sell(date_str, price, reason=f'止盈(+{take_profit*100:.1f}%)')
                # 死叉卖出
                elif prev_golden and row['prev_dif'] >= row['prev_dea'] and row['dif'] < row['dea']:
                    self.sell(date_str, price, reason='MACD死叉')

            prev_golden = row['dif'] > row['dea']

            equity = self.get_equity(date_str)
            self.equity_curve.append({'date': date_str, 'equity': equity})
            if len(self.equity_curve) > 1:
                ret = (equity - self.equity_curve[-2]['equity']) / self.equity_curve[-2]['equity']
                self.daily_returns.append(ret)

        # 最后一天若持仓则平仓
        if self.position and df is not None and not df.empty:
            last_date = str(df.iloc[-1]['date'])[:10]
            last_price = df.iloc[-1]['close']
            self.sell(last_date, last_price, reason='回测结束平仓')

        return self.finalize_result(self.name, p, start_date, end_date)

    def _load_stock_data(self, stock_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        try:
            df = ak.stock_zh_a_hist(symbol=stock_code, start_date=start_date.replace('-', ''),
                                    end_date=end_date.replace('-', ''), adjust='qfq')
            if df is None or df.empty:
                return None
            df.columns = [c.strip() for c in df.columns]
            col_map = {k: v for k, v in {
                '日期': 'date', '开盘': 'open', '收盘': 'close',
                '最高': 'high', '最低': 'low', '成交量': 'volume',
                '成交额': 'amount', '振幅': 'amplitude', '涨跌幅': 'pct_change',
                '涨跌额': 'price_change', '换手率': 'turnover'
            }.items() if k in df.columns}
            df = df.rename(columns=col_map)
            return df
        except Exception as e:
            return None

    def _empty_result(self, p, start, end) -> BacktestResult:
        return BacktestResult(
            strategy_name=self.name, params=p, start_date=start, end_date=end,
            total_trades=0, win_trades=0, lose_trades=0, success_rate=0,
            total_return_pct=0, avg_return_pct=0, max_profit=0, max_loss=0,
            sharpe_ratio=0, max_drawdown=0, annual_return=0,
            trades=[], equity_curve=[], monthly_returns={}
        )


# ============================================================
# 均线突破策略
# ============================================================
class BreakoutStrategy(BaseStrategy):
    name = "均线突破"
    description = "收盘价突破N日均线买入，跌破N日均线卖出。使用布林带过滤假突破。"
    params_schema = [
        {'name': 'ma_period', 'type': 'number', 'default': 20, 'min': 5, 'max': 120, 'step': 5, 'label': '均线周期', 'description': '均线计算周期'},
        {'name': 'bb_period', 'type': 'number', 'default': 20, 'min': 10, 'max': 60, 'step': 5, 'label': '布林带周期', 'description': '布林带周期'},
        {'name': 'bb_std', 'type': 'number', 'default': 2.0, 'min': 1.0, 'max': 4.0, 'step': 0.5, 'label': '布林带倍数', 'description': '布林带标准差倍数'},
        {'name': 'stop_loss', 'type': 'number', 'default': 3.0, 'min': 1, 'max': 15, 'step': 0.5, 'label': '止损 %', 'description': '止损幅度'},
    ]

    def _load_data(self, stock_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        try:
            df = ak.stock_zh_a_hist(symbol=stock_code, start_date=start_date.replace('-', ''),
                                    end_date=end_date.replace('-', ''), adjust='qfq')
            if df is None or df.empty:
                return None
            df.columns = [c.strip() for c in df.columns]
            col_map = {}
            for c in df.columns:
                if '日期' in c: col_map[c] = 'date'
                elif '开盘' in c: col_map[c] = 'open'
                elif '收盘' in c: col_map[c] = 'close'
                elif '最高' in c: col_map[c] = 'high'
                elif '最低' in c: col_map[c] = 'low'
                elif '成交量' in c: col_map[c] = 'volume'
            df = df.rename(columns=col_map)
            return df
        except:
            return None

    def run(self, stock_code: str, start_date: str, end_date: str, params: Dict) -> BacktestResult:
        self.reset()
        ma_period = int(params.get('ma_period', 20))
        bb_period = int(params.get('bb_period', 20))
        bb_std = float(params.get('bb_std', 2.0))
        stop_loss = float(params.get('stop_loss', 3.0)) / 100

        df = self._load_data(stock_code, start_date, end_date)
        if df is None or df.empty:
            return self._empty_result(params, start_date, end_date)

        df['ma'] = df['close'].rolling(ma_period).mean()
        df['bb_std'] = df['close'].rolling(bb_period).std()
        df['bb_upper'] = df['ma'] + bb_std * bb_std  # 注意：bb_std已经是标准差
        df['bb_lower'] = df['ma'] - bb_std * bb_std

        for i, row in df.iterrows():
            if pd.isna(row['ma']):
                continue
            date_str = str(row['date'])[:10]
            price = row['close']

            if not self.position:
                # 收盘价站上均线且在布林带上轨内
                prev_close = df.iloc[i-1]['close'] if i > 0 else price
                if prev_close <= row['ma'] and price > row['ma'] and price < row['bb_upper']:
                    self.buy(date_str, stock_code, stock_code, price, reason=f'突破{ma_period}日均线')
            else:
                pos = self.position
                pnl = (price - pos['avg_cost']) / pos['avg_cost']
                prev_ma = df.iloc[i-1]['ma'] if i > 0 else price

                if pnl <= -stop_loss:
                    self.sell(date_str, price, reason=f'止损(-{stop_loss*100:.1f}%)')
                elif prev_close > prev_ma and price < row['ma']:
                    self.sell(date_str, price, reason=f'跌破{ma_period}日均线')

            equity = self.get_equity(date_str)
            self.equity_curve.append({'date': date_str, 'equity': equity})
            if len(self.equity_curve) > 1:
                ret = (equity - self.equity_curve[-2]['equity']) / self.equity_curve[-2]['equity']
                self.daily_returns.append(ret)

        if self.position and not df.empty:
            last_date = str(df.iloc[-1]['date'])[:10]
            self.sell(last_date, df.iloc[-1]['close'], reason='回测结束平仓')

        return self.finalize_result(self.name, params, start_date, end_date)

    def _empty_result(self, p, start, end) -> BacktestResult:
        return BacktestResult(
            strategy_name=self.name, params=p, start_date=start, end_date=end,
            total_trades=0, win_trades=0, lose_trades=0, success_rate=0,
            total_return_pct=0, avg_return_pct=0, max_profit=0, max_loss=0,
            sharpe_ratio=0, max_drawdown=0, annual_return=0,
            trades=[], equity_curve=[], monthly_returns={}
        )


# ============================================================
# RSI超卖策略
# ============================================================
class RSIOversoldStrategy(BaseStrategy):
    name = "RSI超卖反转"
    description = "RSI低于超卖阈值时买入，RSI回到正常区间或高位时卖出。经典超买超卖策略。"
    params_schema = [
        {'name': 'rsi_period', 'type': 'number', 'default': 14, 'min': 5, 'max': 30, 'step': 1, 'label': 'RSI周期', 'description': 'RSI计算周期'},
        {'name': 'oversold', 'type': 'number', 'default': 30, 'min': 15, 'max': 45, 'step': 5, 'label': '超卖阈值', 'description': 'RSI低于此值视为超卖'},
        {'name': 'overbought', 'type': 'number', 'default': 70, 'min': 55, 'max': 85, 'step': 5, 'label': '超买阈值', 'description': 'RSI高于此值视为超买'},
        {'name': 'stop_loss', 'type': 'number', 'default': 8.0, 'min': 2, 'max': 20, 'step': 0.5, 'label': '止损 %', 'description': '止损幅度'},
    ]

    def _rsi(self, prices: pd.Series, period: int) -> pd.Series:
        delta = prices.diff()
        gain = delta.where(delta > 0, 0)
        loss = -delta.where(delta < 0, 0)
        avg_gain = gain.rolling(period).mean()
        avg_loss = loss.rolling(period).mean()
        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))

    def _load_data(self, stock_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        try:
            df = ak.stock_zh_a_hist(symbol=stock_code, start_date=start_date.replace('-', ''),
                                    end_date=end_date.replace('-', ''), adjust='qfq')
            if df is None or df.empty:
                return None
            df.columns = [c.strip() for c in df.columns]
            col_map = {}
            for c in df.columns:
                if '日期' in c: col_map[c] = 'date'
                elif '开盘' in c: col_map[c] = 'open'
                elif '收盘' in c: col_map[c] = 'close'
                elif '最高' in c: col_map[c] = 'high'
                elif '最低' in c: col_map[c] = 'low'
            df = df.rename(columns=col_map)
            return df
        except:
            return None

    def run(self, stock_code: str, start_date: str, end_date: str, params: Dict) -> BacktestResult:
        self.reset()
        rsi_period = int(params.get('rsi_period', 14))
        oversold = float(params.get('oversold', 30))
        overbought = float(params.get('overbought', 70))
        stop_loss = float(params.get('stop_loss', 8.0)) / 100

        df = self._load_data(stock_code, start_date, end_date)
        if df is None or df.empty:
            return self._empty_result(params, start_date, end_date)

        df['rsi'] = self._rsi(df['close'], rsi_period)
        prev_rsi = None

        for i, row in df.iterrows():
            if pd.isna(row['rsi']):
                prev_rsi = None
                continue

            date_str = str(row['date'])[:10]
            price = row['close']

            if not self.position:
                if prev_rsi is not None and prev_rsi < oversold and row['rsi'] > oversold:
                    self.buy(date_str, stock_code, stock_code, price, reason=f'RSI超卖反转({row["rsi"]:.1f})')
            else:
                pos = self.position
                pnl = (price - pos['avg_cost']) / pos['avg_cost']
                if pnl <= -stop_loss:
                    self.sell(date_str, price, reason=f'止损(-{stop_loss*100:.1f}%)')
                elif row['rsi'] > overbought:
                    self.sell(date_str, price, reason=f'RSI超买({row["rsi"]:.1f})')

            prev_rsi = row['rsi']
            equity = self.get_equity(date_str)
            self.equity_curve.append({'date': date_str, 'equity': equity})
            if len(self.equity_curve) > 1:
                ret = (equity - self.equity_curve[-2]['equity']) / self.equity_curve[-2]['equity']
                self.daily_returns.append(ret)

        if self.position and not df.empty:
            last_date = str(df.iloc[-1]['date'])[:10]
            self.sell(last_date, df.iloc[-1]['close'], reason='回测结束平仓')

        return self.finalize_result(self.name, params, start_date, end_date)

    def _empty_result(self, p, start, end) -> BacktestResult:
        return BacktestResult(
            strategy_name=self.name, params=p, start_date=start, end_date=end,
            total_trades=0, win_trades=0, lose_trades=0, success_rate=0,
            total_return_pct=0, avg_return_pct=0, max_profit=0, max_loss=0,
            sharpe_ratio=0, max_drawdown=0, annual_return=0,
            trades=[], equity_curve=[], monthly_returns={}
        )


# ============================================================
# 双均线金叉死叉策略
# ============================================================
class DualMAStrategy(BaseStrategy):
    name = "双均线交叉"
    description = "短期均线上穿长期均线买入，下穿卖出。经典趋势跟踪策略。"
    params_schema = [
        {'name': 'fast_ma', 'type': 'number', 'default': 5, 'min': 2, 'max': 30, 'step': 1, 'label': '快线周期', 'description': '短期均线周期'},
        {'name': 'slow_ma', 'type': 'number', 'default': 20, 'min': 10, 'max': 120, 'step': 5, 'label': '慢线周期', 'description': '长期均线周期'},
        {'name': 'stop_loss', 'type': 'number', 'default': 5.0, 'min': 1, 'max': 20, 'step': 0.5, 'label': '止损 %', 'description': '止损幅度'},
        {'name': 'trailing_stop', 'type': 'number', 'default': 8.0, 'min': 2, 'max': 30, 'step': 1, 'label': '跟踪止损 %', 'description': '移动止损幅度'},
    ]

    def _load_data(self, stock_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        try:
            df = ak.stock_zh_a_hist(symbol=stock_code, start_date=start_date.replace('-', ''),
                                    end_date=end_date.replace('-', ''), adjust='qfq')
            if df is None or df.empty:
                return None
            df.columns = [c.strip() for c in df.columns]
            col_map = {}
            for c in df.columns:
                if '日期' in c: col_map[c] = 'date'
                elif '收盘' in c: col_map[c] = 'close'
            df = df.rename(columns=col_map)
            return df
        except:
            return None

    def run(self, stock_code: str, start_date: str, end_date: str, params: Dict) -> BacktestResult:
        self.reset()
        fast = int(params.get('fast_ma', 5))
        slow = int(params.get('slow_ma', 20))
        stop_loss = float(params.get('stop_loss', 5.0)) / 100
        trailing_stop = float(params.get('trailing_stop', 8.0)) / 100
        highest_after_buy = 0.0

        df = self._load_data(stock_code, start_date, end_date)
        if df is None or df.empty:
            return self._empty_result(params, start_date, end_date)

        df['ma_fast'] = df['close'].rolling(fast).mean()
        df['ma_slow'] = df['close'].rolling(slow).mean()
        prev_fast_above = False

        for i, row in df.iterrows():
            if pd.isna(row['ma_fast']) or pd.isna(row['ma_slow']):
                continue

            date_str = str(row['date'])[:10]
            price = row['close']
            fast_above = row['ma_fast'] > row['ma_slow']

            if not self.position:
                if not prev_fast_above and fast_above:
                    self.buy(date_str, stock_code, stock_code, price, reason=f'{fast}日线上穿{slow}日线')
                    highest_after_buy = price
            else:
                pos = self.position
                cost = pos['avg_cost']
                pnl = (price - cost) / cost

                if price > highest_after_buy:
                    highest_after_buy = price

                trail_triggered = (highest_after_buy - price) / highest_after_buy >= trailing_stop

                if pnl <= -stop_loss:
                    self.sell(date_str, price, reason=f'止损(-{stop_loss*100:.1f}%)')
                elif trail_triggered:
                    self.sell(date_str, price, reason=f'跟踪止损(-{trailing_stop*100:.1f}%)')
                elif prev_fast_above and not fast_above:
                    self.sell(date_str, price, reason=f'{fast}日线下穿{slow}日线')

            prev_fast_above = fast_above
            equity = self.get_equity(date_str)
            self.equity_curve.append({'date': date_str, 'equity': equity})
            if len(self.equity_curve) > 1:
                ret = (equity - self.equity_curve[-2]['equity']) / self.equity_curve[-2]['equity']
                self.daily_returns.append(ret)

        if self.position and not df.empty:
            last_date = str(df.iloc[-1]['date'])[:10]
            self.sell(last_date, df.iloc[-1]['close'], reason='回测结束平仓')

        return self.finalize_result(self.name, params, start_date, end_date)

    def _empty_result(self, p, start, end) -> BacktestResult:
        return BacktestResult(
            strategy_name=self.name, params=p, start_date=start, end_date=end,
            total_trades=0, win_trades=0, lose_trades=0, success_rate=0,
            total_return_pct=0, avg_return_pct=0, max_profit=0, max_loss=0,
            sharpe_ratio=0, max_drawdown=0, annual_return=0,
            trades=[], equity_curve=[], monthly_returns={}
        )
