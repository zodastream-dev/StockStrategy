"""
策略回测平台 - Flask后端
"""
__version__ = "1.0.3"

import sys
import io
import os
import json
import uuid
import time
import traceback
import threading
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor
from flask import Flask, render_template, request, jsonify

# Flask会在请求处理时处理编码，这里不需要额外的stdout重定向

app = Flask(__name__)
app.config['JSON_AS_ASCII'] = False
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024

# ========== 异步任务管理器 ==========
_executor = ThreadPoolExecutor(max_workers=3)

class TaskManager:
    """线程安全的异步任务管理器"""
    def __init__(self, max_age_seconds=3600):
        self._tasks = {}
        self._lock = threading.Lock()
        self._max_age = max_age_seconds

    def submit(self, fn, *args, **kwargs) -> str:
        task_id = uuid.uuid4().hex[:12]
        task = {
            'id': task_id,
            'status': 'pending',   # pending | running | done | error
            'progress': 0,
            'message': '任务已提交',
            'result': None,
            'error': None,
            'start_time': None,
            'end_time': None,
        }
        with self._lock:
            self._tasks[task_id] = task
        # 在后台线程执行
        _executor.submit(self._run, task_id, fn, args, kwargs)
        return task_id

    def _run(self, task_id, fn, args, kwargs):
        with self._lock:
            self._tasks[task_id]['status'] = 'running'
            self._tasks[task_id]['start_time'] = time.time()
            self._tasks[task_id]['message'] = '正在执行...'
        try:
            result = fn(*args, **kwargs)
            with self._lock:
                self._tasks[task_id]['status'] = 'done'
                self._tasks[task_id]['progress'] = 100
                self._tasks[task_id]['result'] = result
                self._tasks[task_id]['message'] = '完成'
                self._tasks[task_id]['end_time'] = time.time()
        except Exception as e:
            with self._lock:
                self._tasks[task_id]['status'] = 'error'
                self._tasks[task_id]['error'] = str(e)
                self._tasks[task_id]['trace'] = traceback.format_exc()
                self._tasks[task_id]['message'] = f'错误: {e}'
                self._tasks[task_id]['end_time'] = time.time()
        # 清理过期任务
        self._cleanup()

    def get(self, task_id: str) -> dict:
        with self._lock:
            task = self._tasks.get(task_id)
            if task:
                return dict(task)
        return None

    def _cleanup(self):
        now = time.time()
        expired = [k for k, v in self._tasks.items()
                   if v['end_time'] and (now - v['end_time']) > self._max_age]
        for k in expired:
            del self._tasks[k]

_task_manager = TaskManager()

# 注册策略（绝对导入，兼容 python app.py 直接运行）
import importlib, sys as _sys
# 将当前目录加入 sys.path，确保绝对导入可用
_here = os.path.dirname(os.path.abspath(__file__))
if _here not in _sys.path:
    _sys.path.insert(0, _here)

try:
    from strategies import BaseStrategy
    from strategies.ah_limit_up import AHLimitUpStrategy
    from strategies.technical_strategies import (
        MACDCrossStrategy, BreakoutStrategy,
        RSIOversoldStrategy, DualMAStrategy
    )
except ImportError:
    try:
        from strategies import BaseStrategy
        from strategies.ah_limit_up import AHLimitUpStrategy
        from strategies.technical_strategies import (
            MACDCrossStrategy, BreakoutStrategy,
            RSIOversoldStrategy, DualMAStrategy
        )
    except ImportError:
        from .strategies import BaseStrategy
        from .strategies.ah_limit_up import AHLimitUpStrategy
        from .strategies.technical_strategies import (
            MACDCrossStrategy, BreakoutStrategy,
            RSIOversoldStrategy, DualMAStrategy
        )

STRATEGIES = {
    'ah_limit_up': AHLimitUpStrategy,
    'macd_cross': MACDCrossStrategy,
    'breakout': BreakoutStrategy,
    'rsi_oversold': RSIOversoldStrategy,
    'dual_ma': DualMAStrategy,
}


@app.route('/diag')
def diag():
    return render_template('diag.html')


@app.route('/')
def index():
    return render_template('index.html')

@app.route('/chart-test')
def chart_test():
    return render_template('chart_test.html')

@app.route('/debug-charts')
def debug_charts():
    return render_template('debug_charts.html')

@app.route('/debug-adapter')
def debug_adapter():
    return render_template('debug_adapter.html')


@app.route('/api/version')
def get_version():
    """返回当前版本号"""
    return jsonify({"version": __version__})


