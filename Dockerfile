# syntax=docker/dockerfile:1.6
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Runtime deps only (psycopg2-binary needs libpq5)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /code

# Install Python deps first (layer caching)
COPY app/requirements.txt /code/requirements.txt
RUN pip install --no-cache-dir -r /code/requirements.txt

# Bake spaCy model
RUN python -m spacy download en_core_web_sm

# Copy app
COPY app /code/app

# Copy migrations
COPY migrations /code/migrations
COPY run_migration.py /code/run_migration.py

# Non-root user
RUN useradd --create-home --uid 10001 appuser \
  && chown -R appuser:appuser /code
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health').read()" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
