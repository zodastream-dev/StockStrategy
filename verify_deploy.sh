#!/bin/bash
# 本地验证脚本（Railway 部署前先在本地跑通）
# 运行方式: bash verify_deploy.sh

echo "=== 1. 安装依赖 ==="
pip install -r requirements.txt

echo ""
echo "=== 2. 语法检查 ==="
python -m py_compile app.py
python -m py_compile email_notifier.py
python -m py_compile strategies/__init__.py
python -m py_compile strategies/ah_limit_up.py
python -m py_compile strategies/technical_strategies.py
echo "语法检查通过"

echo ""
echo "=== 3. 启动本地服务 ==="
echo "访问 http://127.0.0.1:5050"
python app.py
