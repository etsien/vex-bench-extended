FROM vex-bench-java-base:latest
RUN npm install -g @anthropic-ai/claude-code@latest
ENTRYPOINT ["claude"]
