FROM python:3.11-slim
WORKDIR /app
RUN python -c "import urllib.request; urllib.request.urlretrieve('https://raw.githubusercontent.com/zodastream-dev/StockStrategy/main/requirements.txt', 'requirements.txt')" && \
    pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8080
ENV PORT=8080
CMD ["python", "app.py"]
