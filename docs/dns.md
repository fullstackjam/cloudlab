# DNS and exposure
Proposed proxied CNAME records, each targeting `<NEW_TUNNEL_UUID>.cfargotunnel.com`:

| Full record | Destination |
|---|---|
| whoami.fullstackjam.com | whoami test |
| status.fullstackjam.com | Kuma UI/public status pages |
| grafana.fullstackjam.com | Grafana |
| health.fullstackjam.com | InfluxDB UI and Apple Health write API |
| home.fullstackjam.com | Homepage |

Add the CNAMEs to `domains/fullstackjam.com.js` in [dns-config](https://github.com/fullstackjam/dns-config). Use the Tunnel UUID from OpenTofu as the target, enable Cloudflare proxying, and review the DNSControl preview on the PR before merging. Start with `whoami`, then add the remaining entries after verifying their access policies.

TLS terminates at Cloudflare; encrypted Tunnel connects Cloudflare to cloudflared; HTTP remains within the single-node cluster to Traefik. No public inbound 80/443/6443 required. New SG allows trusted IPv4 SSH + outbound; it is NOT automatically attached. Audit current SG/IPv6, preserve emergency SSH, then attach deliberately. Tunnel egress needs DNS and TCP/UDP 7844 plus HTTPS for downloads/APIs. kube API stays private; use an SSH forward for remote administration.

Before enabling DNS/Tunnel, create Cloudflare Access applications/policies for home, grafana, health UI and Kuma admin, limited to Jam with MFA. Access is a documented manual prerequisite, not encoded policy in this scaffold. Keep app authentication too. Apple Health exporter needs an Access service token or a tightly restricted write path policy; do not publicly bypass the entire health hostname. whoami may be public. Kuma public status page can be explicitly allowed separately. Traefik dashboard omitted to reduce exposure; use kubectl port-forward when needed.
