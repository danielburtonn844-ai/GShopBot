FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Shanghai

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime \
    && echo $TZ > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY gshop ./gshop
COPY main.py VERSION ./

RUN mkdir -p /app/data /app/logs /app/backups

ENV HEALTH_FILE=/app/data/.health

HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import os,sys; sys.exit(0 if os.path.exists(os.getenv('HEALTH_FILE', '/tmp/gshop.health')) else 1)"]

CMD ["python", "-u", "main.py"]
