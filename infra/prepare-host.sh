#!/usr/bin/env bash
# Run as root after copying cloud-init.yaml into /opt/cloudlab-restore.
set -euo pipefail
uuid=${1:?Pass the verified ext4 data disk UUID}
[[ $uuid =~ ^[a-fA-F0-9-]+$ ]] || { echo 'Invalid data disk UUID'; exit 1; }
device=$(blkid -U "$uuid")
[[ $(findmnt -n -o UUID /) != "$uuid" ]] || { echo "The data UUID points to the system disk"; exit 1; }
[[ $(blkid -s TYPE -o value "$device") == ext4 ]] || { echo 'Expected an existing ext4 data disk'; exit 1; }
if mountpoint -q /data; then
  [[ $(findmnt -n -o UUID /data) == "$uuid" ]] || { echo '/data is mounted from another disk'; exit 1; }
else
  mkdir -p /data
  [[ -z $(find /data -mindepth 1 -maxdepth 1 -print -quit) ]] || { echo 'Unmounted /data is not empty'; exit 1; }
  mount -t ext4 "UUID=$uuid" /data
fi
python3 - "$uuid" <<'PY_FSTAB'
from pathlib import Path
import sys
path = Path('/etc/fstab')
text = path.read_text()
entries = [line.split() for line in text.splitlines() if line.strip() and not line.lstrip().startswith('#')]
entries = [row for row in entries if len(row) > 2 and row[1] == '/data']
if entries and any(row[0] != 'UUID=' + sys.argv[1] or row[2] != 'ext4' for row in entries):
    raise SystemExit('Existing /data fstab entry does not match the selected UUID')
if not entries:
    path.write_text(text.rstrip() + '\nUUID=' + sys.argv[1] + ' /data ext4 defaults,nofail 0 2\n')
PY_FSTAB
findmnt --verify >/dev/null
if [[ -e /etc/rancher/k3s/k3s.yaml || -d /data/k3s/server ]]; then
  echo 'Existing k3s state found; use the bootstrap phase only for a node prepared by this recovery run'
  exit 1
fi
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq curl ca-certificates jq restic sqlite3 python3-yaml
python3 - <<'PY'
from pathlib import Path
import yaml
config = yaml.safe_load(Path('/opt/cloudlab-restore/cloud-init.yaml').read_text())
for item in config['write_files']:
    target = Path(item['path'])
    if str(target) not in ('/etc/rancher/k3s/config.yaml', '/usr/local/sbin/cloudlab-install'):
        raise SystemExit('Unexpected cloud-init file')
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(item['content'])
    target.chmod(int(item['permissions'], 8))
PY
install -d -m 0755 /etc/systemd/system/k3s.service.d
printf '[Unit]\nRequiresMountsFor=/data\n' > /etc/systemd/system/k3s.service.d/data-mount.conf
systemctl daemon-reload
/usr/local/sbin/cloudlab-install
node_name=$(hostname -s | tr '[:upper:]' '[:lower:]')
kubectl --context default -n default wait --for=create "node/$node_name" --timeout=120s
kubectl --context default -n default wait --for=condition=Ready "node/$node_name" --timeout=300s
kubectl --context default -n default wait --for=create storageclass/local-path --timeout=120s
