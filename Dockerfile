FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml README.md requirements-runtime.txt ./
COPY src ./src
RUN pip install --no-cache-dir --require-hashes -r requirements-runtime.txt \
    && pip install --no-cache-dir --no-deps .

ENV BIND_PORT=8080
USER 65532:65532
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import os,urllib.request; h='[::1]' if os.environ.get('BIND_HOST')=='::' else '127.0.0.1'; urllib.request.urlopen('http://'+h+':'+os.environ.get('BIND_PORT','8080')+'/healthz', timeout=2).read()"]
CMD ["python", "-m", "hermes_agent_bridge"]
