FROM vex-bench-python-base:latest

ARG CODEX_VERSION=0.131.0

RUN npm install -g @openai/codex@${CODEX_VERSION}
RUN mkdir -p /root/.codex

ENTRYPOINT ["codex"]