@app.route('/api/strategies')
def list_strategies():
    """返回所有可用策略列表"""
    result = []
    for sid, cls in STRATEGIES.items():
        result.append({
            'id': sid,
            'name': cls.name,
            'description': cls.description,
            'params': cls.params_schema
        })
    return jsonify({'strategies': result})


@app.route('/api/stocks/search')
def search_stocks():
    """搜索股票"""
    import akshare as ak
    keyword = request.args.get('q', '')
    if len(keyword) < 1:
        return jsonify({'stocks': []})

    try:
        df = ak.stock_info_a_code_name()
        df.columns = ['code', 'name']
        df['code'] = df['code'].astype(str).str.zfill(6)
        matched = df[df['name'].str.contains(keyword, na=False) | df['code'].str.contains(keyword, na=False)]
        stocks = matched.head(20).to_dict('records')
        return jsonify({'stocks': stocks})
    except Exception as e:
        return jsonify({'stocks': [], 'error': str(e)})


@app.route('/api/stocks/ah-mapping')
def get_ah_mapping():
    """获取A+H配对股列表（供选择标的）"""
    import akshare as ak
    csv_path = os.path.join(os.path.dirname(__file__), 'ah_mapping_final.csv')
    if os.path.exists(csv_path):
        import pandas as pd
        df = pd.read_csv(csv_path, dtype={'hk_code': str, 'a_code': str})
        stocks = [{'code': row['a_code'], 'name': row['a_name'], 'hk_code': row['hk_code'], 'hk_name': row['hk_name']}
                   for _, row in df.iterrows()]
        return jsonify({'stocks': stocks, 'total': len(stocks)})

    try:
        ah_hk = ak.stock_zh_ah_spot()
        stocks = []
        for _, row in ah_hk.iterrows():
            name = str(row.get('名称', row.get('name', '')))
            hk_code = str(row.get('代码', row.get('code', '')))
            if name and hk_code:
                stocks.append({'name': name, 'hk_code': hk_code.zfill(5)})
        return jsonify({'stocks': stocks[:50], 'total': len(stocks)})
    except Exception as e:
        return jsonify({'stocks': [], 'error': str(e)})


def _do_backtest(strategy_id: str, stock_code: str, start_date: str, end_date: str, params: dict):
    """后台执行回测的函数"""
    strategy_cls = STRATEGIES[strategy_id]
    strategy = strategy_cls(initial_capital=100000)
    result = strategy.run(stock_code, start_date, end_date, params)
    if isinstance(result, dict):
        return result
    return result.to_dict()


@app.route('/api/backtest', methods=['POST'])
def run_backtest():
    """提交回测任务（异步，立即返回 task_id）"""
    data = request.get_json()
    strategy_id = data.get('strategy_id', '')
    stock_code = data.get('stock_code', '')
    start_date = data.get('start_date', '')
    end_date = data.get('end_date', '')
    params = data.get('params', {})

    if strategy_id not in STRATEGIES:
        return jsonify({'error': f'未知策略: {strategy_id}'}), 400

    if not start_date or not end_date:
        end_date = datetime.now().strftime('%Y-%m-%d')
        start_date = (datetime.now() - timedelta(days=60)).strftime('%Y-%m-%d')

    task_id = _task_manager.submit(
        _do_backtest, strategy_id, stock_code, start_date, end_date, params
    )
    return jsonify({
        'task_id': task_id,
        'message': '任务已提交，请通过 /api/backtest/status/<task_id> 查询结果'
    })


@app.route('/api/backtest/status/<task_id>')
def backtest_status(task_id: str):
    """查询回测任务状态"""
    task = _task_manager.get(task_id)
    if task is None:
        return jsonify({'error': '任务不存在或已过期'}), 404
    return jsonify(task)


@app.route('/api/stock-info')
def get_stock_info():
    """获取股票基本信息"""
    import akshare as ak
    code = request.args.get('code', '')
    if not code:
        return jsonify({'error': '缺少股票代码'})

    try:
        info = ak.stock_individual_info_em(symbol=code)
        info_dict = {row['item']: row['value'] for _, row in info.iterrows()} if info is not None else {}
        return jsonify({'info': info_dict})
    except Exception as e:
        return jsonify({'info': {}, 'error': str(e)})


