# DNS and access control

`CLOUDLAB_DOMAIN` in [infra/flux/settings.yaml](../infra/flux/settings.yaml) defines the base domain. The following names use Cloudflare-proxied CNAME records, all pointing to `<TUNNEL_UUID>.cfargotunnel.com`.

| Domain | Service |
| --- | --- |
| `whoami.<domain>` | Ingress connectivity check |
| `status.<domain>` | Uptime Kuma administration and public status pages |
| `grafana.<domain>` | Grafana |
| `influxdb.<domain>` | InfluxDB administration and native API |
| `health-ingest.<domain>` | Health Auto Export upload endpoints |
| `home.<domain>` | Homepage |

InfluxDB also keeps `health.<domain>` for compatibility. Use `influxdb.<domain>` for new clients.

## Add a domain

1. Define an HTTPRoute in the component directory with its hostname, paths, and backend Service. Commit it and wait for Flux to sync.
2. Add a proxied CNAME to the zone file in [dns-config](https://github.com/fullstackjam/dns-config). The reference deployment uses `domains/fullstackjam.com.js`. Get the Tunnel ID with `tofu -chdir=infra output -raw tunnel_id`.
3. Open a pull request and review the DNSControl preview. Confirm that only the intended records change, then merge and publish.
4. Check DNS with `dig`, then verify the service response and authentication over HTTPS.

The Tunnel already forwards `*.<domain>` to Traefik, so adding a service under the same base domain only needs an HTTPRoute and DNS record. To change the base domain, update the shared setting, apply the OpenTofu Tunnel configuration, wait for the new HTTPRoutes to become ready, then publish the new DNS records.

## Network access

TLS terminates at Cloudflare. Traffic between Cloudflare and cloudflared uses an encrypted Tunnel. cloudflared reaches Traefik over HTTP inside the cluster. The host does not need public inbound ports 80, 443, or 6443. Use [SSH forwarding](bootstrap.md#4-prepare-kubeconfig-and-encrypted-secrets) for remote access to the Kubernetes API.

The security group created by OpenTofu allows SSH from specified IPv4 CIDRs and all IPv4 outbound traffic. It is not automatically attached to the CVM. Before attaching it, review existing security groups and IPv6 rules, and keep a working SSH connection. The Tunnel requires outbound DNS and TCP/UDP port 7844. Installation and updates also need HTTPS.

## Authentication

This repository does not configure Cloudflare Access. Grafana, InfluxDB, and Kuma use their own login systems. Homepage has no login protection configured here. You can configure Access in Cloudflare to restrict these endpoints; DNS records and the Tunnel alone do not authenticate users.

`health-ingest.<domain>` forwards only the following upload requests. Each receiver checks its own write credentials:

| Request | Authentication |
| --- | --- |
| `POST /api/data` | `api-key` header containing the official Health Auto Export write token |
| `POST /api/healthautoexport/v1/influxdb/ingest` | `Authorization: Bearer <token>` containing the InfluxDB receiver token |

The official service's read API is only available to Grafana inside the cluster. If you add Access to the upload domain, the phone automations also need the corresponding service credentials. Kuma's public status pages and administration UI share a hostname, so distinguish their paths when configuring access policies.
