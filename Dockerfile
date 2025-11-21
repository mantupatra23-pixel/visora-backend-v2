# Dockerfile.web
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y build-essential ffmpeg git curl wget \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . /app

# expose port
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]

# Dockerfile.worker
FROM python:3.11-slim

RUN apt-get update && apt-get install -y ffmpeg git wget \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /worker
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . /worker

CMD ["python", "worker_entry.py"]

# Dockerfile.gpu
FROM nvidia/cuda:12.1.1-runtime-ubuntu22.04

RUN apt-get update && apt-get install -y python3 python3-pip ffmpeg wget curl libglib2.0-0 \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /worker
COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

# install blender (headless)
RUN wget https://download.blender.org/release/Blender3.6/blender-3.6.4-linux-x64.tar.xz -O /tmp/blender.tar.xz \
    && tar -xJf /tmp/blender.tar.xz -C /opt \
    && ln -s /opt/blender-3.6.4-linux-x64/blender /usr/local/bin/blender

COPY . /worker

CMD ["python3", "worker_entry.py"]
