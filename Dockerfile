# Container image for the dead-letter MCP server.
#
# Primary purpose: automated MCP directory checks (e.g. glama.ai), which start
# this image and issue an MCP introspection handshake (initialize + tools/list)
# over stdio. Built from the repo source so the image never drifts from the
# committed package version.
#
# dead-letter-mcp is a stdio server: it speaks JSON-RPC over stdin/stdout and
# exposes no network ports. Run it with an MCP client attached to stdio, e.g.
#   docker run -i --rm dead-letter-mcp
FROM python:3.12-slim

WORKDIR /app
COPY . /app

# Install the package with the `mcp` extra, which registers the
# `dead-letter-mcp` console entry point (see [project.scripts] in pyproject.toml).
RUN pip install --no-cache-dir ".[mcp]"

# Stdio MCP server — communicates over stdin/stdout, binds no ports.
ENTRYPOINT ["dead-letter-mcp"]
