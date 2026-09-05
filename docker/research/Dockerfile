FROM nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04

ARG PYTORCH_INDEX_URL=https://download.pytorch.org/whl/cu124

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_NO_CACHE_DIR=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-pip python3-venv git git-lfs build-essential libgl1 && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /workspace/Drone
COPY pyproject.toml ./
COPY research/requirements-research.txt ./research/requirements-research.txt
COPY src ./src
RUN python3 -m pip install --upgrade pip && \
    python3 -m pip install torch torchvision --index-url ${PYTORCH_INDEX_URL} && \
    python3 -m pip install -e ".[research,neural-runtime,map-production]" && \
    python3 -m pip install -r research/requirements-research.txt

COPY . .
ENTRYPOINT ["bash"]
