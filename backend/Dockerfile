# Build context: repo root (see docker-compose.yml)
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY backend/requirements.txt .
RUN pip install -r requirements.txt \
 && playwright install --with-deps --only-shell chromium \
 && rm -rf /var/lib/apt/lists/* /root/.cache

COPY backend/app ./app
COPY frontend ./frontend

RUN useradd -m agent && chown -R agent /app
USER agent

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/api/health')"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--proxy-headers", "--forwarded-allow-ips", "*"]
