#!/usr/bin/env python3
"""Verify restored services through their APIs without printing health values."""

import base64
import json
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

KUBE = ["kubectl", "--context", "default", "-n", "apps"]


def get(kind, name=None, namespace="apps", extra=()):
    args = ["kubectl", "--context", "default", "-n", namespace, "get", kind]
    return json.loads(subprocess.check_output(args + ([name] if name else []) + list(extra) + ["-o", "json"]))


def api(service, port, path, headers=None):
    address = get("service", service)["spec"]["clusterIP"]
    request = urllib.request.Request(f"http://{address}:{port}{path}", headers=headers or {})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def verify(settings):
    report = {}
    pods = get("pods")["items"]
    unhealthy = [pod["metadata"]["name"] for pod in pods if not pod["metadata"].get("deletionTimestamp")
                 and pod["status"].get("phase") != "Succeeded"
                 and not any(c["type"] == "Ready" and c["status"] == "True" for c in pod["status"].get("conditions", []))]
    if unhealthy:
        raise RuntimeError("Pods not ready: " + ", ".join(unhealthy))
    credentials = get("secret", "grafana-admin")["data"]
    login = base64.b64decode(credentials["admin-user"]) + b":" + base64.b64decode(credentials["admin-password"])
    headers = {"Authorization": "Basic " + base64.b64encode(login).decode()}
    if api("monitoring-grafana", 80, "/api/health", headers)["database"] != "ok":
        raise RuntimeError("Grafana database is not healthy")
    dashboards = api("monitoring-grafana", 80, "/api/search?type=dash-db&limit=5000", headers)
    expected = set()
    for config in get("configmaps", extra=("-l", "grafana_dashboard=1"))["items"]:
        for name, content in config.get("data", {}).items():
            if name.endswith(".json"):
                uid = json.loads(content).get("uid")
                if uid:
                    expected.add(uid)
    missing = expected - {dashboard["uid"] for dashboard in dashboards}
    if missing:
        raise RuntimeError("Grafana has not loaded all provisioned dashboards")
    sources = api("monitoring-grafana", 80, "/api/datasources", headers)
    metrics = [source for source in sources if source["type"] == "prometheus"]
    if len(metrics) != 1 or metrics[0]["url"] != "http://thanos-query.apps.svc.cluster.local:10902":
        raise RuntimeError("Grafana must have one Prometheus datasource pointing to Thanos Query")
    official = {"Health Metrics", "Workouts Table", "Workout Details"}
    loaded = {dashboard["title"] for dashboard in dashboards if dashboard.get("folderTitle") == "Health Auto Export"}
    if not official.issubset(loaded):
        raise RuntimeError("The official Health Auto Export dashboards are missing")
    report["grafana_dashboards"] = len(dashboards)
    report["provisioned_dashboards_present"] = True
    report["official_health_dashboards"] = sorted(official)

    token = base64.b64decode(get("secret", "health-auto-export")["data"]["read-token"]).decode()
    values = api("health-auto-export", 3001, "/api/metrics/step_count", {"api-key": token})
    if not isinstance(values, list):
        raise RuntimeError("Official Health Auto Export read API failed")
    expected_count = next((item["documents"] for item in settings["data_report"]["mongo"] if item["name"] == "step_count"), 0)
    if len(values) < expected_count:
        raise RuntimeError("Official Health Auto Export API returned fewer records than the restore")
    report["health_step_count_documents"] = len(values)

    query = urllib.parse.urlencode({"query": "up", "partial_response": "false"})
    recent = api("thanos-query", 10902, "/api/v1/query?" + query)
    if recent.get("status") != "success" or not recent["data"]["result"] or recent.get("warnings"):
        raise RuntimeError("Thanos recent query failed or returned a partial result")
    end = int(time.time()) - 7200
    query = urllib.parse.urlencode({"query": "up", "start": end - 86400, "end": end, "step": 1800,
                                   "partial_response": "false", "storeMatch[]": '{__address__="thanos-store.apps.svc.cluster.local:10901"}'})
    historical = api("thanos-query", 10902, "/api/v1/query_range?" + query)
    if historical.get("status") != "success" or historical.get("warnings") or not historical["data"]["result"]:
        raise RuntimeError("No historical metrics could be read from COS through Store Gateway")
    report["historical_series"] = len(historical["data"]["result"])
    report["historical_query_no_partial_results"] = True

    script = (Path(__file__).parent / "configure-kuma.js").read_text()
    payload = dict(settings["kuma"], mode="verify", expectedMonitors=settings["data_report"]["kuma_monitors"])
    result = subprocess.run(KUBE + ["exec", "-i", "deployment/uptime-kuma", "--", "node", "-e", script],
                            input=json.dumps(payload), text=True, capture_output=True)
    if result.returncode or not json.loads(result.stdout).get("ok"):
        raise RuntimeError("Kuma login or monitor list verification failed")
    report["kuma_monitors"] = payload["expectedMonitors"]
    return report


if __name__ == "__main__":
    try:
        print(json.dumps(verify(json.load(sys.stdin)), indent=2))
    except Exception as error:
        print("Service verification failed: " + str(error), file=sys.stderr)
        sys.exit(1)
