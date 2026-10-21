FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/home/signal \
    HOST=0.0.0.0 \
    PORT=8000 \
    DATABASE_PATH=/var/lib/signal/news.sqlite3
WORKDIR /app
RUN groupadd --gid 10001 signal \
    && useradd --uid 10001 --gid signal --create-home --home-dir /home/signal signal \
    && mkdir -p /var/lib/signal \
    && chown signal:signal /var/lib/signal
COPY requirements.txt ./
RUN --mount=type=secret,id=build_ca \
    if [ -f /run/secrets/build_ca ]; then export PIP_CERT=/run/secrets/build_ca; fi; \
    pip install --no-cache-dir -r requirements.txt
COPY app.py database.py container_start.py ./
COPY static ./static
RUN chmod -R a+rX /app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8000')+'/api/health',timeout=3).close()"
ENTRYPOINT ["python", "/app/container_start.py"]
