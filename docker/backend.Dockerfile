# FireScope backend: FastAPI plus the GDAL toolchain the raster analyses shell out to.
# Build context is the repository root, because the backend depends on ../mcp.
FROM ghcr.io/osgeo/gdal:ubuntu-small-3.10.2

COPY --from=ghcr.io/astral-sh/uv:0.12.5 /uv /bin/uv

ENV UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PYTHONUNBUFFERED=1

# The lifecycle and analysis scripts import osgeo and numpy, which this image ships
# for its system Python, and the backend runs them with its own interpreter. The
# venv therefore has to see the system packages.
RUN uv venv --system-site-packages --python /usr/bin/python3 /opt/venv

WORKDIR /app/backend

# Dependencies first, so editing source code does not reinstall them.
COPY backend/pyproject.toml backend/uv.lock ./
COPY mcp /app/mcp
RUN uv sync --frozen --no-dev --extra openai --extra anthropic --no-install-project

# Installed editable: the backend finds backend/scripts relative to its own source.
COPY backend ./
RUN uv sync --frozen --no-dev --extra openai --extra anthropic

ENV PATH=/opt/venv/bin:$PATH \
    LOCAL_DATA_ROOT=/data \
    RASTER_PREVIEW_DIR=/tmp/firescope-raster-previews

EXPOSE 8000
CMD ["uvicorn", "wildfire_agent.api:app", "--host", "0.0.0.0", "--port", "8000"]