@app.route('/api/market-overview')
def market_overview():
    """获取市场概览（大盘指数）"""
    import akshare as ak
    try:
        indices = {
            'sh000001': '上证指数',
            'sz399001': '深证成指',
            'sz399006': '创业板指',
            'sh000300': '沪深300',
        }
        result = {}
        for code, name in indices.items():
            try:
                df = ak.stock_zh_index_daily(symbol=code)
                if df is not None and not df.empty:
                    latest = df.iloc[-1]
                    prev = df.iloc[-2] if len(df) > 1 else latest
                    change = (latest['close'] - prev['close']) / prev['close'] * 100
                    result[code] = {
                        'name': name,
                        'price': round(latest['close'], 2),
                        'change_pct': round(change, 2),
                        'volume': round(latest.get('volume', 0) / 1e8, 2) if 'volume' in df.columns else 0
                    }
            except:
                pass
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)})


# 监控港股列表
HK_WATCH_LIST = [
    {'code': '03690', 'name': '美团'},
    {'code': '09988', 'name': '阿里巴巴'},
    {'code': '06669', 'name': '胜宏科技'},
    {'code': '09992', 'name': '泡泡玛特'},
    {'code': '02589', 'name': '沪上阿姨'},
]

@app.route('/api/hk-stocks')
def get_hk_stocks():
    """获取港股实时行情（监控列表）"""
    import akshare as ak
    result = []

    # 方案1：用 stock_hk_spot_em 批量获取实时行情（Railway环境可用）
    try:
        df = ak.stock_hk_spot_em()
        col_code = df.columns[1]
        col_name = df.columns[2]
        # 列名映射（东方财富港股字段）
        col_map = {c: i for i, c in enumerate(df.columns)}

        codes = [s['code'] for s in HK_WATCH_LIST]
        code_name_map = {s['code']: s['name'] for s in HK_WATCH_LIST}

        df['_code5'] = df[col_code].astype(str).str.zfill(5)
        matched = df[df['_code5'].isin(codes)].copy()

        for _, row in matched.iterrows():
            code5 = str(row[col_code]).zfill(5)
            # 东方财富港股实时列：序号/代码/名称/最新价/涨跌额/涨跌幅/今开/最高/最低/昨收/成交量/成交额
            try:
                price = float(row.iloc[3])   # 最新价
                prev_close = float(row.iloc[9])   # 昨收
                change_pct = round((price - prev_close) / prev_close * 100, 2) if prev_close else 0
            except Exception:
                price = None
                change_pct = 0
            result.append({
                'code': code5,
                'name': code_name_map.get(code5, row[col_name]),
                'price': round(price, 3) if price else None,
                'change_pct': change_pct,
                'source': 'realtime',
            })

        # 按监控列表顺序排序
        order = {s['code']: i for i, s in enumerate(HK_WATCH_LIST)}
        result.sort(key=lambda x: order.get(x['code'], 99))

        if result:
            return jsonify({'stocks': result})
    except Exception:
        pass

    # 方案2：逐只用 stock_hk_daily 取最新收盘（兜底）
    for stock in HK_WATCH_LIST:
        try:
            df = ak.stock_hk_daily(symbol=stock['code'], adjust="")
            if df is not None and not df.empty:
                latest = df.iloc[-1]
                prev = df.iloc[-2] if len(df) > 1 else latest
                price = float(latest['close'])
                prev_close = float(prev['close'])
                change_pct = round((price - prev_close) / prev_close * 100, 2)
                result.append({
                    'code': stock['code'],
                    'name': stock['name'],
                    'price': round(price, 3),
                    'change_pct': change_pct,
                    'source': 'daily',
                })
        except Exception:
            result.append({
                'code': stock['code'],
                'name': stock['name'],
                'price': None,
                'change_pct': 0,
                'source': 'error',
            })

    return jsonify({'stocks': result})


# ============================================================
# 持久化配置（JSON 文件，支持环境变量覆盖）
# ============================================================
import json as _json

CONFIG_FILE = os.path.join(os.path.dirname(__file__), 'config.json')

# 环境变量优先（Railway 部署时用环境变量更安全）
_default_email_config = {
    'smtp_host': os.environ.get('SMTP_HOST', 'smtp.qq.com'),
    'smtp_port': int(os.environ.get('SMTP_PORT', 465)),
    'sender_email': os.environ.get('SMTP_EMAIL', ''),
    'sender_password': os.environ.get('SMTP_PASSWORD', ''),
}


