# otmon-mine: OTMon-style monitor + operator HMI (Python stdlib web server, no CDN)
FROM python:3.11-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY requirements.txt .
RUN for i in 1 2 3 4 5; do pip install -r requirements.txt && break; echo "pip failed, retry $i"; sleep 5; done \
 && python -c "import modbus_tk, yaml"
COPY otmon_mine ./otmon_mine
COPY config ./config
RUN useradd -r -u 10001 otmon && mkdir -p /var/lib/otmon-mine && chown otmon /var/lib/otmon-mine
USER otmon
ENV OTMON_BIND=0.0.0.0 OTMON_PORT=8088 OTMON_STATE_DIR=/var/lib/otmon-mine OTMON_CONFIG=/app/config/mine.yaml
EXPOSE 8088
HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8088/health',timeout=2).status==200 else 1)"
CMD ["python", "-m", "otmon_mine"]
