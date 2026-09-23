FROM python:3.12.7-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY simulator ./simulator

RUN useradd --system --uid 10001 app
USER app

CMD ["python", "-m", "simulator.main"]
