# Use official lightweight Python runtime
FROM python:3.11-slim

# Set environment variables:
# 1. Prevent Python from writing .pyc files to disk
# 2. Prevent Python from buffering stdout/stderr (ensures real-time logs in docker logs)
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Set working directory inside container
WORKDIR /app

# Install system dependencies:
# - ffmpeg: Required for video clipping, trimming, and playback speed adjustment
# - curl, unzip, ca-certificates: Used to download and install Deno
# Deno is required by yt-dlp-ejs and home/services.py to solve YouTube JS challenges
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    unzip \
    ca-certificates \
    && curl -fsSL https://deno.land/install.sh | sh \
    && mv /root/.deno/bin/deno /usr/local/bin/deno \
    && apt-get purge -y --auto-remove curl unzip \
    && rm -rf /var/lib/apt/lists/*

# Verify system binaries are installed and accessible in PATH
RUN ffmpeg -version > /dev/null && deno --version > /dev/null

# Copy only requirements.txt first to take advantage of Docker layer caching.
# pip install will only re-run if requirements.txt actually changes!
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the project source code
COPY . .
