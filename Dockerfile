FROM python:3.11-slim
WORKDIR /app
RUN python -c "import urllib.request; urllib.request.urlretrieve('https://raw.githubusercontent.com/zodastream-dev/StockStrategy/main/requirements.txt', 'requirements.txt')" && \
    pip install --no-cache-dir -r requirements.txt
COPY . ./strategy_platform/
ENV PYTHONPATH=/app
EXPOSE 8080
ENV PORT=8080
# Railway 用 gunicorn，端口必须用 $PORT
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "2", "strategy_platform.app:app"]
