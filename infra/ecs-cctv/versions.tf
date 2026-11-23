terraform {
  required_version = ">= 1.10, < 2.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Product      = "Global View"
      Workload     = "live-cctv"
      Organization = var.organization_id
      Site         = var.site_id
      ManagedBy    = "Terraform"
    }
  }
}
