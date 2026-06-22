# Single-stage build using the Playwright image as the base.
# This guarantees all compiled C extensions (pydantic-core, asyncpg, lxml, etc.)
# are compiled against the SAME Python ABI that will run the app.
# The two-stage builder pattern breaks for pydantic-core/.so when the builder's
# Python minor version differs from the Playwright base image's Python version.
FROM mcr.microsoft.com/playwright/python:v1.60.0-jammy AS runner

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY ./app ./app

EXPOSE 8000

CMD ["python3", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
