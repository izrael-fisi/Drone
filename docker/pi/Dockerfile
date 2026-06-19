FROM python:3.11-bookworm

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    cmake \
    pkg-config \
    git \
    curl \
    v4l-utils \
    libgl1 \
    libglib2.0-0 \
    libjpeg62-turbo \
    libpng16-16 \
    libtiff6 \
    sqlite3 \
    gdal-bin \
    libgdal-dev \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements/pi.txt /tmp/requirements-pi.txt
RUN python -m pip install --upgrade pip setuptools wheel \
  && python -m pip install -r /tmp/requirements-pi.txt

COPY pyproject.toml README.md ./
COPY src ./src
RUN python -m pip install -e .

WORKDIR /workspace
CMD ["python", "-m", "vision_nav.match_frame_to_map", "--help"]

