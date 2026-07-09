FROM python:3.12-slim

LABEL org.opencontainers.image.title="Kettu Mem"
LABEL org.opencontainers.image.version="0.2.0"
LABEL org.opencontainers.image.description="Cognitive Memory Layer for OpenClaw agents"

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# App
WORKDIR /app

# Install Python deps
COPY pyproject.toml .
RUN pip install --no-cache-dir \
    fastapi uvicorn[standard] pydantic-settings structlog \
    prometheus-client psutil python-multipart \
    numpy faiss-cpu tiktoken openai

# Copy source
COPY kettu_mem.yaml .
COPY src/ ./src/

# Health check
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8765/health')"

EXPOSE 8765

ENV KETTU_MEM_DATA_DIR=/data
ENV KETTU_MEM_HOST=0.0.0.0

CMD ["python", "-m", "uvicorn", "api.server:app", "--host", "0.0.0.0", "--port", "8765"]
