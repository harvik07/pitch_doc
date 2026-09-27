# TRACE — Marsh Pitch Intelligence, fully self-contained.
# Stage 1 builds the React frontend; stage 2 is the Python app with everything it needs at runtime:
# Docling + its OCR/layout models (downloaded at build time), LibreOffice for slide previews, Arial-metric fonts.
# Nothing depends on the host except the Google credentials and .env settings passed in at `docker run`.

# --- 1. Frontend build ---------------------------------------------------------------------------------------
FROM node:20-slim AS frontend
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# --- 2. Python runtime ---------------------------------------------------------------------------------------
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/app/.cache/huggingface \
    PORT=8080

# Docling/OCR system libraries; LibreOffice Impress (headless slide previews); Liberation fonts (Arial metrics)
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 libglib2.0-0 fonts-liberation fontconfig libreoffice-impress \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# CPU-only PyTorch (a Docling dependency): much smaller than the default CUDA build
RUN pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
COPY requirements.txt pyproject.toml ./
RUN pip install -r requirements.txt

COPY src/ src/
RUN pip install --no-deps -e .
COPY server.py ./
COPY config/ config/
COPY prompts/ prompts/
COPY assets/ assets/
COPY scripts/ scripts/
COPY data/ data/
COPY --from=frontend /app/frontend/dist frontend/dist

# Download Docling's layout / table / OCR models into the image (a real conversion of a bundled brochure,
# both OCR modes), so uploads never fetch models at runtime.
RUN python -c "from pathlib import Path; from marsh.extraction import _convert; \
p = next(Path('data/policies').glob('*.pdf')); _convert(p, full_page_ocr=False); _convert(p, full_page_ocr=True, page=1)"

# Runs, uploads and caches live in volumes (see docker-compose.yml)
RUN mkdir -p outputs data/uploads data/cache/web
EXPOSE 8080

CMD ["sh", "-c", "exec uvicorn server:app --host 0.0.0.0 --port ${PORT}"]
