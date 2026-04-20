"""
策略回测平台 - Flask后端
"""
import sys
import io
from flask import Flask, render_template, request, jsonify
from datetime import datetime, timedelta
import json
import os

# Flask会在请求处理时处理编码，这里不需要额外的stdout重定向

app = Flask(__name__)
app.config['JSON_AS_ASCII'] = False
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024

# 注册策略
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


@app.route('/api/backtest', methods=['POST'])
def run_backtest():
    """运行回测"""
    data = request.get_json()
    strategy_id = data.get('strategy_id', '')
    stock_code = data.get('stock_code', '')
    start_date = data.get('start_date', '')
    end_date = data.get('end_date', '')
    params = data.get('params', {})

    if strategy_id not in STRATEGIES:
        return jsonify({'error': f'未知策略: {strategy_id}'}), 400

    if not start_date or not end_date:
        # 默认最近1个月
        end_date = datetime.now().strftime('%Y-%m-%d')
        start_date = (datetime.now() - timedelta(days=60)).strftime('%Y-%m-%d')

    try:
        strategy_cls = STRATEGIES[strategy_id]
        strategy = strategy_cls(initial_capital=100000)
        result = strategy.run(stock_code, start_date, end_date, params)
        # A+H策略已直接返回 dict，其他策略返回 BacktestResult
        if isinstance(result, dict):
            return jsonify(result)
        return jsonify(result.to_dict())
    except Exception as e:
        import traceback
        return jsonify({'error': str(e), 'trace': traceback.format_exc()}), 500


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


# 邮件配置存储（生产环境建议使用数据库）
email_config = {
    'smtp_host': 'smtp.qq.com',
    'smtp_port': 465,
    'sender_email': '',
    'sender_password': '',
    'configured': False
}


@app.route('/api/email/config', methods=['GET'])
def get_email_config():
    """获取邮件配置状态"""
    return jsonify({
        'configured': email_config['configured'],
        'has_sender': bool(email_config['sender_email']),
        'smtp_host': email_config['smtp_host'],
        'smtp_port': email_config['smtp_port'],
    })


@app.route('/api/email/config', methods=['POST'])
def set_email_config():
    """设置邮件配置"""
    data = request.get_json()

    email_config['smtp_host'] = data.get('smtp_host', 'smtp.qq.com')
    email_config['smtp_port'] = data.get('smtp_port', 465)
    email_config['sender_email'] = data.get('sender_email', '')
    email_config['sender_password'] = data.get('sender_password', '')

    if email_config['sender_email'] and email_config['sender_password']:
        # 初始化邮件通知器
        from .email_notifier import init_email_notifier
        init_email_notifier(
            smtp_host=email_config['smtp_host'],
            smtp_port=email_config['smtp_port'],
            sender_email=email_config['sender_email'],
            sender_password=email_config['sender_password']
        )
        email_config['configured'] = True
        return jsonify({'success': True, 'message': '邮件配置已保存'})
    else:
        email_config['configured'] = False
        return jsonify({'success': False, 'message': '请填写完整信息'})


@app.route('/api/email/test', methods=['POST'])
def test_email():
    """测试邮件发送"""
    data = request.get_json()
    test_email_addr = data.get('email', '')

    if not test_email_addr:
        return jsonify({'success': False, 'message': '请提供测试邮箱地址'})

    if not email_config['configured']:
        return jsonify({'success': False, 'message': '请先配置SMTP信息'})

    from .email_notifier import get_email_notifier
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


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5050))
    print(f"启动策略回测平台 http://0.0.0.0:{port}")
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)
