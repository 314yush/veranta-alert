#!/usr/bin/env bash
# Overnight supervisor: restart python bot.py on exit. Never writes .env.
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ ! -f .env ]]; then
  echo "ERROR: .env missing. Copy .env.example → .env and fill TELEGRAM_BOT_TOKEN, AVANTIS_PRIVATE_KEY, AVANTIS_TRADER_ADDRESS, AVANTIS_NETWORK=mainnet" >&2
  exit 1
fi

PY="${ROOT}/.venv/bin/python"
if [[ ! -x "$PY" ]]; then
  echo "ERROR: venv missing at $PY — python3 -m venv .venv && pip install -r requirements.txt" >&2
  exit 1
fi

mkdir -p data
LOG="${ROOT}/data/bot.stdout.log"
LOCK="${ROOT}/data/bot.lock"
export PYTHONUNBUFFERED=1

exec 9>"$LOCK"
if ! flock -n 9; then
  echo "Already running (data/bot.lock held). Not starting a second getUpdates client."
  exit 0
fi

echo $$ > "${ROOT}/data/bot.supervisor.pid"
backoff=2
while true; do
  echo "$(date -u +'%Y-%m-%d %H:%M:%S') UTC starting python bot.py" | tee -a "$LOG"
  "$PY" bot.py >> "$LOG" 2>&1
  code=$?
  echo "$(date -u +'%Y-%m-%d %H:%M:%S') UTC bot exited code=${code}; restart in ${backoff}s" | tee -a "$LOG"
  sleep "$backoff"
  if (( backoff < 30 )); then
    backoff=$(( backoff * 2 ))
  else
    backoff=30
  fi
done
