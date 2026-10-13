# Stage 1: build
FROM python:3.12-slim AS builder
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src/ ./src/
RUN pip install --no-cache-dir --prefix=/install .

# Stage 2: runtime
FROM python:3.12-slim
LABEL org.opencontainers.image.source="https://github.com/Street3r/tak-feeder-vr" \
      org.opencontainers.image.description="Finnish railway traffic (Digitraffic) to TAK" \
      org.opencontainers.image.licenses="GPL-3.0-or-later"
COPY --from=builder /install /usr/local
COPY LICENSE README.md /app/
# Runs as root like the other feeders, so root-only key files mounted
# from the TAK stack stay readable.
ENV PYTHONUNBUFFERED=1
WORKDIR /app
ENTRYPOINT ["python", "-m", "tak_feeder_vr"]
