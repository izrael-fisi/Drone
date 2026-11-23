data "aws_caller_identity" "current" {}

locals {
  repository = regex(
    "^([0-9]{12})\\.dkr\\.ecr\\.([a-z0-9-]+)\\.amazonaws\\.com/([^@]+)@",
    var.worker_image
  )
  repository_arn = "arn:aws:ecr:${local.repository[1]}:${local.repository[0]}:repository/${local.repository[2]}"
  names = {
    for id in keys(var.cameras) :
    id => "${var.name}-${lower(substr(id, 0, 23))}-${substr(sha256(id), 0, 8)}"
  }
  role_trust = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
      Action    = "sts:AssumeRole"
      Condition = { StringEquals = { "aws:SourceAccount" = data.aws_caller_identity.current.account_id } }
    }]
  })
}

resource "aws_ecs_cluster" "site" {
  name = var.name

  lifecycle {
    precondition {
      condition     = local.repository[1] == var.aws_region
      error_message = "Use a worker image from ECR in the site's AWS region."
    }
    precondition {
      condition     = length(var.aws_https_cidrs) + length(var.aws_https_prefix_list_ids) > 0
      error_message = "Allow the required AWS HTTPS endpoints directly or through approved NAT egress."
    }
  }
}

resource "aws_security_group" "worker" {
  for_each = var.cameras

  name_prefix = "${local.names[each.key]}-"
  description = "Outbound-only camera and gateway access for a CCTV worker"
  vpc_id      = var.vpc_id
  ingress     = []

  egress {
    description = "Approved RTSP stream"
    protocol    = "tcp"
    from_port   = each.value.rtsp_port
    to_port     = each.value.rtsp_port
    cidr_blocks = [each.value.rtsp_cidr]
  }

  egress {
    description     = "Private gateway and AWS HTTPS services"
    protocol        = "tcp"
    from_port       = 443
    to_port         = 443
    cidr_blocks     = sort(tolist(setunion(var.aws_https_cidrs, [each.value.gateway_cidr])))
    prefix_list_ids = sort(tolist(var.aws_https_prefix_list_ids))
  }
}

resource "aws_cloudwatch_log_group" "worker" {
  for_each = var.cameras

  name              = "/proxigo/cctv/${var.name}/${each.key}"
  retention_in_days = 14
}

resource "aws_sqs_queue" "worker" {
  for_each = var.cameras

  name                       = local.names[each.key]
  sqs_managed_sse_enabled    = true
  message_retention_seconds  = 345600
  visibility_timeout_seconds = 60
}
