FROM python:3.12-slim-bookworm AS worker

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates ffmpeg tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY services/camera-gateway/video_analytics/*requirements.txt /tmp/requirements/
ARG ANALYTICS_REQUIREMENTS=requirements.txt
RUN pip install --no-cache-dir -r "/tmp/requirements/${ANALYTICS_REQUIREMENTS}" \
    && mkdir /state \
    && chown 10001:10001 /state

COPY services/camera-gateway/*.py /app/
COPY services/camera-gateway/video_analytics/*.py /app/video_analytics/
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER 10001:10001
ENTRYPOINT ["python", "/app/analytics_worker.py"]

FROM worker AS ecs
USER root
RUN pip install --no-cache-dir 'boto3>=1.40,<2' \
    && touch /state/.worker-volume \
    && chown 10001:10001 /state/.worker-volume
COPY --from=cctvmodels /yolox_nano.onnx /models/yolox_nano.onnx
RUN echo 'c789161ed43c8269fcd4e67c67eeeb4e80c622da2eb296a20bc6007bd18a0b7d  /models/yolox_nano.onnx' | sha256sum -c -
VOLUME ["/state"]
ENV TMPDIR=/state
USER 10001:10001
ENTRYPOINT ["python", "/app/ecs_worker.py"]

FROM worker AS local
