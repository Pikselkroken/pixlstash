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
FROM python:3.12-slim

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

USER pixlstash

# ── Copy application source ───────────────────────────────────────────────────
COPY --chown=pixlstash:pixlstash pyproject.toml setup.py MANIFEST.in alembic.ini ./
COPY --chown=pixlstash:pixlstash pixlstash/ pixlstash/

# Install the pixlstash package itself (no deps — already installed above)
RUN pip install --no-cache-dir --no-deps -e .

# Copy the pre-built frontend into the package's expected location.
# Vite outDir is ../pixlstash/frontend/dist relative to /build/frontend.
COPY --chown=pixlstash:pixlstash --from=frontend-builder /build/pixlstash/frontend/dist pixlstash/frontend/dist/

# ── Entrypoint ────────────────────────────────────────────────────────────────
USER root
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


# ── Stage 3: Runtime image (GPU) ────────────────────────────────────────────────
FROM nvidia/cuda:12.8.1-cudnn-runtime-ubuntu24.04

# Prevent interactive prompts during apt installs
ENV DEBIAN_FRONTEND=noninteractive

# System libraries required by OpenCV, Pillow-HEIF, insightface, etc.
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3.12 \
    python3.12-venv \
    python3-pip \
    python3.12-dev \
    build-essential \
    libgl1 \
    libglib2.0-0 \
    libgomp1 \
    libheif-dev \
    libde265-dev \
    libx265-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Make python3.12 the default python3/python
RUN update-alternatives --install /usr/bin/python python /usr/bin/python3.12 1 \
    && update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.12 1

WORKDIR /app

# ── Install Python deps in a venv ─────────────────────────────────────────────
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Upgrade pip/wheel
RUN pip install --no-cache-dir --upgrade pip wheel setuptools

# PyTorch with CUDA 12.8 — required for Blackwell (RTX 5xxx / sm_120) support.
# Must be installed before open_clip_torch pulls in a CPU-only torch.
RUN pip install --no-cache-dir \
    torch \
    torchvision \
    --index-url https://download.pytorch.org/whl/cu128

# onnxruntime-gpu replaces plain onnxruntime for CUDA inference
RUN pip install --no-cache-dir onnxruntime-gpu

# All other dependencies (onnxruntime already satisfied by onnxruntime-gpu above)
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
    nvidia-ml-py \
    piexif \
    psutil \
    python-dotenv \
    accelerate

# Remove build tools — not needed at runtime
RUN apt-get purge -y --auto-remove build-essential && rm -rf /var/lib/apt/lists/*

# Download spaCy English model
RUN python -m spacy download en_core_web_sm

# ── Non-root user ─────────────────────────────────────────────────────────────
# Run as a non-root user for security.  UID/GID 10001 avoids conflicts with
# UIDs pre-allocated in the nvidia/cuda base image (which already uses 1000).
RUN groupadd -f -g 10001 pixlstash \
    && useradd -r -u 10001 -g 10001 -m -d /home/pixlstash pixlstash \
    && chown -R pixlstash:pixlstash /app

USER pixlstash

# ── Copy application source ───────────────────────────────────────────────────
COPY --chown=pixlstash:pixlstash pyproject.toml setup.py MANIFEST.in alembic.ini ./
COPY --chown=pixlstash:pixlstash pixlstash/ pixlstash/

# Install the pixlstash package itself (no deps — already installed above)
RUN pip install --no-cache-dir --no-deps -e .

# Copy the pre-built frontend into the package's expected location.
# Vite outDir is ../pixlstash/frontend/dist relative to /build/frontend.
COPY --chown=pixlstash:pixlstash --from=frontend-builder /build/pixlstash/frontend/dist pixlstash/frontend/dist/

# ── Entrypoint ────────────────────────────────────────────────────────────────
# Entrypoint is installed as root so it can be found on PATH, then we switch
# back to the non-root user for the actual process.
USER root
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh
USER pixlstash

# Ensure $HOME always points at the mounted volume regardless of which UID
# --user maps to at runtime (the UID may match a different user in /etc/passwd).
ENV HOME=/home/pixlstash

# Volume for persistent data — mount /home/pixlstash to persist config, images,
# downloaded models, and the database across container restarts.
VOLUME ["/home/pixlstash"]

EXPOSE 9537

ENTRYPOINT ["docker-entrypoint.sh"]
