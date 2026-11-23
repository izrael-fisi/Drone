variable "name" {
  type        = string
  description = "Unique Lightsail instance name for this pilot worker host."
  default     = "proxigo-cctv-pilot"

  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9-]{1,46}[a-z0-9]$", var.name))
    error_message = "Use 3–48 lowercase letters, digits and hyphens, starting and ending with a letter or digit."
  }
}

variable "aws_region" {
  type        = string
  description = "Commercial AWS Region for the pilot."
  default     = "us-east-1"
}

variable "availability_zone" {
  type        = string
  description = "Lightsail Availability Zone within the selected Region."
  default     = "us-east-1a"
}

variable "bundle_id" {
  type        = string
  description = "Linux IPv4 instance bundle. The pilot default has 2 vCPUs and 4 GB RAM."
  default     = "medium_3_0"
}

variable "ssh_key_pair_name" {
  type        = string
  description = "Name of an existing Lightsail key pair in this Region."

  validation {
    condition     = length(trimspace(var.ssh_key_pair_name)) > 0
    error_message = "Provide the name of the Lightsail SSH key pair."
  }
}

variable "admin_cidrs" {
  type        = set(string)
  description = "IPv4 host addresses (each /32) permitted to use the public SSH port."

  validation {
    condition = length(var.admin_cidrs) > 0 && alltrue([
      for cidr in var.admin_cidrs : can(cidrnetmask(cidr)) && endswith(cidr, "/32")
    ])
    error_message = "Specify at least one individual IPv4 administrator address with a /32 prefix."
  }
}
