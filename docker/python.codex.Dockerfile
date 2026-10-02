FROM vex-bench-python-base:latest
RUN npm install -g @openai/codex@latest
ENTRYPOINT ["codex"]
