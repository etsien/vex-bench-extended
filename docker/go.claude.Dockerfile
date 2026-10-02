FROM vex-bench-go-base:latest
RUN npm install -g @anthropic-ai/claude-code@latest
ENTRYPOINT ["claude"]
