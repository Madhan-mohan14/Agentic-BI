FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

COPY . /app
WORKDIR /app

ENV PYTHONUNBUFFERED=1

RUN uv sync --locked --no-dev

EXPOSE 8080

CMD ["uv", "run", "tools/bi_tools_server.py"]
