FROM python:3.12-slim

WORKDIR /app

# System deps for tray optional; server image stays slim
RUN apt-get update && apt-get install -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md LICENSE ./
COPY src ./src
COPY web ./web
COPY docs ./docs

RUN pip install --no-cache-dir -e .

ENV FOS_HOST=0.0.0.0
ENV FOS_PORT=7420
ENV FOS_DATA_DIR=/data

EXPOSE 7420
VOLUME ["/data"]

CMD ["python", "-m", "financial_os.cli", "serve", "--host", "0.0.0.0", "--port", "7420"]
