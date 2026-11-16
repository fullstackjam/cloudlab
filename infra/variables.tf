variable "instance_id" {
  type        = string
  description = "Existing CVM instance ID, verified in the Tencent Cloud console"
}
variable "image_id" {
  type        = string
  description = "Exact existing Ubuntu 24.04 image ID"
}
variable "vpc_id" {
  type        = string
  description = "Existing VPC"
}
variable "subnet_id" {
  type        = string
  description = "Existing subnet"
}
variable "backup_bucket" {
  type        = string
  description = "Globally unique bucket name including the COS account APPID suffix"
}
variable "cos_secret_id" {
  type        = string
  description = "Tencent Cloud SecretId for managing the COS backup bucket"
  sensitive   = true
  nullable    = false
}
variable "cos_secret_key" {
  type        = string
  description = "Tencent Cloud SecretKey for managing the COS backup bucket"
  sensitive   = true
  nullable    = false
}
variable "cloudflare_account_id" {
  type        = string
  description = "Existing account ID"
}
variable "ssh_cidr" {
  type        = string
  description = "Trusted public SSH source CIDR, e.g. 203.0.113.10/32"
}
