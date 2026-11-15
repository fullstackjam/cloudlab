#!/usr/bin/env bash
set -euo pipefail
: "${RESTIC_REPOSITORY:?COS S3 endpoint/bucket/restic/cloudlab}"
: "${RESTIC_PASSWORD:?1Password}"
: "${AWS_ACCESS_KEY_ID:?1Password COS access key}"
: "${AWS_SECRET_ACCESS_KEY:?1Password COS secret key}"
export AWS_DEFAULT_REGION=na-ashburn RESTIC_CACHE_DIR=/data/backup-cache
root=$(cd "$(dirname "$0")/.." && pwd)
exec 9>/data/cloudlab-backup.lock
flock -n 9 || exit 0
stage=$(mktemp -d /data/cloudlab-backup.XXXXXX)
cleanup() { rm -rf "$stage"; }
trap cleanup EXIT
# Data-only export: no Influx users, tokens, config or Git.
pod=$(kubectl -n apps get pods -l app=influxdb -o jsonpath='{.items[0].metadata.name}')
kubectl -n apps exec "$pod" -- sh -ec 'influx query --raw --org cloudlab --host http://localhost:8086 --token "$DOCKER_INFLUXDB_INIT_ADMIN_TOKEN" '\''from(bucket:"apple-health") |> range(start: 1970-01-01T00:00:00Z)'\''' > "$stage/apple-health.csv"
claim=$(kubectl -n apps get pvc uptime-kuma -o jsonpath='{.spec.volumeName}')
path=$(kubectl get pv "$claim" -o jsonpath='{.spec.local.path}{.spec.hostPath.path}')
[[ $path == /data/k3s/storage/* ]] || { echo 'Unexpected PV path'; exit 1; }
# SQLite online backup reads WAL consistently; sanitized copy removes credentials.
python3 "$root/scripts/export-data.py" "$path/kuma.db" "$stage/kuma.db"
restic backup "$stage" --tag cloudlab --host cloudlab
restic forget --tag cloudlab --host cloudlab --group-by host,tags --keep-within 30d --prune
restic check --read-data-subset=5%
