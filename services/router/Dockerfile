FROM python:3.11-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl git && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY src/ ./src/
COPY tests/ ./tests/
RUN chmod +x /app/src/mcp_workspace.py && ln -sf /app/src/mcp_workspace.py /app/mcp_workspace.py
RUN mkdir -p /home/appuser/.gemini /workspace && chown -R 1000:1000 /home/appuser /workspace /app
ENV HOME=/home/appuser
ENV PYTHONPATH=/app/src
EXPOSE 8000
CMD ["uvicorn", "src.app:app", "--host", "0.0.0.0", "--port", "8000"]
