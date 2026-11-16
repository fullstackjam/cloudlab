#!/usr/bin/env python3
"""Restore application exports on an isolated node. Credentials arrive on stdin."""

import csv
import datetime
import decimal
import io
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys

KUBE = ["kubectl", "--context", "default", "-n", "apps"]
HERE = Path(__file__).resolve().parent


def run(args, **kwargs):
    result = subprocess.run(args, capture_output=True, **kwargs)
    if result.returncode:
        raise RuntimeError("Command failed: " + " ".join(args[:5]))
    return result.stdout


def kube(*args):
    return run(KUBE + list(args), text=True)


def volume(claim):
    pvc = json.loads(kube("get", "pvc", claim, "-o", "json"))
    pv = json.loads(kube("get", "pv", pvc["spec"]["volumeName"], "-o", "json"))
    spec = pv["spec"]
    path = Path(spec.get("local", spec.get("hostPath", {}))["path"]).resolve()
    if not path.is_relative_to("/data/k3s/storage"):
        raise RuntimeError("Unexpected local volume path")
    return path


def scale(name, replicas):
    kube("scale", "deployment", name, "--replicas=" + str(replicas))
    if replicas:
        kube("rollout", "status", "deployment/" + name, "--timeout=180s")
    else:
        kube("wait", "--for=delete", "pod", "-l", "app=" + name, "--timeout=120s")


def influx_records(text):
    header, types, groups, records = None, [], [], []
    for row in csv.reader(io.StringIO(text)):
        if not row:
            continue
        if row[0] == "#datatype":
            types = row
        elif row[0] == "#group":
            groups = row
        elif row[0].startswith("#"):
            continue
        elif "_measurement" in row and "_time" in row:
            header = row
        else:
            if header is None:
                raise ValueError("Missing annotated CSV header")
            data = dict(zip(header, row))
            kind = types[header.index("_value")]
            value = data["_value"]
            if kind in ("long", "unsignedLong"):
                value = str(int(value))
            elif kind == "double":
                value = str(decimal.Decimal(value).normalize())
            elif kind == "boolean":
                value = value.lower()
            tags = tuple(sorted((key, data[key]) for i, key in enumerate(header)
                                if key and not key.startswith("_") and key not in ("result", "table")
                                and i < len(groups) and groups[i] == "true"))
            match = re.fullmatch(r"(.*T\d\d:\d\d:\d\d)(?:\.(\d+))?Z", data["_time"])
            if not match:
                raise ValueError("Expected a UTC timestamp")
            seconds = int(datetime.datetime.fromisoformat(match[1] + "+00:00").timestamp())
            stamp = seconds * 10**9 + int((match[2] or "").ljust(9, "0")[:9])
            records.append((data["_measurement"], tags, data["_field"], kind, value, stamp))
    return sorted(records)


def configure_kuma(settings, mode, count):
    payload = dict(settings, mode=mode, expectedMonitors=count)
    script = (HERE / "configure-kuma.js").read_text()
    result = kube_json_exec("deployment/uptime-kuma", ["node", "-e", script], payload)
    if not result.get("ok"):
        raise RuntimeError("Kuma configuration failed")
    return result


def kube_json_exec(target, command, payload):
    return json.loads(run(KUBE + ["exec", "-i", target, "--"] + command,
                          input=json.dumps(payload), text=True))


def restore(folder, settings):
    os.umask(0o077)
    source = Path(folder)
    for name in ("apple-health.csv", "health-auto-export.archive.gz", "kuma.db"):
        if not (source / name).is_file():
            raise RuntimeError("Missing backup export: " + name)
    # Export snapshots are data-only. A recovered node must not accept writes yet.
    deployments = json.loads(kube("get", "deployments", "-o", "json"))["items"]
    if any(item["metadata"]["name"] == "apple-health-ingester" for item in deployments):
        scale("apple-health-ingester", 0)
    scale("health-auto-export", 0)
    scale("uptime-kuma", 0)

    with (source / "apple-health.csv").open("rb") as csv_file:
        converted = run([sys.executable, str(HERE / "restore-influxdb.py")], stdin=csv_file)
    run(KUBE + ["exec", "-i", "deployment/influxdb", "--", "sh", "-ec",
        'INFLUX_TOKEN="$DOCKER_INFLUXDB_INIT_ADMIN_TOKEN" exec influx write --org cloudlab --bucket apple-health --precision ns'], input=converted)
    queried = kube("exec", "deployment/influxdb", "--", "sh", "-ec",
        'INFLUX_TOKEN="$DOCKER_INFLUXDB_INIT_ADMIN_TOKEN" exec influx query --raw --org cloudlab '
        '\'from(bucket:"apple-health") |> range(start: 1970-01-01T00:00:00Z)\'')
    expected, actual = influx_records((source / "apple-health.csv").read_text()), influx_records(queried)
    if expected != actual:
        raise RuntimeError("InfluxDB records differ from the snapshot")

    with (source / "health-auto-export.archive.gz").open("rb") as archive:
        run(KUBE + ["exec", "-i", "health-auto-export-mongo-0", "--", "bash", "-ec",
            'mongorestore --host 127.0.0.1 --username health-auto-export --authenticationDatabase admin '
            '--config=<(printf "password: %s\\n" "$MONGO_APP_PASSWORD") '
            '--archive --gzip --drop --stopOnError --quiet --nsInclude="health-auto-export.*"'], stdin=archive)
    mongo = json.loads(kube("exec", "health-auto-export-mongo-0", "--", "mongosh", "--quiet", "--eval",
        'db.getSiblingDB("admin").auth("health-auto-export",process.env.MONGO_APP_PASSWORD); '
        'const d=db.getSiblingDB("health-auto-export"); print(JSON.stringify(d.getCollectionNames().sort().map(n=>'
        '({name:n,documents:d[n].countDocuments({}),indexes:d[n].getIndexes().length}))));'))

    path = volume("uptime-kuma")
    for name in ("kuma.db", "kuma.db-wal", "kuma.db-shm"):
        (path / name).unlink(missing_ok=True)
    shutil.copy2(source / "kuma.db", path / "kuma.db")
    with sqlite3.connect(path / "kuma.db") as db:
        monitors = db.execute("SELECT count(*) FROM monitor").fetchone()[0]
        heartbeats = db.execute("SELECT count(*) FROM heartbeat").fetchone()[0]
        if db.execute("SELECT count(*) FROM user").fetchone()[0]:
            raise RuntimeError("Expected a sanitized Kuma export without users")
    scale("uptime-kuma", 1)
    configure_kuma(settings, "setup", monitors)
    scale("uptime-kuma", 0)
    with sqlite3.connect(path / "kuma.db") as db:
        user = db.execute("SELECT id FROM user WHERE username=?", (settings["username"],)).fetchall()
        if len(user) != 1:
            raise RuntimeError("Kuma administrator was not created")
        db.execute("UPDATE monitor SET user_id=? WHERE user_id IS NULL OR user_id=0", user[0])
    scale("uptime-kuma", 1)
    configure_kuma(settings, "configure", monitors)
    scale("health-auto-export", 1)
    return {"influx_records": len(actual), "influx_exact_match": True,
            "mongo": mongo, "kuma_monitors": monitors, "kuma_heartbeats": heartbeats}


if __name__ == "__main__":
    try:
        print(json.dumps(restore(sys.argv[1], json.load(sys.stdin))))
    except Exception as error:
        print("Application restore failed: " + str(error), file=sys.stderr)
        sys.exit(1)
