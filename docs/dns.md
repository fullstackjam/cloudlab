# DNS and exposure
Use the domain set in [clusters/cloudlab/settings.yaml](../clusters/cloudlab/settings.yaml) for the proxied CNAMEs below. Each record targets `<TUNNEL_UUID>.cfargotunnel.com`.

| Full record | Destination |
|---|---|
| `whoami.<domain>` | whoami test |
| `status.<domain>` | Kuma UI/public status pages |
| `grafana.<domain>` | Grafana |
| `influxdb.<domain>` | InfluxDB UI and native API |
| `health-ingest.<domain>` | Health Auto Export JSON ingestion for InfluxDB and the official dashboards (authenticated POST only) |
| `home.<domain>` | Homepage |

Add the CNAMEs to the matching zone file in [dns-config](https://github.com/fullstackjam/dns-config). The reference deployment uses `domains/fullstackjam.com.js`. Use the Tunnel UUID from OpenTofu as the target, enable Cloudflare proxying, and review the DNSControl preview on the PR before merging. Start with `whoami`, then add the remaining entries after verifying their access policies. When changing the base domain, apply the Tunnel ingress change and verify the new HTTPRoutes before publishing DNS for the new names.

TLS terminates at Cloudflare; encrypted Tunnel connects Cloudflare to cloudflared; HTTP remains within the single-node cluster to Traefik. No public inbound 80/443/6443 required. New SG allows trusted IPv4 SSH + outbound; it is NOT automatically attached. Audit current SG/IPv6, preserve emergency SSH, then attach deliberately. Tunnel egress needs DNS and TCP/UDP 7844 plus HTTPS for downloads/APIs. kube API stays private; use an SSH forward for remote administration.

Before enabling DNS/Tunnel, create Cloudflare Access applications/policies for home, grafana, health UI and Kuma admin, limited to Jam with MFA. Access is a documented manual prerequisite, not encoded policy in this scaffold. Keep app authentication too. Apple Health exporter needs an Access service token or a tightly restricted write path policy; do not publicly bypass the entire health hostname. whoami may be public. Kuma public status page can be explicitly allowed separately. Traefik dashboard omitted to reduce exposure; use kubectl port-forward when needed.

The reference deployment also accepts `health.<domain>` as a compatibility hostname for InfluxDB. Use `influxdb.<domain>` for new clients.

`health-ingest.<domain>` is a separate HTTPS hostname. The Gateway forwards `POST /api/healthautoexport/v1/influxdb/ingest` to the InfluxDB receiver and `POST /api/data` to the official receiver. Each path requires its own write credential.
