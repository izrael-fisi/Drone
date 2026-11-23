variable "aws_region" {
  description = "Commercial AWS region for this site and its worker image."
  type        = string
  default     = "us-east-1"
}

variable "name" {
  description = "Unique stack name in this AWS account, including across regions."
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9-]{1,18}[a-z0-9]$", var.name))
    error_message = "Use a unique name of 3–20 lowercase letters, digits, and hyphens."
  }
}

variable "organization_id" {
  description = "Organization ID published by the site gateway."
  type        = string

  validation {
    condition     = can(regex("^[A-Za-z0-9_-]{1,96}$", var.organization_id))
    error_message = "Use the organization ID from Global View."
  }
}

variable "site_id" {
  description = "Site ID published by the site gateway."
  type        = string

  validation {
    condition     = can(regex("^[A-Za-z0-9_-]{1,96}$", var.site_id))
    error_message = "Use the site ID from Global View."
  }
}

variable "vpc_id" {
  description = "Existing VPC with approved routes to the site's private gateway and camera network."
  type        = string
}

variable "private_subnet_ids" {
  description = "Existing private task subnets with the site's routes and AWS service access."
  type        = set(string)

  validation {
    condition     = length(var.private_subnet_ids) > 0
    error_message = "Supply at least one private task subnet."
  }
}

variable "aws_https_cidrs" {
  description = "IPv4 destination ranges for AWS API endpoints or approved HTTPS egress through NAT."
  type        = set(string)
  default     = []

  validation {
    condition     = alltrue([for cidr in var.aws_https_cidrs : can(cidrnetmask(cidr))])
    error_message = "AWS endpoint egress requires IPv4 CIDRs."
  }
}

variable "aws_https_prefix_list_ids" {
  description = "Optional VPC prefix lists for AWS HTTPS traffic, including the S3 gateway endpoint for ECR layers."
  type        = set(string)
  default     = []
}

variable "worker_image" {
  description = "ECS worker image from private ECR, pinned to an image digest."
  type        = string

  validation {
    condition     = can(regex("^[0-9]{12}\\.dkr\\.ecr\\.[a-z0-9-]+\\.amazonaws\\.com/[a-z0-9/_-]+@sha256:[0-9a-f]{64}$", var.worker_image))
    error_message = "Provide a private ECR image URI with an immutable SHA-256 digest."
  }
}

variable "cameras" {
  description = "Published Global View camera IDs and per-camera settings. Each enabled service runs one active worker."
  type = map(object({
    secret_arn   = string
    rtsp_cidr    = string
    gateway_cidr = string
    rtsp_port    = optional(number, 554)
    enabled      = optional(bool, false)
    cpu          = optional(number, 512)
    memory       = optional(number, 1024)
  }))

  validation {
    condition = length(var.cameras) > 0 && alltrue([
      for id, camera in var.cameras : can(regex("^[A-Za-z0-9_-]{1,96}$", id))
      && can(regex("^arn:aws:secretsmanager:[a-z0-9-]+:[0-9]{12}:secret:[^:*]+$", camera.secret_arn))
      && can(cidrnetmask(camera.rtsp_cidr)) && endswith(camera.rtsp_cidr, "/32")
      && can(cidrnetmask(camera.gateway_cidr)) && endswith(camera.gateway_cidr, "/32")
      && camera.rtsp_port >= 1 && camera.rtsp_port <= 65535 && floor(camera.rtsp_port) == camera.rtsp_port
    ])
    error_message = "Each camera needs its exact Global View ID, a Secret ARN, two IPv4 /32 destinations and a valid RTSP port."
  }

  validation {
    condition     = length(distinct([for camera in values(var.cameras) : camera.secret_arn])) == length(var.cameras)
    error_message = "Enroll each camera with a distinct worker secret."
  }
}
