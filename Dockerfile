# ── Stage 1: Build the Vue frontend ──────────────────────────────────────────
FROM node:22-slim AS frontend-builder

WORKDIR /build/frontend
COPY frontend/package*.json ./
RUN npm ci

COPY frontend/ ./
# Copy pyproject.toml so Vite can read the version at build time
COPY pyproject.toml /build/pyproject.toml
RUN npm run build


# ── Stage 2: Runtime image (CPU-only) ────────────────────────────────────────
FROM python:3.12-slim AS runtime-cpu

# Prevent interactive prompts during apt installs
ENV DEBIAN_FRONTEND=noninteractive

# System libraries required by OpenCV, Pillow-HEIF, insightface, etc.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libgl1 \
    libglib2.0-0 \
    libgomp1 \
    libheif-dev \
    libde265-dev \
    libx265-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ── Install Python deps in a venv ─────────────────────────────────────────────
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Upgrade pip/wheel
RUN pip install --no-cache-dir --upgrade pip wheel setuptools

# PyTorch CPU-only — much smaller than the CUDA build
RUN pip install --no-cache-dir \
    torch \
    torchvision \
    --index-url https://download.pytorch.org/whl/cpu

# CPU onnxruntime
RUN pip install --no-cache-dir onnxruntime

# All other dependencies
RUN pip install --no-cache-dir \
    open_clip_torch \
    fastapi \
    "uvicorn[standard]" \
    numpy \
    pillow \
    opencv-python-headless \
    scipy \
    platformdirs \
    tomli \
    colorlog \
    httpx \
    python-multipart \
    requests \
    transformers \
    insightface \
    rapidfuzz \
    tqdm \
    einops \
    sentence_transformers \
    spacy \
    pillow-heif \
    sqlmodel \
    alembic \
    "python-jose[cryptography]" \
    passlib \
    "bcrypt<4.0.0" \
    psutil \
    piexif \
    python-dotenv \
    accelerate

# Remove build tools — not needed at runtime
RUN apt-get purge -y --auto-remove build-essential && rm -rf /var/lib/apt/lists/*

# Download spaCy English model
RUN python -m spacy download en_core_web_sm

# ── Non-root user ─────────────────────────────────────────────────────────────
RUN groupadd -f -g 10001 pixlstash \
    && useradd -r -u 10001 -g 10001 -m -d /home/pixlstash pixlstash \
    && chown -R pixlstash:pixlstash /app

# ── Copy application source ───────────────────────────────────────────────────
COPY --chown=pixlstash:pixlstash pyproject.toml setup.py MANIFEST.in alembic.ini ./
COPY --chown=pixlstash:pixlstash pixlstash/ pixlstash/

# Install the pixlstash package itself (no deps — already installed above)
RUN pip install --no-cache-dir --no-deps -e .

# Copy the pre-built frontend into the package's expected location.
# Vite outDir is ../pixlstash/frontend/dist relative to /build/frontend.
COPY --chown=pixlstash:pixlstash --from=frontend-builder /build/pixlstash/frontend/dist pixlstash/frontend/dist/

# ── Entrypoint ────────────────────────────────────────────────────────────────
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

USER pixlstash

# Ensure $HOME always points at the mounted volume regardless of which UID
# --user maps to at runtime (the UID may match a different user in /etc/passwd).
ENV HOME=/home/pixlstash

# Volume for persistent data
VOLUME ["/home/pixlstash"]

EXPOSE 9537

ENTRYPOINT ["docker-entrypoint.sh"]

# GPU image is intentionally maintained in Dockerfile.gpu.
