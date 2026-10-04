FROM vex-bench-go-base:latest

ARG CLAUDE_CODE_VERSION=2.1.150

RUN npm install -g @anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}
RUN useradd -m -s /bin/bash bench
USER bench
WORKDIR /home/bench/work

ENTRYPOINT ["claude"]
