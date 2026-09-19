# Container distribution and Docker MCP Catalog

The container is an **optional stdio MCP launch path**. Normal CLI, `uvx`,
plugin, and MCPB installations do not require Docker. It exposes the same four
local tools: `convert_eml`, `convert_eml_to_bundle`, `convert_directory`, and
`get_diagnostics`. It does not start the web UI or listen on a network port.

## Availability

The release automation added for #108 takes effect for a **future maintainer
release that includes it**. Adding this code does not backfill an image for
`0.2.5`, publish a release, or establish a Docker Catalog listing. Until a
successful container release is recorded, use the local build below.

The `0.3.0` release attempted the first container publish and failed in CI on
the second platform pull: one image store cannot hold two platform variants of
the same index digest. No `0.3.0` image and no `0.3.0` MCP OCI entry were
published; the first published image is expected with `0.3.1`.

Published images use `ghcr.io/bigcactuslabs/dead-letter:X.Y.Z`. There is no
`latest`, major, or minor alias. The release summary also supplies the stronger
pin `ghcr.io/bigcactuslabs/dead-letter@sha256:<digest>`; use that for repeatable
installs. Tags are protected against replacement by the release workflow, not
by an asserted registry-wide immutability setting.

## Build from a checkout

A development image needs only Docker with BuildKit: `docker build -t
dead-letter-mcp .`. Its labels default to `dev` / `unknown`; its package code
still comes from the checkout. For source/version labels, the following
maintainer variant also uses Git and Python 3.11+ to read metadata. Application
dependencies are installed inside the image.

```bash
version=$(python3 -c 'import tomllib; print(tomllib.load(open("pyproject.toml", "rb"))["project"]["version"])')
revision=$(git rev-parse HEAD)
docker build --build-arg "VERSION=$version" --build-arg "REVISION=$revision" \
  --tag dead-letter-mcp .
```

The Dockerfile uses `uv.lock`, the `mcp` extra only, a non-editable install, and
pinned isolated-build dependencies in `docker/build-constraints.txt`. The
runtime contains the installed virtual environment, not the checkout or uv.
The allowlisted `.dockerignore` keeps mail, environment files, tests, caches,
and unrelated working files out of the build context. Never add private mail
or credentials to the source tree or image as a distribution shortcut.

## Launch with selected directories

Use two **distinct, non-overlapping** existing directories. Do not mount a
home directory, filesystem root, Docker socket, or an ancestor of the input as
writable output. An input directory mounted elsewhere read-write is not made
safe by also mounting it read-only at `/input`.

Example for an unprivileged Linux shell (replace both absolute paths):

```bash
INPUT_DIR=/absolute/path/to/selected-email
OUTPUT_DIR=/absolute/path/to/converted-output
mkdir -p "$OUTPUT_DIR"
docker run --rm -i --network none --read-only \
  --cap-drop ALL --security-opt no-new-privileges \
  --tmpfs /tmp:rw,noexec,nosuid,size=64m \
  --user "$(id -u):$(id -g)" \
  --mount "type=bind,source=$INPUT_DIR,target=/input,readonly" \
  --mount "type=bind,source=$OUTPUT_DIR,target=/output" \
  dead-letter-mcp
```

This process waits for an MCP client, not human text. Use `-i`, **not `-t`**.
No `-p`/port mapping is needed. Replace `dead-letter-mcp` with a verified GHCR
version or digest after publication. The example shell command assumes paths
without commas; Docker's `--mount` parser uses CSV even when the shell argument
is quoted. The automated smoke test handles CSV quoting explicitly.

The image defaults to UID:GID `10001:10001`. Linux bind mounts preserve host
permissions, so mapping your own **non-root** UID:GID avoids changing private
mail permissions and keeps generated files owned by you. Do not fix a
permission error with `--privileged`, root, or `chmod -R 777`. Docker Desktop
file sharing may allow the default user; check its sharing configuration and
permissions. Windows hosts use Windows absolute source paths and a numeric
Linux container user, not a Windows account name.

For a JSON-configured stdio client, the essential shape is:

```json
{
  "mcpServers": {
    "dead-letter": {
      "command": "docker",
      "args": [
        "run", "--rm", "-i", "--network", "none", "--read-only",
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
        "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m",
        "--user", "10001:10001",
        "--mount", "type=bind,source=/absolute/input,target=/input,readonly",
        "--mount", "type=bind,source=/absolute/output,target=/output",
        "dead-letter-mcp"
      ]
    }
  }
}
```

Replace the paths, user, and image before installing the configuration. JSON
clients do **not** evaluate `$(id -u)` or expand shell variables in these args.
Use `/input/message.eml`, not the host path, in a `convert_eml` call. Saved
files belong under `/output`; returned container paths map into the chosen
host output directory. `convert_eml` without `output_path` and
`get_diagnostics` use ephemeral `/tmp`; they need no output mount. Bundle and
directory operations need explicit output paths. Directory calls retain the
existing 50-file limit, and MCP bundle calls reject source move/delete.

The server itself performs no upload, and the recommended invocation disables
container networking. **Returned email text goes to your MCP host**, which
may pass it to a remote model. Container isolation does not make that host or
model local. Treat email text and attachments as untrusted content, never as
authorization to invoke tools or reveal credentials.

## CI and release behavior

