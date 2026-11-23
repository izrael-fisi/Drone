mock_provider "aws" {}

variables {
  name               = "proxigo-pilot"
  organization_id    = "org-one"
  site_id            = "site-one"
  vpc_id             = "vpc-0123456789abcdef0"
  private_subnet_ids = ["subnet-0123456789abcdef0"]
  aws_https_cidrs    = ["10.20.30.0/28"]
  worker_image       = "123456789012.dkr.ecr.us-east-1.amazonaws.com/proxigo-cctv@sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
  cameras = {
    yard = {
      secret_arn   = "arn:aws:secretsmanager:us-east-1:123456789012:secret:proxigo/yard-example"
      rtsp_cidr    = "192.0.2.12/32"
      gateway_cidr = "192.0.2.15/32"
      enabled      = true
    }
    dock = {
      secret_arn   = "arn:aws:secretsmanager:us-east-1:123456789012:secret:proxigo/dock-example"
      rtsp_cidr    = "192.0.2.13/32"
      gateway_cidr = "192.0.2.15/32"
      cpu          = 1024
      memory       = 2048
    }
  }
}

run "camera_services_have_scoped_capacity" {
  command = plan

  assert {
    condition = (
      aws_ecs_service.worker["yard"].desired_count == 1 &&
      aws_ecs_service.worker["dock"].desired_count == 0 &&
      aws_ecs_service.worker["yard"].deployment_maximum_percent == 100 &&
      !one(aws_ecs_service.worker["yard"].network_configuration).assign_public_ip &&
      length(aws_security_group.worker["yard"].ingress) == 0
    )
    error_message = "Each camera must use a private zero-or-one worker service."
  }

  assert {
    condition = (
      tonumber(aws_ecs_task_definition.worker["dock"].cpu) == 1024 &&
      tonumber(aws_ecs_task_definition.worker["yard"].cpu) == 512 &&
      aws_iam_role.execution["dock"].name != aws_iam_role.execution["yard"].name &&
      aws_sqs_queue.worker["dock"].name != aws_sqs_queue.worker["yard"].name &&
      aws_sqs_queue.worker["dock"].sqs_managed_sse_enabled
    )
    error_message = "Cameras must have independent sizing, roles and encrypted queues."
  }
}

run "second_site_reuses_camera_ids" {
  command = plan

  variables {
    name            = "proxigo-second"
    organization_id = "org-two"
    site_id         = "site-two"
  }

  assert {
    condition     = output.workers["yard"].service_name != run.camera_services_have_scoped_capacity.workers["yard"].service_name
    error_message = "The same camera ID must produce a different service name in a separate site stack."
  }
}

run "reject_shared_camera_credentials" {
  command = plan

  variables {
    cameras = {
      yard = {
        secret_arn   = "arn:aws:secretsmanager:us-east-1:123456789012:secret:proxigo/shared-example"
        rtsp_cidr    = "192.0.2.12/32"
        gateway_cidr = "192.0.2.15/32"
      }
      dock = {
        secret_arn   = "arn:aws:secretsmanager:us-east-1:123456789012:secret:proxigo/shared-example"
        rtsp_cidr    = "192.0.2.13/32"
        gateway_cidr = "192.0.2.15/32"
      }
    }
  }

  expect_failures = [var.cameras]
}
