terraform {
  required_version = ">= 1.8.0, < 2.0.0"
  required_providers {
    tencentcloud = {
      source  = "tencentcloudstack/tencentcloud"
      version = "~> 1.82"
    }
    cloudflare = {
      source  = "cloudflare/cloudflare"
      version = "~> 5.8"
    }
  }
}
provider "tencentcloud" { region = "na-ashburn" }
provider "tencentcloud" {
  alias      = "cos"
  region     = "na-ashburn"
  secret_id  = trimspace(var.cos_secret_id)
  secret_key = trimspace(var.cos_secret_key)
}
provider "cloudflare" {} # CLOUDFLARE_API_TOKEN from 1Password
