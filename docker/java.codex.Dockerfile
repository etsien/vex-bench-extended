FROM vex-bench-java-base:latest
RUN npm install -g @openai/codex@latest
ENTRYPOINT ["codex"]
