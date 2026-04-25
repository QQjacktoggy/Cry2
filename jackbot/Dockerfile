FROM python:3.11-slim AS base

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
# Install numpy and pandas explicitly just in case
RUN pip install --no-cache-dir -r requirements.txt pandas numpy pyyaml

# Copy source code
COPY src/ src/
COPY config/ config/
COPY scripts/ scripts/

# Set Python path
ENV PYTHONPATH=/app/src:/app
ENV PYTHONUNBUFFERED=1

# Default command (jackbot run)
CMD ["python", "scripts/run.py"]
