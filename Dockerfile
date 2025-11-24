# Dockerfile.gpu
# Multi-stage: builder -> final runtime (GPU)
# Base runtime must match your host CUDA drivers (adjust tag if needed)

############################
# 1) Builder: install deps, pip wheel cache
############################
FROM python:3.11-slim AS builder

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
      build-essential \
      python3-dev \
      git \
      wget \
      curl \
      ca-certificates \
      pkg-config \
      libssl-dev \
      && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# copy requirements and let pip build wheels in builder stage
COPY requirements.txt /build/requirements.txt

RUN python -m pip install --upgrade pip wheel setuptools && \
    python -m pip wheel --wheel-dir=/build/wheels -r /build/requirements.txt

############################
# 2) Final runtime image (GPU)
############################
FROM nvidia/cuda:12.1.1-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_HOME=/app

# system deps for Blender + ffmpeg + common libs
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    wget \
    git \
    ffmpeg \
    libglib2.0-0 \
    libc6 \
    libstdc++6 \
    libgcc-s1 \
    libxcb1 \
    libx11-6 \
    libxrender1 \
    libxext6 \
    libsm6 \
    libpulse0 \
    libasound2 \
    xvfb \
    unzip \
    && rm -rf /var/lib/apt/lists/*

# create non-root user
RUN groupadd -r visora && useradd -r -s /bin/bash -g visora visora \
    && mkdir -p /home/visora/.cache /var/log/visora /workdir /app \
    && chown -R visora:visora /home/visora /var/log/visora /workdir /app

WORKDIR /app

# install python from builder wheels for speed
COPY --from=builder /build/wheels /wheels
COPY --from=builder /build/requirements.txt /app/requirements.txt

RUN python3 -m pip install --upgrade pip setuptools wheel && \
    python3 -m pip install --no-index --find-links=/wheels -r /app/requirements.txt

# --------- Install Blender (headless) ----------
# adjust version if you need other release
ENV BLENDER_VERSION=3.6.4
ENV BLENDER_DIR=/opt/blender-${BLENDER_VERSION}-linux-x64
RUN wget -q https://mirror.clarkson.edu/blender/release/Blender${BLENDER_VERSION%.*}/blender-${BLENDER_VERSION}-linux-x64.tar.xz -O /tmp/blender.tar.xz \
 && mkdir -p /opt \
 && tar -xJf /tmp/blender.tar.xz -C /opt \
 && ln -s ${BLENDER_DIR}/blender /usr/local/bin/blender \
 && rm /tmp/blender.tar.xz

# copy app code
COPY . /app
RUN chown -R visora:visora /app

# expose app port (if using web server)
EXPOSE 8000

# switch to non-root user
USER visora
ENV PATH="/home/visora/.local/bin:${PATH}"

# healthcheck (blender binary sanity + python)
HEALTHCHECK --interval=60s --timeout=5s --start-period=30s --retries=3 \
  CMD blender --version >/dev/null 2>&1 || exit 1

# Default command: worker entry (adjust if you need uvicorn)
# If you run both web and worker separate containers, change accordingly.
CMD ["python3", "worker_entry.py"]
