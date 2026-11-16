#!/usr/bin/env bash
set -euo pipefail
umask 077
: "${RESTIC_REPOSITORY:?COS S3 endpoint/bucket/restic/cloudlab}"
: "${RESTIC_PASSWORD:?1Password}"
: "${AWS_ACCESS_KEY_ID:?1Password COS access key}"
: "${AWS_SECRET_ACCESS_KEY:?1Password COS secret key}"
export AWS_DEFAULT_REGION=na-ashburn RESTIC_CACHE_DIR=/data/backup-cache
script_dir=$(cd "$(dirname "$0")" && pwd)
context=${CLOUDLAB_KUBE_CONTEXT:-$(kubectl config current-context)}
restic_args=()
if [[ $RESTIC_REPOSITORY == s3:* ]]; then
  restic_args=(-o s3.bucket-lookup=dns)
fi
exec 9>/data/cloudlab-backup.lock
flock -n 9 || exit 0
stage=$(mktemp -d /data/cloudlab-backup.XXXXXX)
cleanup() { rm -rf "$stage"; }
trap cleanup EXIT
# Data-only export: no Influx users, tokens, config or Git.
kubectl --context "$context" -n apps exec deployment/influxdb -- sh -ec '
  INFLUX_TOKEN="$DOCKER_INFLUXDB_INIT_ADMIN_TOKEN" exec influx query --raw --org cloudlab --host http://localhost:8086 "$1"
' -- 'from(bucket:"apple-health") |> range(start: 1970-01-01T00:00:00Z)' > "$stage/apple-health.csv"
claim=$(kubectl --context "$context" -n apps get pvc uptime-kuma -o jsonpath='{.spec.volumeName}')
path=$(kubectl --context "$context" get pv "$claim" -o jsonpath='{.spec.local.path}{.spec.hostPath.path}')
[[ $path == /data/k3s/storage/* ]] || { echo 'Unexpected PV path'; exit 1; }
# SQLite online backup reads WAL consistently; sanitized copy removes credentials.
python3 "$script_dir/export-kuma.py" "$path/kuma.db" "$stage/kuma.db"
# Export application BSON and indexes, excluding MongoDB users and roles.
# The password is passed through an anonymous pipe, never a command argument.
kubectl --context "$context" -n apps exec health-auto-export-mongo-0 -c mongo -- bash -ec '
  mongodump --host 127.0.0.1 --username health-auto-export --authenticationDatabase admin \
    --config=<(printf "password: %s\n" "$MONGO_APP_PASSWORD") \
    --db health-auto-export --archive --gzip
' > "$stage/health-auto-export.archive.gz"
gzip -t "$stage/health-auto-export.archive.gz"
restic "${restic_args[@]}" backup "$stage" --tag cloudlab --host cloudlab
restic "${restic_args[@]}" forget --tag cloudlab --host cloudlab --group-by host,tags --keep-within 30d --prune
restic "${restic_args[@]}" check --read-data-subset=5%
