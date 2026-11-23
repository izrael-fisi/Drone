resource "aws_iam_role" "execution" {
  for_each = var.cameras

  name               = "${local.names[each.key]}-exec"
  assume_role_policy = local.role_trust
}

resource "aws_iam_role_policy" "execution" {
  for_each = var.cameras

  name = "camera-worker-startup"
  role = aws_iam_role.execution[each.key].name
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = "ecr:GetAuthorizationToken", Resource = "*" },
      { Effect = "Allow", Action = ["ecr:BatchCheckLayerAvailability", "ecr:GetDownloadUrlForLayer", "ecr:BatchGetImage"], Resource = local.repository_arn },
      { Effect = "Allow", Action = ["logs:CreateLogStream", "logs:PutLogEvents"], Resource = "${aws_cloudwatch_log_group.worker[each.key].arn}:*" },
      { Effect = "Allow", Action = "secretsmanager:GetSecretValue", Resource = each.value.secret_arn }
    ]
  })
}

resource "aws_iam_role" "worker" {
  for_each = var.cameras

  name               = "${local.names[each.key]}-task"
  assume_role_policy = local.role_trust
}

resource "aws_iam_role_policy" "worker" {
  for_each = var.cameras

  name = "camera-detection-queue"
  role = aws_iam_role.worker[each.key].name
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["sqs:SendMessage", "sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes"]
      Resource = aws_sqs_queue.worker[each.key].arn
    }]
  })
}
