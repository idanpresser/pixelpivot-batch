# --- Stage 1: Node.js Dependencies ---
FROM node:20-slim AS node-builder
WORKDIR /app/services/sharp-daemon
COPY services/sharp-daemon/package*.json ./
RUN npm install --omit=dev

# --- Stage 2: cavif binary (Rust; not in Debian repos) ---
FROM rust:slim AS cavif-builder
RUN apt-get update && apt-get install -y nasm && rm -rf /var/lib/apt/lists/*
RUN cargo install cavif

# --- Stage 3: Final Production Image ---
FROM python:3.14-slim

# Install system dependencies
# libheif-dev for AVIF, libvips-dev for Sharp/Vips, ffmpeg for FFmpeg tools
RUN apt-get update && apt-get install -y \
    imagemagick \
    libheif-dev \
    libvips-dev \
    libvips-tools \
    ffmpeg \
    curl \
    procps \
    sqlite3 \
    && rm -rf /var/lib/apt/lists/*

# Install Node.js runtime (required for Sharp daemon)
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y nodejs && \
    rm -rf /var/lib/apt/lists/*

# cavif binary from Rust builder stage
COPY --from=cavif-builder /usr/local/cargo/bin/cavif /usr/local/bin/cavif

WORKDIR /app

# Install Python dependencies
COPY pyproject.toml .
RUN pip install --no-cache-dir ".[dev,gui,postgres]"

# Copy Node.js dependencies from builder
COPY --from=node-builder /app/services/sharp-daemon/node_modules ./services/sharp-daemon/node_modules
COPY services/sharp-daemon/package*.json ./services/sharp-daemon/

# Copy application code
COPY . .

# Environment configuration
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1
ENV IS_DOCKER=true

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
  CMD curl -f http://localhost:8000/ || exit 1

EXPOSE 8000 8503

# Default entrypoint
CMD ["uvicorn", "app.batch_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
