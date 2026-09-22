#!/usr/bin/env bash
# Make a consistent SQLite snapshot and upload it to a GCS bucket.
# Required environment: GCS_BUCKET (for example gs://my-bookbot-backups).
set -euo pipefail

app_dir="${APP_DIR:-/opt/discord-book-bot}"
db_path="${DB_PATH:-${app_dir}/data/bookbot.db}"
bucket="${GCS_BUCKET:?Set GCS_BUCKET to a gs:// bucket URI}"
sqlite_bin="${SQLITE_BIN:-sqlite3}"

if [[ ! -f "$db_path" ]]; then
  echo "Database does not exist yet: $db_path" >&2
  exit 1
fi

snapshot="$(mktemp --suffix=.db)"
trap 'rm -f "$snapshot"' EXIT
"$sqlite_bin" "$db_path" ".backup '$snapshot'"

stamp="$(date -u +%Y-%m-%dT%H-%M-%SZ)"
gcloud storage cp "$snapshot" "${bucket%/}/bookbot-${stamp}.db"
