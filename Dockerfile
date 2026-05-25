# AI Landscape — single-image container for the web app + CLI.
#
# The image bakes in:
#   * the Python runtime + requirements.txt
#   * the application code (ailandscape/, scripts/)
#   * the version-controlled corpus, snapshots, and corrections — these
#     ARE the project's source of truth; serving the app without them
#     would be meaningless
#
# What lives OUTSIDE the image (mounted at run time, see docker-compose.yml):
#   * data/  — derived caches (knowledge_graph.db, ner_output_log.db,
#              run_history.jsonl). Rebuilt on first run.
#
# Multi-stage build keeps the final image small: the `builder` stage has
# the compiler toolchain for any wheels that need it; the runtime stage
# is a fresh slim image with just the installed packages.
#
# Default command: `ailandscape serve --port 8000` -- the FastAPI web app.
# Override CMD to use the CLI instead (e.g. `docker compose run web run`
# to trigger an ingestion).

# ---- builder stage --------------------------------------------------------
FROM python:3.11-slim AS builder

WORKDIR /build

# build-essential covers any C extensions in the dep tree (lxml etc.)
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install runtime deps into a virtualenv we can copy whole into the final
# image -- gives us a small image without compilers.
RUN python -m venv /venv
ENV PATH=/venv/bin:$PATH

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ---- runtime stage --------------------------------------------------------
FROM python:3.11-slim AS runtime

# Run as a non-root user. The /data volume is chowned to this user at
# image-build time so the entrypoint can write to it without --user
# overrides at runtime.
RUN useradd --create-home --shell /bin/bash --uid 1000 ail

ENV PATH=/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    AIL_HOME=/app

WORKDIR /app

# Copy the prebuilt venv from the builder stage.
COPY --from=builder /venv /venv

# Copy the source of truth + application code. We DO copy snapshots/ +
# corpus/ + corrections.json so the running container can serve real
# content immediately; derived data/ is left to the mounted volume.
COPY --chown=ail:ail ailandscape/ ./ailandscape/
COPY --chown=ail:ail scripts/ ./scripts/
COPY --chown=ail:ail corpus/ ./corpus/
COPY --chown=ail:ail snapshots/ ./snapshots/
# Glob patterns so a missing optional file (e.g. corrections.json hasn't
# been initialised in a fresh clone) doesn't break the build.
COPY --chown=ail:ail corrections.json* review.json* ./
COPY --chown=ail:ail requirements.txt README.md LLM_INDEX.md ./

# Create the data/ dir (mounted at runtime) and hand ownership to the
# non-root user. Mounting empty over this is harmless.
RUN mkdir -p /app/data && chown -R ail:ail /app/data

USER ail

# Pre-build the derived SQLite databases (NER log + knowledge graph)
# so the image ships ready-to-serve. Without this, a fresh container
# would start with empty data/ and /api/overview would error until
# the operator ran rebuild manually.
#
# Uses the rule-based NER backend (no spaCy model dependency), which
# matches what the test suite + CI use. The host can override at
# runtime by mounting their own data/ volume.
#
# This step is expensive (~30-60s on the published corpus) but only
# runs when corpus/ or ailandscape/ changes -- Docker's layer cache
# skips it on subsequent builds with unchanged source.
RUN python -m ailandscape.cli rebuild --ner rule \
    && echo "Pre-built data/ contents:" \
    && ls -la /app/data/

# Healthcheck: /api/overview is a cheap read that exercises the corpus
# loader + the KG store -- if either is broken the container is unhealthy.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request,sys; urllib.request.urlopen('http://127.0.0.1:8000/api/overview', timeout=4).read(); sys.exit(0)" || exit 1

EXPOSE 8000

# Default: serve the web app. Override at `docker run` / `docker compose run`
# to use any other CLI subcommand: e.g. `docker compose run web rebuild`.
#
# --host 0.0.0.0 is REQUIRED inside a container: docker's `-p 8000:8000`
# forwards host:8000 to the container's external interface (eth0), not to
# loopback. Binding uvicorn to 127.0.0.1 would mean forwarded packets find
# nothing listening. The CLI default is 127.0.0.1 so local dev stays
# loopback-only; containers opt in explicitly here.
CMD ["python", "-m", "ailandscape.cli", "serve", "--host", "0.0.0.0", "--port", "8000"]
