FROM python:3.13-slim-trixie
RUN apt-get update && apt-get install -y --no-install-recommends \
    git curl ca-certificates ripgrep && rm -rf /var/lib/apt/lists/*
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && \
    apt-get install -y --no-install-recommends nodejs && rm -rf /var/lib/apt/lists/*
WORKDIR /work