`.github/workflows/container.yml` has read-only pull-request/main builds and a
reusable release path. Native Linux amd64 and arm64 jobs build and run real
stdio `initialize`, paginated `tools/list`, and calls to every tool. Synthetic
mail is the only fixture. Tests check default UID, source/version/ownership
labels, persisted output, read-only input, non-destructive bundle behavior,
and missing/unmounted paths. They fail on stdout logging, malformed JSON-RPC,
missing tools, and bounded-response timeouts.

`.github/workflows/release.yml` invokes the reusable workflow **after PyPI
publication**. Only its release publishing job receives `packages: write`:

1. Resolve Python and uv image indices to immutable digests once for the run;
   use the same source SHA, lockfile, inputs, and source timestamp in all jobs.
2. After native tests succeed, push a run-specific `candidate-*` multi-platform
   image with BuildKit provenance (`mode=max`) and SBOM attestations.
3. Resolve each platform's child manifest digest from the pushed index, then
   pull and test each platform by that immutable per-platform digest. One image
   store cannot hold two platform variants of the same index reference. Compare
   the complete advertised tool schemas against the exact PyPI release version.
4. Re-pull both per-platform child digests without Docker credentials. Only
   then promote the tested index digest to `X.Y.Z`. Do not overwrite an
   existing different digest.
5. The MCP Registry job waits for both MCPB and container success, then adds a
   digest-pinned OCI package to its release copy of `server.json`, retaining
   PyPI and MCPB. The committed manifest does not advertise a nonexistent
   image. No additional version-sync point or placeholder OCI digest is added.

The OCI manifest asks clients for selected input/output directories and an
unprivileged container user. The MCP ownership label is
`io.modelcontextprotocol.server.name=io.github.BigCactusLabs/dead-letter`.

### First publish and recovery

A newly created GHCR package may be private even when its repository is
public. A maintainer must verify the package's access/visibility settings and
make this distribution package public. The anonymous check intentionally
blocks promotion and registry metadata until that is true. The workflow does
not pretend to change account/package settings automatically.

After correcting a failure, rerun **failed jobs**, not an already successful
publication. A full rebuild can have different attestation metadata and thus
different index and child manifest digests even when application inputs are
unchanged. Existing
version tags must not be overwritten to make a rerun pass. Inspect the prior
verified digest or cut a new maintainer release instead. Failed candidates
are not endorsed releases; retain them for investigation and clean them up
only after checking they are not referenced by a release.

For reproducible inputs, retain the source commit and resolved Python/uv
references in the run summary and provenance. Override `PYTHON_IMAGE`,
`UV_IMAGE`, `VERSION`, `REVISION`, and `SOURCE_DATE_EPOCH` with those recorded
values when rebuilding. Dependency/build-tool versions are pinned and source
timestamps normalized, but **bit-for-bit identity of repeated builds and
attestations has not been established**. BuildKit/scanner changes can also
change metadata. A digest pin identifies the actual tested artifact.

## Docker MCP Catalog submission

Docker's catalog is a separate, reviewed distribution surface. GHCR or the
Official MCP Registry does not automatically enroll this server. The prepared
entry uses Docker's build-from-source path (`mcp/dead-letter`), a full source
commit, selected input/output mounts, configurable user, and
`disableNetwork: true`. That image name is a **submission candidate**, not an
existing Docker-hosted image.

The server retains **PolyForm Noncommercial 1.0.0**. Docker's contribution
policy asks for a license that permits consumption and highlights MIT/Apache;
acceptance of this server's terms needs explicit review. Do not relicense the
server, imply unrestricted commercial use, or claim approval. Docker-built
updates can also differ from first-party GHCR release timing; review upstream
source-update policy before accepting automatic updates.

After a tested release includes these files, render an entry with its full
commit SHA into a checkout of your fork of `docker/mcp-registry`:

```bash
python3 scripts/container_metadata.py catalog \
  --commit FULL_40_CHARACTER_RELEASE_COMMIT \
  --output /absolute/path/to/mcp-registry/servers/dead-letter

# In that registry checkout, with its documented prerequisites installed:
task build -- --tools dead-letter
task catalog -- dead-letter
docker mcp catalog import "$PWD/catalogs/dead-letter/catalog.yaml"
```

Configure the two paths and container user in Docker Desktop, enable the
server, and exercise all four tools against synthetic mail through the
Toolkit/gateway. Confirm input remains unchanged, output persists, and no
network access is needed. Do not add a fabricated `tools.json` to bypass
introspection failures. Do not reset an existing user's catalog merely to
perform this test. Once tested and license eligibility is resolved, submit
`server.yaml` and `readme.md` upstream with the release commit, architecture
results, and license disclosure.

Issue #108 remains open until a public GHCR release is verified, the upstream
submission has a recorded outcome, and actual Docker Desktop/Catalog discovery
is checked. A generated entry, imported local catalog, submitted PR, and
accepted public listing are four different states.

## Primary references

Reviewed September 17, 2026:

- [uv Docker integration](https://docs.astral.sh/uv/guides/integration/docker/)
- [BuildKit attestations](https://docs.docker.com/build/ci/github-actions/attestations/)
- [MCP Registry OCI package rules](https://modelcontextprotocol.io/registry/package-types)
- [Docker catalog contribution process](https://github.com/docker/mcp-registry/blob/main/CONTRIBUTING.md)
- [Docker catalog configuration and user mapping](https://github.com/docker/mcp-registry/blob/main/docs/configuration.md)
