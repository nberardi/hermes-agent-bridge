FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

USER nobody
ENV BIND_PORT=8080
CMD ["python", "-m", "hermes_agent_bridge"]
