FROM vex-bench-go-base:latest

ARG OPENCODE_VERSION=1.15.5

RUN npm install -g opencode-ai@${OPENCODE_VERSION}

ENTRYPOINT ["opencode"]
