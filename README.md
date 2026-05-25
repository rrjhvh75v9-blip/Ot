# artarb — Art Arbitrage Platform

Tracks Dutch and Belgian art auctions, identifies arbitrage opportunities, and
monitors artist market signals (Google Trends, news mentions, exhibitions).

## Architecture

```
artarb/
├── data/          rkd_import.py    — seeds artists from the RKD API
├── scrapers/      kunstveiling.py  — live auction listings
├── signals/       google_trends.py, news_monitor.py
├── utils/         name_resolver.py — fuzzy artist name matching
└── models/        SQLAlchemy ORM   — 11 PostgreSQL tables

main.py            — single-process scheduler (6h / 24h / 7d cadences)
```

## Setup (5 steps)

### 1 — Prerequisites

Install [Docker Desktop](https://www.docker.com/products/docker-desktop/) (or Docker Engine + Compose plugin).
No local Python installation is required.

```bash
docker --version          # 24.x or later
docker compose version    # 2.x or later
```

### 2 — Configure environment

```bash
cp .env.example .env
```

Open `.env` and set a strong database password — the only value you must change:

```dotenv
DB_PASSWORD=change_me_now
```

All other defaults (`DB_NAME=artarb`, `DB_USER=artarb`, `LOG_LEVEL=INFO`) work
out of the box.

### 3 — Build the image

```bash
docker compose build
```

This installs all Python dependencies inside the image.
Subsequent builds are fast because pip layers are cached.

### 4 — Start the stack

```bash
docker compose up -d
```

On first start the entrypoint automatically:
1. Waits until PostgreSQL accepts connections
2. Runs `alembic upgrade head` to create all tables
3. Starts `main.py`, which seeds artists from RKD (if the table is empty)
   then enters the scheduler loop

### 5 — Verify and monitor

```bash
# Stream live logs
docker compose logs -f app

# Check the schedule is running (look for ✔ DONE lines)
docker compose logs app | grep -E "START|DONE|FAIL"

# Open a database shell
docker compose exec db psql -U artarb -d artarb

# Run the test suite inside the container
docker compose exec app python -m pytest tests/ -v

# Stop everything (data is preserved in the pgdata volume)
docker compose down
```

To wipe the database and start fresh:

```bash
docker compose down -v    # -v removes volumes
docker compose up -d
```

## Useful one-liners

```bash
# Trigger a manual scrape without waiting for the 6-hour window
docker compose exec app python -m artarb.scrapers.kunstveiling

# Import / refresh artists from RKD
docker compose exec app python -m artarb.data.rkd_import

# Fetch Google Trends for all artists
docker compose exec app python -m artarb.signals.google_trends

# Run news monitor demo (no network required)
docker compose exec app python -m artarb.signals.news_monitor \
    --demo --names "Karel Appel" "Pierre Alechinsky" "Corneille"

# Install Chromium for browser-based scraping (one-off)
docker compose exec app playwright install chromium --with-deps
```

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `DB_PASSWORD` | *(required)* | PostgreSQL password |
| `DB_NAME` | `artarb` | Database name |
| `DB_USER` | `artarb` | Database user |
| `DB_HOST` | `db` | Hostname (compose service name) |
| `DB_PORT` | `5432` | PostgreSQL port |
| `DB_POOL_SIZE` | `5` | SQLAlchemy connection pool size |
| `DB_MAX_OVERFLOW` | `10` | Extra connections above pool size |
| `LOG_LEVEL` | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |
| `ARTARB_UNMATCHED_LOG` | `unmatched_artists.log` | Path for unresolved artist names |
