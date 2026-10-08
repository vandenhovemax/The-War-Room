# The War Room — backend image (shared by 4 services: live backend, paper
# backend, download worker, candle sync + scanner; the compose entry picks
# the command). Frontend has its own image in frontend/Dockerfile.
# Data (antigravity.db, ohlcv/, live_engine_state/, logs/) lives in
# host-mounted volumes — NEVER in this image.
FROM python:3.13-slim

WORKDIR /app

# System tzdata so TZ=America/New_York (set in compose) affects naive
# local-time calls exactly like on the Windows machine (Eastern time) —
# parity insurance for any datetime.now() in the pipeline.
RUN apt-get update && apt-get install -y --no-install-recommends tzdata \
  && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

# Only what the services run: the backend package + the two root-level
# entrypoint scripts. No data, no frontend, no Co-Work, no .env (see
# .dockerignore — config comes from compose environment variables).
COPY backend/ backend/
COPY candle_sync.py download_worker.py ./

CMD ["python", "-m", "backend.main"]
