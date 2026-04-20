FROM python:3.12-slim

WORKDIR /app

# Install all dependencies inline (no COPY required)
RUN pip install --no-cache-dir \
    flask>=3.0.0 \
    akshare>=1.14.0 \
    pandas>=2.0.0 \
    numpy>=1.26.0 \
    requests>=2.31.0 \
    gunicorn>=21.0.0

# Copy application code
COPY app.py .
COPY strategies ./strategies
COPY templates ./templates
COPY __init__.py .
COPY ah_mapping_final.csv .
COPY email_notifier.py .
COPY launch.bat .
COPY Procfile .
COPY runtime.txt .
COPY verify_deploy.sh .

EXPOSE 8080

ENV PORT=8080

CMD ["python", "-m", "gunicorn", "app:app", "--bind", "0.0.0.0:8080", "--workers", "2"]
