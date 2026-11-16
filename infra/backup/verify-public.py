#!/usr/bin/env python3
"""Check the public ingress, upload authentication, and ntfy delivery after cutover."""

import base64
import datetime
import json
import subprocess
import sys
import time
import urllib.parse
import urllib.request

KUBE = ["kubectl", "--context", "default", "-n", "apps"]


def get(kind, name=None, extra=()):
    return json.loads(subprocess.check_output(KUBE + ["get", kind] + ([name] if name else []) + list(extra) + ["-o", "json"]))


def curl(url, *, configuration="", extra=()):
    result = subprocess.run(["curl", "-fsSL", "--max-time", "30", "--config", "-", *extra, url],
                            input=configuration, text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError("Public request failed: " + urllib.parse.urlsplit(url).hostname)
    return result.stdout


def verify(settings):
    domain = settings["domain"]
    expected = get("pods", extra=("-l", "app=whoami"))["items"][0]["metadata"]["name"]
    body = curl(f"https://whoami.{domain}/")
    if f"Hostname: {expected}\n" not in body:
        raise RuntimeError("Public traffic did not reach the recovered node")
    for host, path in (("grafana", "/login"), ("home", "/"), ("status", "/"), ("influxdb", "/signin")):
        curl(f"https://{host}.{domain}{path}")
    key = base64.b64decode(get("secret", "health-auto-export")["data"]["write-token"]).decode()
    response = json.loads(curl(f"https://health-ingest.{domain}/api/data",
        configuration='header = "api-key: ' + key + '"\nheader = "Content-Type: application/json"\n',
        extra=("--data-binary", '{"data":{"metrics":[],"workouts":[]}}')))
    # An empty batch exercises authentication and routing without inventing health data.
    if not isinstance(response, dict) or not response.get("metrics", {}).get("success"):
        raise RuntimeError("The official upload endpoint did not accept an authenticated empty batch")
    report = {"public_whoami_pod": expected, "public_service_pages": "passed", "authenticated_empty_upload": "passed"}
    if settings.get("test_notifications"):
        now = datetime.datetime.now(datetime.timezone.utc)
        alert = "CloudLabRecoveryCheck" + str(int(now.timestamp()))
        address = get("service", "monitoring-kube-prometheus-alertmanager")["spec"]["clusterIP"]
        payload = [{"labels": {"alertname": alert, "severity": "warning"},
                    "annotations": {"summary": "Restored node notification test"},
                    "startsAt": now.isoformat(), "endsAt": (now + datetime.timedelta(minutes=2)).isoformat()}]
        request = urllib.request.Request(f"http://{address}:9093/api/v2/alerts", data=json.dumps(payload).encode(),
                                         headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=15) as response:
            if response.status != 200:
                raise RuntimeError("Alertmanager rejected the notification test")
        url = settings["ntfy_url"].rstrip("/") + "/json?poll=1&since=5m"
        for attempt in range(24):
            messages = [json.loads(line) for line in curl(url).splitlines() if line.strip()]
            match = next((item for item in messages if alert in item.get("message", "")), None)
            if match:
                report["ntfy_event_id"] = match["id"]
                break
            time.sleep(5)
        else:
            raise RuntimeError("The test alert did not arrive at ntfy")
    return report


if __name__ == "__main__":
    try:
        print(json.dumps(verify(json.load(sys.stdin)), indent=2))
    except Exception as error:
        print("Public verification failed: " + str(error), file=sys.stderr)
        sys.exit(1)
