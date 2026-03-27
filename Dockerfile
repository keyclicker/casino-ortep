FROM python:3.13-slim

WORKDIR /app

RUN pip install --no-cache-dir poetry

COPY pyproject.toml poetry.lock ./
RUN poetry install --only main --no-root

COPY *.py ./

RUN mkdir -p data

CMD ["poetry", "run", "python", "bot.py"]
