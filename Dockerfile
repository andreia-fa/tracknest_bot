FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY tracknest/ ./tracknest

RUN useradd --create-home --shell /usr/sbin/nologin bot \
    && mkdir -p /app/data \
    && chown -R bot:bot /app
USER bot

WORKDIR /app/tracknest
ENV PYTHONUNBUFFERED=1
ENV DB_PATH=/app/data/tracknest.db
VOLUME ["/app/data"]

CMD ["python", "-m", "bot.main"]
