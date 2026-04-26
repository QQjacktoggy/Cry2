FROM python:3.11-slim AS base

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY src/ src/
COPY config/ config/
COPY scripts/ scripts/
COPY backtest_tool/ backtest_tool/

# Set Python path
ENV PYTHONPATH=/app/src:/app
ENV PYTHONUNBUFFERED=1

# Health check
HEALTHCHECK --interval=60s --timeout=10s --retries=3 \
    CMD python -c "import bot; print('ok')" || exit 1

# Default command (paper trading)
CMD ["python", "scripts/run_paper.py"]
