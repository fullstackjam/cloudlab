# Adoption only: no cloud-init or disk mutations on the running node.
import {
  to = tencentcloud_instance.cloudlab
  id = var.instance_id
}
resource "tencentcloud_instance" "cloudlab" {
  instance_name     = "cloudlab"
  availability_zone = "na-ashburn-1"
  instance_type     = "SA5.LARGE16"
  image_id          = var.image_id
  vpc_id            = var.vpc_id
  subnet_id         = var.subnet_id
  system_disk_size  = 100
  lifecycle {
    prevent_destroy = true
    ignore_changes  = all
  }
}
# Not attached automatically: preserve current SSH access and existing SGs.
resource "tencentcloud_security_group" "cloudlab" {
  name        = "cloudlab"
  description = "Single node: SSH from trusted IP; tunnel egress; no public Kubernetes"
}
resource "tencentcloud_security_group_rule_set" "cloudlab" {
  security_group_id = tencentcloud_security_group.cloudlab.id
  ingress {
    action     = "ACCEPT"
    cidr_block = var.ssh_cidr
    port       = "22"
    protocol   = "TCP"
  }
  egress {
    action     = "ACCEPT"
    cidr_block = "0.0.0.0/0"
    port       = "all"
    protocol   = "ALL"
  }
}
resource "tencentcloud_cos_bucket" "backups" {
  provider          = tencentcloud.cos
  bucket            = var.backup_bucket
  acl               = "private"
  versioning_enable = true
  force_clean       = false
  lifecycle { prevent_destroy = true }
  # Restic owns retention; NEVER expire current pack objects by age.
  lifecycle_rules {
    id            = "restic-noncurrent-35d"
    filter_prefix = "restic/"
    non_current_expiration { non_current_days = 35 }
  }
}
resource "cloudflare_zero_trust_tunnel_cloudflared" "cloudlab" {
  account_id = var.cloudflare_account_id
  name       = "cloudlab"
  config_src = "cloudflare"
}
resource "cloudflare_zero_trust_tunnel_cloudflared_config" "cloudlab" {
  account_id = var.cloudflare_account_id
  tunnel_id  = cloudflare_zero_trust_tunnel_cloudflared.cloudlab.id
  config = {
    ingress = [
      { hostname = "*.fullstackjam.com", service = "http://traefik.infra.svc.cluster.local:80" },
      { service = "http_status:404" }
    ]
  }
}
output "tunnel_id" { value = cloudflare_zero_trust_tunnel_cloudflared.cloudlab.id }
output "backup_bucket" { value = tencentcloud_cos_bucket.backups.bucket }
