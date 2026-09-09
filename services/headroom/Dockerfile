FROM python:3.11-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends curl git build-essential && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir "headroom-ai[all]"
EXPOSE 8787
ENV HEADROOM_TELEMETRY=off
CMD ["headroom", "proxy", "--host", "0.0.0.0", "--port", "8787"]
