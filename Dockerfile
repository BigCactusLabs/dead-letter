# The release workflow resolves both inputs to immutable multi-platform digests.
# Override these arguments with the recorded digests to reproduce a release build.
ARG PYTHON_IMAGE=python:3.12-slim-bookworm
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.12.16
FROM ${UV_IMAGE} AS uv
FROM ${PYTHON_IMAGE} AS builder

COPY --from=uv /uv /usr/local/bin/uv
ENV UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_NO_CACHE=1
WORKDIR /build
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY docker/build-constraints.txt ./build-constraints.txt
COPY src ./src
ARG SOURCE_DATE_EPOCH=0
RUN uv sync --locked --no-dev --extra mcp --no-editable \
    --build-constraint build-constraints.txt

FROM ${PYTHON_IMAGE} AS runtime
ARG VERSION=dev
ARG REVISION=unknown
LABEL org.opencontainers.image.title="dead-letter" \
    org.opencontainers.image.description="Local .eml email conversion to Markdown over stdio MCP" \
    org.opencontainers.image.source="https://github.com/BigCactusLabs/dead-letter" \
    org.opencontainers.image.url="https://github.com/BigCactusLabs/dead-letter" \
    org.opencontainers.image.licenses="PolyForm-Noncommercial-1.0.0" \
    org.opencontainers.image.version="${VERSION}" \
    org.opencontainers.image.revision="${REVISION}" \
    io.modelcontextprotocol.server.name="io.github.BigCactusLabs/dead-letter"
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HOME=/tmp
RUN groupadd --gid 10001 dead-letter \
    && useradd --uid 10001 --gid 10001 --no-create-home --no-log-init \
       --home-dir /tmp --shell /usr/sbin/nologin dead-letter
COPY --from=builder /opt/venv /opt/venv
COPY LICENSE /usr/share/doc/dead-letter/LICENSE
WORKDIR /data
USER 10001:10001
# No listener, runtime downloads, or uv resolution. Attach stdin, never a TTY.
ENTRYPOINT ["dead-letter-mcp"]
