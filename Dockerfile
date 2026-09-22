FROM python:3.12-slim

WORKDIR /app

# cloudflared powers the /dashboard web app's HTTPS access (see bot/tunnel.py)
# — the VM has no domain and no inbound port open, so it tunnels out to
# Cloudflare instead of terminating TLS locally. Static binary, amd64 to
# match the VM's shape (see DEPLOY_STRATEGY.md).
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && curl -fsSL -o /usr/local/bin/cloudflared \
       https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 \
    && chmod +x /usr/local/bin/cloudflared \
    && apt-get purge -y curl \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

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