def _load_persistent_config():
    """从 JSON 文件加载配置，环境变量优先级最高"""
    defaults = {
        'email': _default_email_config.copy(),
        'alert': {
            'enabled': False,
            'receiver_email': '',
            'threshold': 4000,
            'last_sent_time': None,
        }
    }
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                saved = _json.load(f)
            # 合并：文件有则用文件，否则用环境变量/默认值
            for key in ['email', 'alert']:
                if key in saved:
                    defaults[key].update(saved[key])
        except Exception:
            pass
    # 环境变量永远覆盖文件（适合 Railway 部署）
    if os.environ.get('SMTP_EMAIL'):
        defaults['email']['sender_email'] = os.environ['SMTP_EMAIL']
    if os.environ.get('SMTP_PASSWORD'):
        defaults['email']['sender_password'] = os.environ['SMTP_PASSWORD']
    if os.environ.get('SMTP_HOST'):
        defaults['email']['smtp_host'] = os.environ['SMTP_HOST']
    if os.environ.get('SMTP_PORT'):
        defaults['email']['smtp_port'] = int(os.environ['SMTP_PORT'])
    return defaults


def _save_config(data: dict):
    """保存配置到 JSON 文件"""
    try:
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            _json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"配置保存失败: {e}")


# 全局配置（启动时从文件加载）
_config = _load_persistent_config()
_email_config = _config['email']
alert_config = _config['alert']


# 启动时初始化邮件通知器（SendGrid优先，SMTP兜底）
if _email_config.get('sender_email'):
    try:
        from email_notifier import init_email_notifier
    except (ImportError, ModuleNotFoundError):
        from strategy_platform.email_notifier import init_email_notifier
    init_email_notifier(
        smtp_host=_email_config.get('smtp_host'),
        smtp_port=_email_config.get('smtp_port', 465),
        sender_email=_email_config.get('sender_email'),
        sender_password=_email_config.get('sender_password'),
        sendgrid_api_key=os.environ.get('SENDGRID_API_KEY')
    )


@app.route('/api/email/config', methods=['GET'])
def get_email_config():
    """获取邮件配置状态"""
    return jsonify({
        'configured': bool(_email_config.get('sender_email') and _email_config.get('sender_password')),
        'has_sender': bool(_email_config.get('sender_email')),
        'smtp_host': _email_config.get('smtp_host'),
        'smtp_port': _email_config.get('smtp_port'),
    })


@app.route('/api/email/config', methods=['POST'])
def set_email_config():
    """设置邮件配置"""
    global _email_config
    data = request.get_json()

    _email_config['smtp_host'] = data.get('smtp_host', 'smtp.qq.com')
    _email_config['smtp_port'] = int(data.get('smtp_port', 465))
    _email_config['sender_email'] = data.get('sender_email', '')
    _email_config['sender_password'] = data.get('sender_password', '')

    if _email_config['sender_email'] and _email_config['sender_password']:
        try:
            from email_notifier import init_email_notifier
        except (ImportError, ModuleNotFoundError):
            from strategy_platform.email_notifier import init_email_notifier
        init_email_notifier(
            smtp_host=_email_config.get('smtp_host'),
            smtp_port=_email_config.get('smtp_port', 465),
            sender_email=_email_config.get('sender_email'),
            sender_password=_email_config.get('sender_password'),
            sendgrid_api_key=os.environ.get('SENDGRID_API_KEY')
        )
        _email_config['configured'] = True
    else:
        _email_config['configured'] = False

    # 持久化（不保存密码到文件，仅环境变量方式）
    _save_config({'email': _email_config, 'alert': alert_config})
    return jsonify({'success': True, 'message': '邮件配置已保存（SendGrid优先，SMTP兜底）'})


@app.route('/api/email/test', methods=['POST'])
def test_email():
    """测试邮件发送"""
    data = request.get_json()
    test_email_addr = data.get('email', '')

    if not test_email_addr:
        return jsonify({'success': False, 'message': '请提供测试邮箱地址'})

    if not _email_config.get('sender_email') or not _email_config.get('sender_password'):
        return jsonify({'success': False, 'message': '请先配置SMTP信息'})

    try:
        from email_notifier import get_email_notifier
    except (ImportError, ModuleNotFoundError):
        from strategy_platform.email_notifier import get_email_notifier
    notifier = get_email_notifier()

    result = notifier.send_email(
        to_email=test_email_addr,
        subject='📈 A+H策略平台 - 邮件通知测试',
        html_content='''
        <h2>✅ 邮件通知功能测试成功！</h2>
        <p>如果您收到此邮件，说明 A+H策略回测平台 的邮件通知功能已配置正确。</p>
        <p>现在您可以在策略触发时接收实时邮件提醒了！</p>
        <hr>
        <p style="color:#888;font-size:12px">此邮件由系统自动发送，请勿回复。</p>
        '''
    )

    return jsonify(result)



# ============================================================
# 上证指数预警配置（已通过 _config 全局变量加载，无需重复定义）
# ============================================================


@app.route('/api/alert/config', methods=['GET'])
def get_alert_config():
    """获取预警配置"""
    return jsonify({
        'enabled': alert_config['enabled'],
        'receiver_email': alert_config['receiver_email'],
        'threshold': alert_config['threshold'],
    })


