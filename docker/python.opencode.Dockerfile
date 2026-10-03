FROM vex-bench-python-base:latest

ARG OPENCODE_VERSION=1.15.5

RUN npm install -g opencode-ai@${OPENCODE_VERSION}
RUN mkdir -p /root/.config/opencode /root/.local/share/opencode/log

ENTRYPOINT ["opencode"]
