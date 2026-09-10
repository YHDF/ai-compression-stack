FROM python:3.11-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl git && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py mcp_workspace.py .
RUN chmod +x /app/mcp_workspace.py
RUN mkdir -p /home/appuser/.gemini /workspace && chown -R 1000:1000 /home/appuser /workspace /app
ENV HOME=/home/appuser
EXPOSE 8000
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
