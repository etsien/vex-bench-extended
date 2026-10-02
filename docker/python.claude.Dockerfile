FROM vex-bench-python-base:latest
RUN npm install -g @anthropic-ai/claude-code@latest
ENTRYPOINT ["claude"]
