#!/bin/sh
# Back up or restore the ClaimPilot database (local compose).
#   scripts/backup.sh backup [file]     writes a compressed dump (default backups/claimpilot-<time>.sql.gz)
#   scripts/backup.sh restore <file>    replaces the database contents with the dump
# Documents live in the S3 bucket and are backed up by the bucket's own versioning, not here.
set -eu
cmd="${1:-}"
case "$cmd" in
  backup)
    out="${2:-backups/claimpilot-$(date +%Y%m%d-%H%M%S).sql.gz}"
    mkdir -p "$(dirname "$out")"
    docker compose exec -T postgres pg_dump -U claimpilot --clean --if-exists claimpilot | gzip > "$out"
    echo "wrote $out ($(wc -c < "$out") bytes)"
    ;;
  restore)
    file="${2:?usage: backup.sh restore <file>}"
    gunzip -c "$file" | docker compose exec -T postgres psql -U claimpilot -d claimpilot -q -v ON_ERROR_STOP=1
    echo "restored $file"
    ;;
  *)
    echo "usage: $0 backup [file] | restore <file>" >&2
    exit 2
    ;;
esac
