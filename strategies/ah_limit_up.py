"""
A+H股涨停联动策略
当A股涨停时，买入对应港股，次日（或当日收盘）观察H股涨跌。
"""
import sys
import io
import time
import os
import json
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta, date
from typing import Dict, List, Any, Optional
from . import BaseStrategy, BacktestResult, Trade

import akshare as ak


def _get_email_notifier():
    """延迟导入，避免循环依赖"""
    try:
        from ..email_notifier import get_email_notifier
        return get_email_notifier()
    except ImportError:
        return None


class AHLimitUpStrategy(BaseStrategy):
    """A+H股涨停联动策略"""

    name = "A+H涨停联动"
    description = (
        "当A股涨停时，买入对应港股。策略逻辑："
        "通过东方财富接口获取H股1分钟K线数据，"
        "以A股涨停时（10:30）对应的H股1分钟开盘价作为买入价，以港股收盘价作为卖出价，"
        "计算单笔收益率。若无1分钟数据则自动回退到日线开盘价。初始资金10万元。"
        "支持邮件通知：策略触发时自动发送邮件提醒。"
    )
    params_schema = [
        {'name': 'email_notify', 'type': 'checkbox', 'default': False,
         'label': '📧 启用邮件通知', 'description': '策略条件满足时发送邮件提醒'},
        {'name': 'email_to', 'type': 'text', 'default': '',
         'label': '📮 通知邮箱', 'description': '接收通知的邮箱地址'},
    ]

    def _send_notification(self, params: Dict, trade_info: dict) -> None:
        """发送邮件通知"""
        # 检查是否启用邮件通知
        email_notify = params.get('email_notify', False)
        if not email_notify:
            return

        email_to = params.get('email_to', '').strip()
        if not email_to:
            print(f"[邮件通知] 未配置收件邮箱，跳过发送")
            return

        notifier = _get_email_notifier()
        if not notifier:
            print(f"[邮件通知] 邮件服务未初始化，跳过发送")
            return

        if not notifier.is_configured():
            print(f"[邮件通知] SMTP未配置，跳过发送")
            return

        # 构建股票信息
        stock_info = {
            'code': f"{trade_info['a_code']} / {trade_info['hk_code']}",
            'name': f"A:{trade_info['a_name']} / H:{trade_info['hk_name']}",
            'price': trade_info['hk_close'],
            'currency': 'HKD',
            'change_pct': round((trade_info['hk_close'] - trade_info['hk_price_at_limitup'])
                               / trade_info['hk_price_at_limitup'] * 100, 2)
                               if trade_info['hk_price_at_limitup'] > 0 else 0,
            'trigger_time': trade_info['date'],
        }

        # 构建预警详情
        alert_details = (
            f"A股 {trade_info['a_name']}（{trade_info['a_code']}）今日涨停！\n"
            f"对应港股 {trade_info['hk_name']}（{trade_info['hk_code']}）\n"
            f"A股成交额: {trade_info['a_vol']:.0f}万元\n"
            f"港股开盘价(涨停时): HK${trade_info['hk_price_at_limitup']:.3f}\n"
            f"港股收盘价: HK${trade_info['hk_close']:.3f}\n"
            f"港股成交量: {trade_info['hk_vol']:,.0f}股\n"
            f"从涨停到收盘盈亏: {stock_info['change_pct']:+.2f}%"
        )

        # 发送邮件
        result = notifier.send_strategy_alert(
            to_email=email_to,
            strategy_name=self.name,
            stock_info=stock_info,
            alert_details=alert_details
        )

        if result['success']:
            print(f"[邮件通知] ✓ 已发送至 {email_to}")
        else:
            print(f"[邮件通知] ✗ 发送失败: {result['message']}")

    def _load_ah_mapping(self) -> dict:
        """加载A+H配对表"""
        csv_path = os.path.join(os.path.dirname(__file__), '..', 'ah_mapping_final.csv')
        if not os.path.exists(csv_path):
            return self._build_ah_mapping()

        df = pd.read_csv(csv_path, dtype={'hk_code': str, 'a_code': str})
        a_to_hk = {}
        for _, row in df.iterrows():
            a_code = str(row['a_code']).strip().zfill(6)
            hk_code = str(row['hk_code']).strip()
            if len(hk_code) < 5:
                hk_code = hk_code.zfill(5)
            a_to_hk[a_code] = (hk_code, row['hk_name'], row['a_name'])
        return a_to_hk

    def _build_ah_mapping(self) -> dict:
        """构建A+H配对表（备用）"""
        try:
            ah_hk = ak.stock_zh_ah_name()
            ah_hk.columns = ['hk_code', 'hk_name']
            ah_hk['hk_code'] = ah_hk['hk_code'].astype(str).str.zfill(5)

            df_a = ak.stock_info_a_code_name()
            df_a.columns = ['a_code', 'a_name']
            df_a['a_code'] = df_a['a_code'].astype(str).str.zfill(6)

            def normalize(s):
                s = str(s).strip()
                for suffix in ['股份', '集团', '公司', 'A', '-H']:
                    s = s.replace(suffix, '')
                return s.strip()

            ah_hk['norm_name'] = ah_hk['hk_name'].apply(normalize)
            df_a['norm_name'] = df_a['a_name'].apply(normalize)

            a_to_hk = {}
            matched = set()
            for _, row in ah_hk.iterrows():
                hk_code = row['hk_code']
                hk_name = row['hk_name']
                norm = row['norm_name']
                match = df_a[(df_a['norm_name'] == norm) & ~df_a['a_code'].isin(matched)]
                if not match.empty:
                    a_code = match.iloc[0]['a_code']
                    a_name = match.iloc[0]['a_name']
                    a_to_hk[a_code] = (hk_code, hk_name, a_name)
                    matched.add(a_code)
            return a_to_hk
        except Exception as e:
            print(f"构建AH映射失败: {e}")
            return {}

    def _get_trading_days(self, start_date: str, end_date: str) -> list:
        """获取交易日列表"""
        try:
            df = ak.stock_zh_index_daily(symbol="sh000001")
            df['date'] = pd.to_datetime(df['date']).dt.strftime('%Y-%m-%d')
            days = df[(df['date'] >= start_date) & (df['date'] <= end_date)]['date'].tolist()
            return sorted(days) if days else []
        except:
            start = datetime.strptime(start_date, '%Y-%m-%d')
            end = datetime.strptime(end_date, '%Y-%m-%d')
            days = []
            current = start
            while current <= end:
                if current.weekday() < 5:
                    days.append(current.strftime('%Y-%m-%d'))
                current += timedelta(days=1)
            return days

    def _get_zt_pool(self, trade_date: str) -> pd.DataFrame:
        """获取涨停池"""
        try:
            date_str = trade_date.replace('-', '')
            pool = ak.stock_zt_pool_em(date=date_str)
            if pool is None or pool.empty:
                return pd.DataFrame()
            return pool
        except:
            return pd.DataFrame()

    def _get_a_volume(self, a_code: str, trade_date: str) -> Optional[float]:
        """
        获取A股当日成交额（万元）。
        列结构：iloc[5] = 成交额（元）
        返回值以「万元」为单位。
        """
        try:
            pool = self._get_zt_pool(trade_date)
            if pool.empty:
                return None
            for _, row in pool.iterrows():
                try:
                    code = str(row.iloc[1]).strip().zfill(6)
                    if code == a_code and len(row) > 5:
                        v = row.iloc[5]
                        if v is not None and not pd.isna(v):
                            return float(v) / 10000  # 转为万元
                except:
                    continue
            return None
        except:
            return None

    def _get_hk_daily(self, hk_code: str, trade_date: str) -> dict:
        """获取港股当日日线数据"""
        try:
            hk_df = ak.stock_hk_daily(symbol=hk_code)
            hk_df['date_obj'] = pd.to_datetime(hk_df['date']).dt.date
            target = datetime.strptime(trade_date, '%Y-%m-%d').date()
            day_data = hk_df[hk_df['date_obj'] == target]
            if day_data.empty:
                return {}
            row = day_data.iloc[0]
            return {
                'open': float(row.get('open', 0)),
                'close': float(row.get('close', 0)),
                'high': float(row.get('high', 0)),
                'low': float(row.get('low', 0)),
                'volume': float(row.get('volume', 0)),  # 股数
                'amount': float(row.get('amount', 0)),   # 成交金额（HKD）
            }
        except:
            return {}

    def _get_hk_realtime_price(self, hk_code: str) -> Optional[float]:
        """
        获取H股实时成交价，使用 stock_zh_ah_spot() 接口。
        该接口不受代理限制，可正常访问。
        接口返回的代码格式为5位带前导零，如 '00700'。

        参数:
            hk_code: 港股代码（5位，如 '00700'）

        返回:
            最新价（float）或 None
        """
        try:
            df = ak.stock_zh_ah_spot()
            if df is None or df.empty:
                return None
            # 列名固定: ['代码', '名称', '最新价', ...]
            code_col = '代码'
            price_col = '最新价'
            if code_col not in df.columns:
                code_col = df.columns[0]
                price_col = df.columns[2]

            # 统一用5位zfill匹配
            target = hk_code.zfill(5)
            matched = df[df[code_col].astype(str).str.zfill(5) == target]
            if matched.empty:
                return None

            price = float(matched.iloc[0][price_col])
            return price if price > 0 else None
        except Exception:
            return None

    def _get_hk_minute_bars(self, hk_code: str, trade_date: str) -> list:
        """
        获取港股当日1分钟K线数据（东方财富接口）。
        返回: [{'datetime': '...', 'open': float, 'close': float, ...}, ...]
        失败返回空列表。
        """
        try:
            # 东方财富港股市场代码116
            secid = f"116.{hk_code}"
            date_str = trade_date.replace('-', '')  # 如 '20260417'

            url = (
                f"http://push2his.eastmoney.com/api/qt/stock/kline/get"
                f"?fields1=f1%2Cf2%2Cf3%2Cf4%2Cf5%2Cf6"
                f"&fields2=f51%2Cf52%2Cf53%2Cf54%2Cf55%2Cf56%2Cf57%2Cf58%2Cf59%2Cf60%2Cf61"
                f"&ut=7eea3edcaed734bea9cbfc24409ed989"
                f"&klt=1&fqt=1&beg={date_str}&end={date_str}"
                f"&secid={secid}"
            )

            # 禁用代理以避免连接问题
            session = requests.Session()
            session.trust_env = False
            resp = session.get(url, timeout=10)

            if not resp.text.strip():
                return []

            data = json.loads(resp.text)
            if data.get('data') is None:
                return []

            klines = data['data']['klines']
            records = []
            for k in klines:
                parts = k.split(',')
                # 格式: 日期时间, 开盘, 收盘, 最高, 最低, 成交量, 成交额
                records.append({
                    'datetime': parts[0],
                    'open': float(parts[1]),
                    'close': float(parts[2]),
                    'high': float(parts[3]),
                    'low': float(parts[4]),
                    'volume': float(parts[5]),
                    'amount': float(parts[6]),
                })
            return records
        except Exception as e:
            return []

    def _find_hk_price_at_limitup(self, hk_code: str, trade_date: str,
                                   limitup_time: str = "10:30:00") -> tuple:
        """
        根据A股涨停时刻（默认10:30），获取H股对应的1分钟K线收盘价。
        即：A股涨停后最新的H股成交价。

        参数:
            hk_code: 港股代码
            trade_date: 交易日期 YYYY-MM-DD
            limitup_time: A股涨停估算时间，默认"10:30:00"

        返回:
            (价格, 来源描述, 1分钟K线列表)
            - 优先用1分钟K线 >= limitup_time 那根K线的【收盘价】（即A股涨停后最新成交价）
            - 失败则用日线开盘价回退
        """
        # 1. 尝试获取1分钟K线
        minute_bars = self._get_hk_minute_bars(hk_code, trade_date)

        if minute_bars:
            # 2. 找到 >= limitup_time 的第一根K线，取其【收盘价】（该分钟内最新成交价）
            for bar in minute_bars:
                # bar['datetime'] 格式: "2026-04-17 09:31:00"
                bar_time = bar['datetime'].split(' ')[1]  # "09:31:00"
                if bar_time >= limitup_time:
                    price = round(bar['close'], 3)  # 取收盘价，代表该分钟内最新成交价
                    source = f'1minK@{bar["datetime"][-8:]}'
                    return price, source, minute_bars

            # 如果没有匹配的（数据只到10:30之前），取最后一根K线的收盘价
            last_bar = minute_bars[-1]
            price = round(last_bar['close'], 3)
            source = f'1minK末@{last_bar["datetime"][-8:]}'
            return price, source, minute_bars

        # 3. 回退：使用日线开盘价
        daily_data = self._get_hk_daily(hk_code, trade_date)
        if daily_data and daily_data['open'] > 0:
            price = round(daily_data['open'], 3)
            return price, '日线开盘(回退)', []

        return 0, '无数据', []

    def run(self, stock_code: str, start_date: str, end_date: str, params: Dict) -> BacktestResult:
        """运行回测"""
        self.reset()
        a_to_hk = self._load_ah_mapping()
        if not a_to_hk:
            return self._build_empty_result('A+H配对表为空', params, start_date, end_date)

        trading_days = self._get_trading_days(start_date, end_date)
        results = []

        for trade_date in trading_days:
            zt_pool = self._get_zt_pool(trade_date)
            if zt_pool is None or zt_pool.empty:
                continue

            for _, row in zt_pool.iterrows():
                try:
                    a_code = str(row.iloc[1]).strip().zfill(6)
                    a_name = str(row.iloc[2]).strip() if len(row) > 2 else ''
                    # iloc[5] = 成交额（元），转为万元
                    a_vol = None
                    if len(row) > 5:
                        v = row.iloc[5]
                        if v is not None and not pd.isna(v):
                            a_vol = float(v) / 10000
                except:
                    continue

                if a_code not in a_to_hk:
                    continue

                hk_code, hk_name, mapped_a_name = a_to_hk[a_code]

                # 获取H股日线数据（用于收盘价、成交量等）
                hk_data = self._get_hk_daily(hk_code, trade_date)
                if not hk_data or hk_data['open'] <= 0 or hk_data['close'] <= 0:
                    continue

                # 获取涨停时H股价格（优先1分钟K线收盘价，回退日线开盘价）
                hk_buy_price, price_source, _ = self._find_hk_price_at_limitup(
                    hk_code, trade_date, limitup_time="10:30:00"
                )

                # 获取H股实时股价（仅在今日有意义）
                hk_realtime = self._get_hk_realtime_price(hk_code)

                results.append({
                    'date': trade_date,
                    'a_code': a_code,
                    'a_name': a_name or mapped_a_name,
                    'hk_code': hk_code,
                    'hk_name': hk_name,
                    'a_vol': a_vol,                          # A股成交额（万元）
                    'hk_vol': hk_data['volume'],             # H股成交量（股）
                    'hk_amount': hk_data.get('amount', 0),   # H股成交额（HKD）
                    'hk_close': hk_data['close'],            # H股收盘价
                    'hk_open': hk_data['open'],              # H股开盘价
                    'hk_price_at_limitup': hk_buy_price,     # A股涨停后H股最新成交价（买入价）
                    'price_source': price_source,
                    'hk_realtime': hk_realtime,              # H股当前实时成交价
                })

                # 发送邮件通知
                self._send_notification(params, results[-1])

                time.sleep(0.2)

        # 模拟交易（当日买开盘、卖收盘）
        for r in results:
            buy_price = r['hk_price_at_limitup']
            if buy_price <= 0:
                continue

            # 计算可买入股数（整手）
            shares = int(self.cash * 0.95 / buy_price / 100) * 100
            if shares < 100:
                continue

            sell_price = r['hk_close']

            # 关键修复：先扣买入款，再计入卖出款
            cost = shares * buy_price
            proceed = shares * sell_price
            self.cash -= cost          # 扣买入款
            self.cash += proceed       # 加卖出款
            pnl = (proceed - cost) / cost  # 收益率

            # 从涨停时价格到收盘价的盈亏
            pnl_by_limitup_to_close = (sell_price - buy_price) / buy_price if buy_price > 0 else 0

            closed_trade = Trade(
                date=r['date'],
                stock_code=r['hk_code'],
                stock_name=r['hk_name'],
                action='sell',
                price=sell_price,
                shares=shares,
                amount=proceed,
                reason=f"A股{r['a_name']}涨停→H股1minK线买/收盘卖",
                pnl=pnl,
                holding_days=0
            )
            # 附加额外字段
            closed_trade.a_vol = r['a_vol']
            closed_trade.hk_vol = r['hk_vol']
            closed_trade.hk_amount = r['hk_amount']
            closed_trade.hk_close = r['hk_close']
            closed_trade.hk_open = r['hk_open']
            closed_trade.hk_price_at_limitup = r['hk_price_at_limitup']
            closed_trade.pnl_by_limitup_to_close = round(pnl_by_limitup_to_close * 100, 2)
            closed_trade.price_source = r.get('price_source', '未知')
            closed_trade.hk_realtime = r.get('hk_realtime')  # H股实时股价

            self.trades.append(closed_trade)
            self.position = None

            equity = self.cash
            self.equity_curve.append({'date': r['date'], 'equity': equity})
            if len(self.equity_curve) > 1:
                ret = (equity - self.equity_curve[-2]['equity']) / self.equity_curve[-2]['equity']
                self.daily_returns.append(ret)

        if not self.equity_curve:
            for td in trading_days:
                self.equity_curve.append({'date': td, 'equity': self.initial_capital})

        return self._finalize_result(self.name, params, start_date, end_date)

    def _finalize_result(self, strategy_name: str, params: Dict,
                         start_date: str, end_date: str) -> BacktestResult:
        """计算最终指标，返回 BacktestResult"""
        closed_trades = [t for t in self.trades if t.action == 'sell']
        win_trades = [t for t in closed_trades if t.pnl > 0]
        lose_trades = [t for t in closed_trades if t.pnl <= 0]

        pnls = [t.pnl for t in closed_trades]
        success_rate = len(win_trades) / len(pnls) * 100 if pnls else 0
        avg_return = sum(pnls) / len(pnls) if pnls else 0
        max_profit = max(pnls) if pnls else 0
        max_loss = min(pnls) if pnls else 0

        if len(self.daily_returns) > 1:
            import numpy as np
            mean_ret = np.mean(self.daily_returns)
            std_ret = np.std(self.daily_returns) if np.std(self.daily_returns) > 0 else 1e-6
            sharpe = (mean_ret / std_ret) * (252 ** 0.5) if std_ret > 0 else 0
        else:
            sharpe = 0

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

        days = (datetime.strptime(end_date, '%Y-%m-%d') -
                datetime.strptime(start_date, '%Y-%m-%d')).days
        years = max(days / 365, 1/365)
        annual_return = (final_equity / self.initial_capital) ** (1/years) - 1 if years > 0 else 0

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

        extra_keys = ['a_vol', 'hk_vol', 'hk_amount', 'hk_close', 'hk_open',
                      'hk_price_at_limitup', 'pnl_by_limitup_to_close', 'price_source', 'hk_realtime']

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

    def _build_empty_result(self, msg: str, params, start, end):
        result = BacktestResult(
            strategy_name=self.name, params=params, start_date=start, end_date=end,
            total_trades=0, win_trades=0, lose_trades=0, success_rate=0,
            total_return_pct=0, avg_return_pct=0, max_profit=0, max_loss=0,
            sharpe_ratio=0, max_drawdown=0, annual_return=0,
            trades=[], equity_curve=[], monthly_returns={}
        )
        d = result.to_dict()
        d['error'] = msg
        return d


STRATEGY_CLASS = AHLimitUpStrategy
