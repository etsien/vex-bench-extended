FROM vex-bench-go-base:latest
RUN npm install -g @openai/codex@latest
ENTRYPOINT ["codex"]
