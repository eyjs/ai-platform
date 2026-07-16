FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml .
# 상용 extra(openai/anthropic)는 2026-07-16 퇴역. langchain-openai 는 core 로 옮겼다 —
# DGX ollama /v1 shim 과 MLX 를 치는 클라이언트라 서빙에 반드시 필요하다.
RUN pip install --no-cache-dir "."

COPY . .

EXPOSE 8000

# 기본값: API 서버. worker는 docker-compose에서 command로 override.
CMD ["python", "-m", "uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
