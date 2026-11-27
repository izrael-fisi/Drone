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
ARG CCTV_GROUND_MODEL=yolo11s
ARG CCTV_GROUND_MODEL_SHA256
ENV PROXIGO_CCTV_GROUND_MODEL=${CCTV_GROUND_MODEL}
ENV PROXIGO_CCTV_GROUND_MODEL_SHA256=${CCTV_GROUND_MODEL_SHA256}
COPY --from=cctvmodels /${CCTV_GROUND_MODEL}.onnx /models/${CCTV_GROUND_MODEL}.onnx
RUN python -c 'import os; from ecs_worker import ground_model; from video_analytics.detectors import fingerprint; model = ground_model(os.environ["PROXIGO_CCTV_GROUND_MODEL"], os.environ["PROXIGO_CCTV_GROUND_MODEL_SHA256"]); fingerprint(model["path"], model["sha256"])'
VOLUME ["/state"]
ENV TMPDIR=/state
USER 10001:10001
ENTRYPOINT ["python", "/app/ecs_worker.py"]

FROM ecs AS ecs-pose
USER root
ARG CCTV_DETAIL_POSE_MODEL=yolo11l-pose
ARG CCTV_DETAIL_POSE_SHA256
ENV PROXIGO_CCTV_DETAIL_POSE_MODEL=${CCTV_DETAIL_POSE_MODEL}
ENV PROXIGO_CCTV_DETAIL_POSE_SHA256=${CCTV_DETAIL_POSE_SHA256}
COPY --from=cctvmodels /${CCTV_DETAIL_POSE_MODEL}.onnx /models/${CCTV_DETAIL_POSE_MODEL}.onnx
RUN python -c 'import os; from ecs_worker import detail_pose_model; from video_analytics.detectors import fingerprint; model = detail_pose_model(os.environ["PROXIGO_CCTV_DETAIL_POSE_SHA256"], os.environ["PROXIGO_CCTV_DETAIL_POSE_MODEL"]); fingerprint(model["path"], model["sha256"])'
USER 10001:10001

FROM ecs AS ecs-person
USER root
ARG CCTV_PERSON_MODEL_SHA256
ENV PROXIGO_CCTV_PERSON_MODEL_SHA256=${CCTV_PERSON_MODEL_SHA256}
COPY --from=cctvmodels /yolo11-person.onnx /models/yolo11-person.onnx
RUN python -c 'import os; from ecs_worker import person_model; from video_analytics.detectors import fingerprint; model = person_model(os.environ["PROXIGO_CCTV_PERSON_MODEL_SHA256"]); fingerprint(model["path"], model["sha256"])'
USER 10001:10001

FROM worker AS local
