FROM python:3.11-slim
WORKDIR /app
RUN python -c "import urllib.request; urllib.request.urlretrieve('https://raw.githubusercontent.com/zodastream-dev/StockStrategy/main/requirements.txt', 'requirements.txt')" && \
    pip install --no-cache-dir -r requirements.txt
COPY . .
ENV PYTHONPATH=/app
EXPOSE 8080
# Railway 设置 $PORT，gunicorn 监听该端口；未设置时默认 8080
CMD gunicorn --bind 0.0.0.0:${PORT:-8080} --workers 2 strategy_platform.app:app
