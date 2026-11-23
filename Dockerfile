# Use an official Python base image
FROM python:3.11-slim

# Create and set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential && \
    rm -rf /var/lib/apt/lists/*

# Copy requirements and install them
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the app code
COPY pyproject.toml .
COPY src ./src
COPY static ./static
COPY server.py .
RUN pip install --no-cache-dir --no-deps -e .

# Expose the port Uvicorn will run on
EXPOSE 8000

# Mount credentials and runtime data separately; they are not image contents.
# Listen on container interfaces; publish to host loopback with -p 127.0.0.1:8000:8000.
CMD ["uvicorn", "kalshi_workstation.app:app", "--host", "0.0.0.0", "--port", "8000"]
