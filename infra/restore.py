#!/usr/bin/env python3
"""Recover CloudLab on an empty Ubuntu node; keep it isolated until activation."""

import argparse
import base64
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import shlex
import socket
import subprocess
import sys
import time
import urllib.parse

import yaml

ROOT = Path(__file__).resolve().parents[1]
BACKUP_ENV = ("RESTIC_REPOSITORY", "RESTIC_PASSWORD", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY")


def command(args, *, cwd=None, data=None, env=None, quiet=False):
    result = subprocess.run(args, cwd=cwd, input=data, env=env, text=True,
                            stdout=subprocess.PIPE if quiet else None, stderr=subprocess.PIPE if quiet else None)
    if result.returncode:
        if args[:3] == ["op", "item", "get"]:
            reason = result.stderr or ""
            if "authorization timeout" in reason.lower():
                raise RuntimeError("1Password desktop authorization timed out; no host changes were made")
            if "not signed in" in reason.lower():
                raise RuntimeError("1Password desktop authentication is not available")
        # Captured commands can carry credentials in their output. Do not print it.
        raise RuntimeError(f"{args[0]} failed (exit {result.returncode})")
    return result.stdout


def resolve_credentials(environment, names):
    """Resolve explicitly supplied 1Password references with one desktop request."""
    references = {}
    for name in names:
        value = environment.get(name, "")
        if value.startswith("op://"):
            parsed = urllib.parse.urlsplit(value)
            parts = urllib.parse.unquote(parsed.path).strip("/").split("/")
            if len(parts) != 2:
                raise ValueError("Use op://VAULT/ITEM-ID/FIELD for recovery credentials")
            references[name] = (parsed.netloc, *parts)
    if not references:
        return
    vaults = {reference[0] for reference in references.values()}
    if len(vaults) != 1:
        raise ValueError("Resolve recovery credentials from one vault per invocation")
    request = [{"id": item} for item in sorted({reference[1] for reference in references.values()})]
    raw = command(["op", "item", "get", "-", "--vault", vaults.pop(), "--format", "json"],
                  data=json.dumps(request), quiet=True).strip()
    decoder, items = json.JSONDecoder(), []
    while raw:
        value, end = decoder.raw_decode(raw)
        items.extend(value if isinstance(value, list) else [value])
        raw = raw[end:].lstrip()
    for name, (_, item_id, field) in references.items():
        item = next(item for item in items if item["id"] == item_id)
        fields = [entry for entry in item.get("fields", []) if field in (entry.get("id"), entry.get("label"))]
        if len(fields) != 1 or not fields[0].get("value"):
            raise ValueError("Missing or ambiguous credential field for " + name)
        environment[name] = fields[0]["value"]


def isolate(repo, branch):
    """Prepare the remote source before bootstrap can reconcile it."""
    sync = repo / "infra/flux/flux-system/gotk-sync.yaml"
    documents = list(yaml.safe_load_all(sync.read_text()))
    for document in documents:
        if document and document.get("kind") == "GitRepository":
            document["spec"]["ref"] = {"branch": branch}
    sync.write_text(yaml.safe_dump_all(documents, sort_keys=False))
    for name, omitted in (("platform/kustomization.yaml", "cloudflared"),
                          ("apps/kustomization.yaml", "apple-health-influxdb")):
        file = repo / name
        document = yaml.safe_load(file.read_text())
        if omitted in document["resources"]:
            document["resources"].remove(omitted)
        file.write_text(yaml.safe_dump(document, sort_keys=False))
    file = repo / "platform/thanos/resources.yaml"
    documents = list(yaml.safe_load_all(file.read_text()))
    for document in documents:
        if document.get("kind") == "Deployment" and document["metadata"]["name"] == "thanos-compactor":
            document["spec"]["replicas"] = 0
    file.write_text(yaml.safe_dump_all(documents, sort_keys=False))
    file = repo / "platform/kube-prometheus-stack/resources.yaml"
    document = yaml.safe_load(file.read_text())
    thanos = document["spec"]["values"]["prometheus"]["prometheusSpec"]["thanos"]
    thanos.pop("objectStorageConfig", None)
    thanos.pop("additionalArgs", None)
    file.write_text(yaml.safe_dump(document, sort_keys=False))


class Recovery:
    def __init__(self, args):
        self.args = args
        self.host = args.host
        if not self.host or self.host.startswith("-") or any(c.isspace() for c in self.host):
            raise ValueError("Use an SSH destination such as ubuntu@host")
        self.work = Path(args.work_dir).expanduser().resolve()
        if self.work.is_relative_to(ROOT):
            raise ValueError("Keep the recovery work directory outside the repository")
        self.work.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.work.chmod(0o700)
        self.state_path = self.work / "state.json"
        self.state = json.loads(self.state_path.read_text()) if self.state_path.exists() else {
            "host": self.host, "uuid": args.data_uuid, "branch": args.branch or "codex/recover-" + time.strftime("%Y%m%d-%H%M%S"),
            "repository": args.repository, "completed": [],
        }
        if (self.state["host"], self.state["uuid"], self.state["repository"]) != (self.host, args.data_uuid, args.repository):
            raise ValueError("Work directory belongs to another recovery target")
        self.repo = self.work / "repo"
        self.ssh_options = shlex.split(os.environ.get("CLOUDLAB_SSH_OPTIONS", ""))
        self.env = dict(os.environ)
        if not self.env.get("SOPS_AGE_KEY") and self.env.get("SOPS_AGE_KEY_FILE"):
            self.env["SOPS_AGE_KEY"] = Path(self.env["SOPS_AGE_KEY_FILE"]).read_text()
        self.env.pop("SOPS_AGE_KEY_FILE", None)
        required = ["SOPS_AGE_KEY", *BACKUP_ENV, "KUMA_USERNAME", "KUMA_PASSWORD", "NTFY_URL"]
        resolve_credentials(self.env, [*required, "GITHUB_TOKEN"])
        missing = [key for key in required if not self.env.get(key)]
        if missing:
            raise ValueError("Missing credential inputs: " + ", ".join(missing))
        url = urllib.parse.urlsplit(self.env["NTFY_URL"])
        if url.scheme != "https" or url.username or url.password or len(url.path.strip("/").split("/")) != 1 or not url.path.strip("/"):
            raise ValueError("NTFY_URL must be an HTTPS topic URL without embedded credentials")
        if not self.env.get("GITHUB_TOKEN"):
            self.env["GITHUB_TOKEN"] = command(["gh", "auth", "token"], quiet=True).strip()
        self.env["GITHUB_REPOSITORY"] = args.repository
        self.env["GITHUB_BRANCH"] = self.state["branch"]
        self.save()

    def save(self):
        self.state_path.write_text(json.dumps(self.state, indent=2) + "\n")
        self.state_path.chmod(0o600)

    def ssh(self, script, data=None, quiet=False, host=None):
        return command(["ssh", *self.ssh_options, host or self.host, "sudo -n bash -c " + shlex.quote(script)],
                       data=data, quiet=quiet)

    def upload(self):
        files = ["infra/cloud-init.yaml", "infra/prepare-host.sh", *[str(p.relative_to(ROOT)) for p in (ROOT / "infra/backup").glob("*.py")],
                 "infra/backup/configure-kuma.js", "infra/backup/backup.sh",
                 "infra/backup/systemd/cloudlab-backup.service", "infra/backup/systemd/cloudlab-backup.timer"]
        self.ssh("install -d -m 0700 /opt/cloudlab-restore")
        for file in files:
            # Base64 only transports public repository files; credentials use separate stdin JSON.
            payload = base64.b64encode((ROOT / file).read_bytes()).decode()
            self.ssh("base64 -d > /opt/cloudlab-restore/" + shlex.quote(Path(file).name), data=payload, quiet=True)
        self.ssh("chmod 700 /opt/cloudlab-restore/*.sh")

    def prepare(self):
        self.upload()
        self.ssh("/opt/cloudlab-restore/prepare-host.sh " + shlex.quote(self.args.data_uuid))

    def encrypt(self, relative, document):
        encrypted = command(["sops", "encrypt", "--input-type", "json", "--output-type", "yaml",
                             "--filename-override", relative, "/dev/stdin"], cwd=self.repo,
                            data=json.dumps(document), env=self.env, quiet=True)
        (self.repo / relative).write_text(encrypted)

    def decrypt(self, relative):
        return yaml.safe_load(command(["sops", "decrypt", str(self.repo / relative)], env=self.env, quiet=True))

    def push(self, message):
        command(["git", "add", "-A"], cwd=self.repo)
        changed = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=self.repo).returncode
        if changed:
            command(["git", "commit", "-m", message], cwd=self.repo)
        command(["git", "push", "-u", "origin", self.state["branch"]], cwd=self.repo)

    @contextmanager
    def api(self):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        config = self.ssh("kubectl --context default -n default config view --raw", quiet=True)
        document = yaml.safe_load(config)
        document["clusters"][0]["cluster"]["server"] = f"https://127.0.0.1:{port}"
        path = self.work / "kubeconfig"
        path.write_text(yaml.safe_dump(document))
        path.chmod(0o600)
        self.env["KUBECONFIG"] = str(path)
        tunnel = subprocess.Popen(["ssh", *self.ssh_options, "-N", "-o", "ExitOnForwardFailure=yes", "-L",
                                   f"127.0.0.1:{port}:127.0.0.1:6443", self.host], stdout=subprocess.DEVNULL)
        try:
            for attempt in range(30):
                if tunnel.poll() is not None:
                    raise RuntimeError("SSH API forwarding failed")
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=1):
                        break
                except OSError:
                    time.sleep(1)
            else:
                raise RuntimeError("SSH API forwarding did not become ready")
            yield
        finally:
            tunnel.terminate()
            tunnel.wait(timeout=10)

    def wait(self):
        revision = command(["git", "rev-parse", "HEAD"], cwd=self.repo, quiet=True).strip()
        check = """import json,subprocess,sys,time
wanted=sys.argv[1]
for attempt in range(120):
    objects=json.loads(subprocess.check_output(['kubectl','--context','default','-n','flux-system','get','kustomizations','-o','json']))['items']
    ready=objects and all(item.get('status',{}).get('lastAppliedRevision','').endswith(wanted) and any(c['type']=='Ready' and c['status']=='True' for c in item.get('status',{}).get('conditions',[])) for item in objects)
    if ready:
        print('All Flux Kustomizations are ready at '+wanted);break
    time.sleep(5)
else:raise SystemExit('Flux did not converge to the expected revision')
"""
        self.ssh("python3 -c " + shlex.quote(check) + " " + shlex.quote(revision))

    def bootstrap(self):
        if not self.repo.exists():
            command(["git", "clone", "--branch", "main", "git@github.com:" + self.args.repository + ".git", str(self.repo)])
            command(["git", "switch", "-c", self.state["branch"]], cwd=self.repo)
        if self.state["branch"] == "main":
            raise RuntimeError("The active node cannot be bootstrapped as an isolated recovery")
        current_branch = command(["git", "branch", "--show-current"], cwd=self.repo, quiet=True).strip()
        if current_branch != self.state["branch"]:
            raise RuntimeError("The recovery checkout is on an unexpected branch")
        for base in ("apps", "platform"):
            for file in (self.repo / base).rglob("*.sops.yaml"):
                self.decrypt(str(file.relative_to(self.repo)))
        # Repeat these steps if a previous attempt stopped after cloning.
        isolate(self.repo, self.state["branch"])
        relative = "platform/kube-prometheus-stack/alertmanager.sops.yaml"
        muted = self.decrypt(relative)
        muted["stringData"] = {"alertmanager.yaml": yaml.safe_dump({"route": {"receiver": "recovery"}, "receivers": [{"name": "recovery"}]})}
        self.encrypt(relative, muted)
        self.push("Prepare an isolated node recovery")
        with self.api():
            command(["bash", "infra/bootstrap.sh"], cwd=self.repo, env=self.env)
        command(["git", "pull", "--ff-only"], cwd=self.repo)
        self.wait()

    def data(self):
        self.upload()
        # Persistent backup credentials are needed by the host timer after activation.
        writer = "import json,pathlib,shlex,sys,os; os.umask(0o077); p=pathlib.Path('/etc/cloudlab'); p.mkdir(exist_ok=True); (p/'backup.env').write_text(''.join(k+'='+shlex.quote(v)+chr(10) for k,v in json.load(sys.stdin).items()))"
        self.ssh("python3 -c " + shlex.quote(writer), data=json.dumps({k: self.env[k] for k in BACKUP_ENV}), quiet=True)
        restic = "set -a; . /etc/cloudlab/backup.env; set +a; restic -o s3.bucket-lookup=dns --no-lock "
        snapshots = json.loads(self.ssh(restic + "snapshots --tag cloudlab --host cloudlab --json", quiet=True))
        selected = max(snapshots, key=lambda item: item["time"]) if self.args.snapshot == "latest" else next(
            item for item in snapshots if item["id"].startswith(self.args.snapshot))
        snapshot = selected["id"]
        destination = "/data/cloudlab-restore/" + snapshot[:8]
        self.state["snapshot"] = snapshot
        self.save()
        self.ssh(restic + "restore " + snapshot + " --target " + destination)
        if len(selected["paths"]) != 1 or not selected["paths"][0].startswith("/data/cloudlab-backup."):
            raise RuntimeError("Unexpected snapshot export layout")
        folder = destination + selected["paths"][0]
        self.ssh("kubectl --context default -n flux-system patch kustomization apps --type=merge -p '{\"spec\":{\"suspend\":true}}'")
        settings = {"username": self.env["KUMA_USERNAME"], "password": self.env["KUMA_PASSWORD"], "ntfyUrl": self.env["NTFY_URL"]}
        report = self.ssh("KUBECONFIG=/etc/rancher/k3s/k3s.yaml python3 /opt/cloudlab-restore/import-apps.py " + shlex.quote(folder),
                          data=json.dumps(settings), quiet=True)
        self.state["data_report"] = json.loads(report)
        self.save()
        relative = "apps/apple-health-influxdb/credentials.sops.yaml"
        credentials = self.decrypt(relative)
        auth = self.ssh('''kubectl --context default -n apps exec deployment/influxdb -- sh -ec 'export INFLUX_TOKEN="$DOCKER_INFLUXDB_INIT_ADMIN_TOKEN"; bucket=$(influx bucket list --org cloudlab --name apple-health --hide-headers | awk "{print \\$1}"); influx auth create --org cloudlab --write-bucket "$bucket" --description "Restored receiver" --json' ''', quiet=True)
        credentials["stringData"]["influx-write-token"] = json.loads(auth)["token"]
        self.encrypt(relative, credentials)
        file = self.repo / "apps/kustomization.yaml"
        document = yaml.safe_load(file.read_text())
        if "apple-health-influxdb" not in document["resources"]:
            document["resources"].append("apple-health-influxdb")
        file.write_text(yaml.safe_dump(document, sort_keys=False))
        self.push("Restore the receiver with a scoped InfluxDB write token")
        self.ssh("kubectl --context default -n flux-system patch kustomization apps --type=merge -p '{\"spec\":{\"suspend\":false}}'")
        self.wait()
        print(json.dumps(self.state["data_report"], indent=2))

    def verify(self):
        self.upload()
        payload = {"data_report": self.state["data_report"], "kuma": {
            "username": self.env["KUMA_USERNAME"], "password": self.env["KUMA_PASSWORD"]}}
        report = self.ssh("KUBECONFIG=/etc/rancher/k3s/k3s.yaml python3 /opt/cloudlab-restore/verify-services.py",
                          data=json.dumps(payload), quiet=True)
        self.state["service_report"] = json.loads(report)
        self.save()
        print(report)

    def activate(self):
        if self.state["branch"] == "main":
            self.finish_activation()
            return
        if "verify" not in self.state["completed"]:
            raise RuntimeError("Complete isolated recovery and verification before activation")
        if not self.args.source_host and not self.args.source_unavailable:
            raise RuntimeError("Specify the source host or confirm that the old node is unavailable")
        self.verify()
        if self.args.source_host:
            source = self.args.source_host
            if source.startswith("-") or any(c.isspace() for c in source):
                raise ValueError("Invalid source SSH destination")
            machine = self.ssh("cat /etc/machine-id", quiet=True)
            if self.ssh("cat /etc/machine-id", quiet=True, host=source) == machine:
                raise RuntimeError("Source and recovery target are the same machine")
            source_repo = self.ssh("kubectl --context default -n flux-system get gitrepository flux-system -o jsonpath='{.spec.url}'", quiet=True, host=source)
            if source_repo.strip() != "ssh://git@github.com/" + self.args.repository:
                raise RuntimeError("The source node watches a different repository")
            self.ssh("systemctl cat cloudlab-backup.service >/dev/null; test -s /etc/cloudlab/backup.env", host=source)
            self.ssh('''set -euo pipefail
umask 077
kubectl --context default -n apps get deployment,statefulset -o json > /root/cloudlab-before-recovery.json
kubectl --context default -n flux-system scale deployment kustomize-controller helm-controller --replicas=0
systemctl stop cloudlab-backup.timer
kubectl --context default -n infra scale deployment cloudflared --replicas=0
kubectl --context default -n infra wait --for=delete pod -l app=cloudflared --timeout=90s
kubectl --context default -n apps scale deployment apple-health-ingester health-auto-export uptime-kuma thanos-compactor monitoring-kube-prometheus-operator --replicas=0
kubectl --context default -n apps wait --for=delete pod -l app=kube-prometheus-stack-operator --timeout=90s
kubectl --context default -n apps wait --for=delete pod -l app=health-auto-export --timeout=120s
kubectl --context default -n apps scale statefulset prometheus-monitoring-kube-prometheus-prometheus alertmanager-monitoring-kube-prometheus-alertmanager --replicas=0
kubectl --context default -n apps wait --for=delete pod prometheus-monitoring-kube-prometheus-prometheus-0 alertmanager-monitoring-kube-prometheus-alertmanager-0 --timeout=90s
systemctl start cloudlab-backup.service
''', host=source)
            # Take the final snapshot after the public writers stop, then import it.
            self.args.snapshot = "latest"
            self.data()
            self.verify()

        self.ssh("python3 /opt/cloudlab-restore/reset-staging-metrics.py")
        relative = "apps/apple-health-influxdb/credentials.sops.yaml"
        ciphertext = (self.repo / relative).read_bytes()
        recovery_branch = self.state["branch"]
        command(["git", "fetch", "origin", "main"], cwd=self.repo)
        command(["git", "checkout", "-B", "main", "origin/main"], cwd=self.repo)
        (self.repo / relative).write_bytes(ciphertext)
        command(["git", "add", relative], cwd=self.repo)
        if subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=self.repo).returncode:
            command(["git", "commit", "-m", "Update the InfluxDB receiver token after node recovery"], cwd=self.repo)
        command(["git", "push", "origin", "HEAD:main"], cwd=self.repo)
        self.state["recovery_branch"] = recovery_branch
        self.state["branch"] = "main"
        self.save()
        self.finish_activation()

    def handover_branch(self, recovery_branch):
        # Both source branches must point to main before the live ref changes.
        # Otherwise an in-flight reconciliation can restore the recovery ref.
        if recovery_branch != "main" and command(["git", "ls-remote", "--heads", "origin", recovery_branch], cwd=self.repo, quiet=True).strip():
            command(["git", "fetch", "origin", recovery_branch], cwd=self.repo)
            command(["git", "checkout", "-B", recovery_branch, "FETCH_HEAD"], cwd=self.repo)
            sync = self.repo / "infra/flux/flux-system/gotk-sync.yaml"
            documents = list(yaml.safe_load_all(sync.read_text()))
            for document in documents:
                if document and document.get("kind") == "GitRepository":
                    document["spec"]["ref"] = {"branch": "main"}
            sync.write_text(yaml.safe_dump_all(documents, sort_keys=False))
            command(["git", "add", str(sync)], cwd=self.repo)
            if subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=self.repo).returncode:
                command(["git", "commit", "-m", "Hand the recovered cluster over to main"], cwd=self.repo)
            command(["git", "push", "origin", recovery_branch], cwd=self.repo)
            command(["git", "checkout", "main"], cwd=self.repo)

    def finish_activation(self):
        recovery_branch = self.state.get("recovery_branch", "main")
        self.handover_branch(recovery_branch)
        self.ssh('''set -e
kubectl --context default -n flux-system patch gitrepository flux-system --type=merge -p '{"spec":{"ref":{"branch":"main"}}}'
stamp=$(date -u +%FT%TZ)
kubectl --context default -n flux-system annotate gitrepository flux-system reconcile.fluxcd.io/requestedAt="$stamp" --overwrite
kubectl --context default -n flux-system annotate kustomization flux-system platform apps reconcile.fluxcd.io/requestedAt="$stamp" --overwrite
''')
        self.wait()
        self.verify()
        if self.args.source_host:
            self.ssh("kubectl --context default -n apps scale deployment --all --replicas=0; kubectl --context default -n apps scale statefulset --all --replicas=0", host=self.args.source_host)
        self.ssh('''set -e
install -d -m 0755 /opt/cloudlab-backup
install -m 0755 /opt/cloudlab-restore/backup.sh /opt/cloudlab-restore/export-kuma.py /opt/cloudlab-backup/
install -m 0644 /opt/cloudlab-restore/cloudlab-backup.service /opt/cloudlab-restore/cloudlab-backup.timer /etc/systemd/system/
systemctl daemon-reload
systemctl start cloudlab-backup.service
systemctl enable --now cloudlab-backup.timer
systemctl is-active cloudlab-backup.timer
journalctl -u cloudlab-backup.service -n 5 --no-pager
''')
        domain = yaml.safe_load((self.repo / "infra/flux/settings.yaml").read_text())["data"]["CLOUDLAB_DOMAIN"]
        report = self.ssh("python3 /opt/cloudlab-restore/verify-public.py", data=json.dumps({
            "domain": domain, "ntfy_url": self.env["NTFY_URL"], "test_notifications": True}), quiet=True)
        self.state["public_report"] = json.loads(report)
        self.save()
        print(report)
        if recovery_branch != "main" and command(["git", "ls-remote", "--heads", "origin", recovery_branch], cwd=self.repo, quiet=True).strip():
            command(["git", "push", "origin", "--delete", recovery_branch], cwd=self.repo)
        print("Keep the active Flux deploy key: it is still used after switching to main.")

    def execute(self):
        phases = ["prepare", "bootstrap", "data", "verify"] if self.args.phase == "all" else [self.args.phase]
        for phase in phases:
            if self.args.phase == "all" and phase in self.state["completed"]:
                print("Already completed:", phase, flush=True)
                continue
            print("Running:", phase, flush=True)
            getattr(self, phase)()
            if phase not in self.state["completed"]:
                self.state["completed"].append(phase)
            self.save()
        print("Recovery state:", self.state_path)
        if "activate" in self.state["completed"]:
            print("Recovery is active on main; backups and public service checks passed.")
        else:
            print("Public ingress and COS metric writers remain disabled until activation.")


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True, help="SSH destination with passwordless sudo")
    parser.add_argument("--data-uuid", required=True, help="Verified existing ext4 data disk UUID")
    parser.add_argument("--repository", required=True, help="GitHub OWNER/REPO")
    parser.add_argument("--work-dir", required=True, help="Private recovery state directory outside Git")
    parser.add_argument("--branch", help="New temporary recovery branch")
    parser.add_argument("--snapshot", default="latest", help="restic snapshot ID or latest")
    parser.add_argument("--phase", choices=["all", "prepare", "bootstrap", "data", "verify", "activate"], default="all")
    parser.add_argument("--source-host", help="Existing CloudLab node to stop during activation")
    parser.add_argument("--source-unavailable", action="store_true", help="The old node is confirmed stopped or lost")
    args = parser.parse_args()
    try:
        Recovery(args).execute()
    except Exception as error:
        print("Recovery stopped:", error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
