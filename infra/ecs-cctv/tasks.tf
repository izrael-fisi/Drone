resource "aws_ecs_task_definition" "worker" {
  for_each = var.cameras

  family                   = local.names[each.key]
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = coalesce(each.value.cpu, var.worker_capacity.cpu)
  memory                   = coalesce(each.value.memory, var.worker_capacity.memory)
  execution_role_arn       = aws_iam_role.execution[each.key].arn
  task_role_arn            = aws_iam_role.worker[each.key].arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }

  volume {
    name = "worker-state"
  }

  container_definitions = jsonencode([{
    name                   = "worker"
    image                  = each.value.worker_image_digest == null ? var.worker_image : "${split("@", var.worker_image)[0]}@${each.value.worker_image_digest}"
    essential              = true
    user                   = "10001:10001"
    readonlyRootFilesystem = true
    stopTimeout            = 30
    linuxParameters        = { initProcessEnabled = true, capabilities = { drop = ["ALL"] } }
    mountPoints            = [{ sourceVolume = "worker-state", containerPath = "/state", readOnly = false }]
    environment = [
      { name = "PROXIGO_ORGANIZATION_ID", value = var.organization_id },
      { name = "PROXIGO_SITE_ID", value = var.site_id },
      { name = "PROXIGO_CAMERA_ID", value = each.key },
      { name = "PROXIGO_CCTV_QUEUE_URL", value = aws_sqs_queue.worker[each.key].url }
    ]
    secrets = [{ name = "PROXIGO_CCTV_SECRET", valueFrom = each.value.secret_arn }]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.worker[each.key].name
        "awslogs-region"        = var.aws_region
        "awslogs-stream-prefix" = "worker"
      }
    }
  }])
}

resource "aws_ecs_service" "worker" {
  for_each = var.cameras

  name                               = local.names[each.key]
  cluster                            = aws_ecs_cluster.site.id
  task_definition                    = aws_ecs_task_definition.worker[each.key].arn
  launch_type                        = "FARGATE"
  platform_version                   = "1.4.0"
  desired_count                      = each.value.enabled ? 1 : 0
  deployment_minimum_healthy_percent = 0
  deployment_maximum_percent         = 100

  network_configuration {
    subnets          = var.private_subnet_ids
    security_groups  = [aws_security_group.worker[each.key].id]
    assign_public_ip = false
  }

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  depends_on = [aws_iam_role_policy.execution, aws_iam_role_policy.worker]
}
