# Stage 1: Build Python dependencies
# Use a standard slim image for the build step — keeps the builder lean.
FROM python:3.11-slim AS builder

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# Stage 2: Final runtime image
# mcr.microsoft.com/playwright/python ships Chromium + all system deps
# pre-installed. We swap the base image here so the app image inherits
# browser binaries without running "playwright install --with-deps".
# Tag is locked to the same Playwright release as requirements.txt (>=1.44).
FROM mcr.microsoft.com/playwright/python:v1.44.0-jammy AS runner

WORKDIR /app

# Copy pip-installed packages from the builder stage.
# The Playwright base image uses a non-root user "pwuser"; we keep root here
# so the copy target matches the builder's --user install path.
COPY --from=builder /root/.local /root/.local
COPY ./app ./app

ENV PATH=/root/.local/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
