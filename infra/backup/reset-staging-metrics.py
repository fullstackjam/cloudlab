#!/usr/bin/env python3
"""Discard metrics collected during isolated recovery before enabling COS uploads."""

import json
from pathlib import Path
import shutil
import subprocess

KUBE = ["kubectl", "--context", "default", "-n", "apps"]


def run(*args):
    return subprocess.check_output(KUBE + list(args), text=True)


connectors = json.loads(subprocess.check_output([
    "kubectl", "--context", "default", "-n", "infra", "get", "pods", "-l", "app=cloudflared", "-o", "json"
]))["items"]
if connectors:
    raise SystemExit("Refusing to discard staging metrics while a Tunnel connector exists")
name = "prometheus-monitoring-kube-prometheus-prometheus"
claim = name + "-db-" + name + "-0"
pvc = json.loads(run("get", "pvc", claim, "-o", "json"))
pv = json.loads(run("get", "pv", pvc["spec"]["volumeName"], "-o", "json"))["spec"]
path = Path(pv.get("local", pv.get("hostPath", {}))["path"]).resolve()
if not path.is_relative_to("/data/k3s/storage") or path == Path("/data/k3s/storage"):
    raise SystemExit("Unexpected Prometheus volume path")
run("scale", "deployment", "monitoring-kube-prometheus-operator", "--replicas=0")
run("wait", "--for=delete", "pod", "-l", "app=kube-prometheus-stack-operator", "--timeout=90s")
run("scale", "statefulset", name, "--replicas=0")
run("wait", "--for=delete", "pod", name + "-0", "--timeout=90s")
for child in path.iterdir():
    if child.is_dir() and not child.is_symlink():
        shutil.rmtree(child)
    else:
        child.unlink()
run("scale", "statefulset", name, "--replicas=1")
run("scale", "deployment", "monitoring-kube-prometheus-operator", "--replicas=1")
print("Discarded isolated Prometheus samples; existing COS blocks were not changed")
