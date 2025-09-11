FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential \
      gcc \
      libstdc++6 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install poetry & deps first (layer cache) without requiring package source code
COPY pyproject.toml /app/pyproject.toml
RUN pip install --upgrade pip \
 && pip install --no-cache-dir poetry \
 && poetry config virtualenvs.create false \
 && poetry install --no-interaction --no-ansi --no-root

# Now copy the rest of the project
COPY . /app

# (Optional) install project itself if we ship as a package later
# RUN poetry install --no-interaction --no-ansi

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
