# MinIO for the compose files.
#
# `minio/minio` is gone from Docker Hub ("object not found"), and quay.io and
# bitnami have no image either, so a fresh box could not start storage at all;
# only a box that still had the old image cached kept working. Chainguard
# still publishes the server, but its image has no shell or curl, and the
# compose health check (which the api waits on) needs curl. So: their binary,
# on a small base that has curl.
FROM cgr.dev/chainguard/minio:latest AS minio

FROM debian:bookworm-slim
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=minio /usr/bin/minio /usr/bin/minio
ENTRYPOINT ["/usr/bin/minio"]
