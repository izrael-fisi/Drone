# syntax=docker/dockerfile:1
FROM python:3.11-slim-bookworm AS runtime
ARG SOURCE_REVISION=unrecorded
LABEL org.opencontainers.image.title="Drone Vision portable module" \
      org.opencontainers.image.revision="${SOURCE_REVISION}" \
      org.opencontainers.image.description="Shadow research jobs; no flight publication"
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 \
    MAVLINK20=1 HOME=/tmp VISION_NAV_SOURCE_REVISION=${SOURCE_REVISION}
RUN apt-get update && apt-get install -y --no-install-recommends libglib2.0-0 libgl1 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /opt/vision-nav/app
COPY docker/module/requirements.txt /tmp/module-requirements.txt
RUN python -m pip install --no-cache-dir --only-binary=:all: -r /tmp/module-requirements.txt
COPY pyproject.toml README.md ./
COPY src ./src
RUN python -m pip install --no-cache-dir --no-deps . && python -m pip check \
    && python -m pip freeze > /opt/vision-nav/python-packages.txt \
    && mkdir -p /inputs /outputs && chown 1000:1000 /outputs
USER 1000:1000
ENTRYPOINT ["python", "-m", "vision_nav.deployment.cli"]
CMD ["inspect"]

# Deliberate separate opt-in GPL research target. No downloaded models/captures.
FROM runtime AS native-build
USER root
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential cmake git ca-certificates libeigen3-dev libopencv-dev \
    libboost-filesystem-dev libboost-thread-dev libboost-date-time-dev \
    libceres-dev nlohmann-json3-dev libssl-dev \
    && rm -rf /var/lib/apt/lists/*
RUN git init /opt/vision-nav/vendor/open_vins \
    && git -C /opt/vision-nav/vendor/open_vins remote add origin https://github.com/rpng/open_vins.git \
    && git -C /opt/vision-nav/vendor/open_vins fetch --depth 1 origin 69488123ed9362dd44b6f28e7f4680abbff1442b \
    && git -C /opt/vision-nav/vendor/open_vins checkout --detach FETCH_HEAD
COPY research/native/openvins /opt/vision-nav/native-adapter
RUN cmake -S /opt/vision-nav/native-adapter -B /tmp/native-build \
      -DOPENVINS_SOURCE=/opt/vision-nav/vendor/open_vins -DCMAKE_BUILD_TYPE=Release \
    && cmake --build /tmp/native-build -j 2 \
    && ctest --test-dir /tmp/native-build --output-on-failure

FROM runtime AS research-native
USER root
RUN apt-get update && apt-get install -y --no-install-recommends \
    git libopencv-core406 libopencv-imgproc406 libopencv-imgcodecs406 \
    libopencv-video406 libopencv-features2d406 libopencv-calib3d406 \
    libceres3 libboost-filesystem1.74.0 libboost-thread1.74.0 \
    libboost-date-time1.74.0 libssl3 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=native-build /tmp/native-build/vision-nav-openvins /opt/vision-nav/bin/vision-nav-openvins
# Keep source and license with the GPL research binary; .git enables provenance checks.
COPY --from=native-build /opt/vision-nav/vendor/open_vins /opt/vision-nav/vendor/open_vins
COPY research/native/openvins /opt/vision-nav/native-adapter
RUN git config --system --add safe.directory /opt/vision-nav/vendor/open_vins \
    && /opt/vision-nav/bin/vision-nav-openvins --version
USER 1000:1000
