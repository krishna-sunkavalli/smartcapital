FROM python:3.12-slim

WORKDIR /app

# Install deps first for layer caching
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

COPY config.example.yaml ./config.example.yaml

# State persists across restarts via a mounted volume (Azure Files on ACA).
ENV STATE_FILE=/data/state.json

CMD ["smartcapital", "run"]