@app.route('/api/alert/config', methods=['POST'])
def set_alert_config():
    """保存预警配置"""
    global alert_config
    data = request.get_json()
    alert_config['enabled'] = bool(data.get('enabled', False))
    alert_config['receiver_email'] = data.get('receiver_email', '').strip()
    alert_config['threshold'] = float(data.get('threshold', 4000))
    # 持久化到文件（不含 last_sent_time）
    save_data = {'email': {k: v for k, v in _email_config.items()}, 'alert': {
        'enabled': alert_config['enabled'],
        'receiver_email': alert_config['receiver_email'],
        'threshold': alert_config['threshold'],
        'last_sent_time': alert_config.get('last_sent_time'),
    }}
    _save_config(save_data)
    return jsonify({'success': True, 'message': '预警配置已保存'})




@app.route('/api/alert/sh-index', methods=['GET'])
def get_sh_index():
    """获取上证指数当前点位"""
    import akshare as ak
    try:
        df = ak.stock_zh_index_daily(symbol='sh000001')
        if df is not None and not df.empty:
            latest = df.iloc[-1]
            prev = df.iloc[-2] if len(df) > 1 else latest
            change = round((latest['close'] - prev['close']) / prev['close'] * 100, 2)
            return jsonify({
                'name': '上证指数',
                'code': 'sh000001',
                'price': round(latest['close'], 2),
                'change_pct': change,
                'date': str(latest.name)[:10] if hasattr(latest.name, '__str__') else str(latest.get('date', ''))[:10],
            })
    except Exception as e:
        pass
    return jsonify({'error': '获取上证指数失败', 'price': None})


@app.route('/api/alert/check', methods=['POST'])
def check_and_alert():
    """检查上证指数，超过阈值则发送邮件"""
    if not alert_config['enabled']:
        return jsonify({'success': False, 'message': '预警未启用', 'triggered': False})

    if not alert_config['receiver_email']:
        return jsonify({'success': False, 'message': '未设置通知邮箱', 'triggered': False})

    if not _email_config.get('sender_email') or not _email_config.get('sender_password'):
        return jsonify({'success': False, 'message': 'SMTP未配置', 'triggered': False})

    # 获取上证指数
    import akshare as ak
    try:
        df = ak.stock_zh_index_daily(symbol='sh000001')
        if df is None or df.empty:
            return jsonify({'success': False, 'message': '获取指数数据失败', 'triggered': False})
        latest = df.iloc[-1]
        sh_price = round(latest['close'], 2)
    except Exception as e:
        return jsonify({'success': False, 'message': f'获取失败: {str(e)}', 'triggered': False})

    triggered = sh_price >= alert_config['threshold']

    if triggered:
        # 防止重复发送（同一个交易日只发一次）
        from datetime import datetime
        today = datetime.now().strftime('%Y-%m-%d')
        if alert_config['last_sent_time'] == today:
            return jsonify({
                'success': True,
                'message': f'今日已发送过通知（{today}）',
                'triggered': True,
                'sh_price': sh_price,
                'already_sent': True,
            })

        try:
            from email_notifier import get_email_notifier
        except (ImportError, ModuleNotFoundError):
            from strategy_platform.email_notifier import get_email_notifier
        notifier = get_email_notifier()
        result = notifier.send_email(
            to_email=alert_config['receiver_email'],
            subject=f'📈 上证指数预警 - 当前 {sh_price} 点',
            html_content=f'''
            <h2>📈 上证指数预警通知</h2>
            <p><strong>触发时间：</strong>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
            <p><strong>当前上证指数：</strong><span style="font-size:24px;color:#c33;font-weight:bold">{sh_price}</span></p>
            <p><strong>预警阈值：</strong>{alert_config['threshold']} 点</p>
            <p>上证指数已突破 <strong>{alert_config['threshold']}</strong> 点，当前为 <strong>{sh_price}</strong> 点，请关注市场动态。</p>
            <hr>
            <p style="color:#888;font-size:12px">此邮件由 A+H策略回测平台 自动发送，请勿回复。</p>
            ''',
        )
        if result['success']:
            alert_config['last_sent_time'] = today
        return jsonify({**result, 'triggered': True, 'sh_price': sh_price})

    return jsonify({
        'success': True,
        'message': f'上证指数 {sh_price} 点，未超过阈值 {alert_config["threshold"]}',
        'triggered': False,
        'sh_price': sh_price,
    })


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5050))
    print(f"启动策略回测平台 http://0.0.0.0:{port}")
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)
