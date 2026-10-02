FROM eclipse-temurin:21-jdk
RUN apt-get update && apt-get install -y --no-install-recommends \
    git curl ca-certificates ripgrep maven && rm -rf /var/lib/apt/lists/*
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && \
    apt-get install -y --no-install-recommends nodejs && rm -rf /var/lib/apt/lists/*
WORKDIR /work
