FROM golang:1.26-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends \
    git curl ca-certificates ripgrep && rm -rf /var/lib/apt/lists/*
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && \
    apt-get install -y --no-install-recommends nodejs && rm -rf /var/lib/apt/lists/*
WORKDIR /work
