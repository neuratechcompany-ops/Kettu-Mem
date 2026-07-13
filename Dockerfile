FROM python:3.12-slim

LABEL org.opencontainers.image.title="Kettu Mem"
LABEL org.opencontainers.image.version="0.3.1"
LABEL org.opencontainers.image.description="Cognitive Memory Layer for AI agents"

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md kettu_mem.yaml ./
COPY src/ ./src/

RUN pip install --no-cache-dir . \
    && python -c "from api.server import app; print('Kettu Mem import OK')"

ENV KETTU_MEM_DATA_DIR=/data
ENV KETTU_MEM_HOST=0.0.0.0
ENV PYTHONUNBUFFERED=1

EXPOSE 8765

CMD ["python", "-m", "uvicorn", "api.server:app", "--host", "0.0.0.0", "--port", "8765"]
