output "cluster_name" {
  value = aws_ecs_cluster.site.name
}

output "workers" {
  value = {
    for id, service in aws_ecs_service.worker : id => {
      service_name     = service.name
      task_definition  = aws_ecs_task_definition.worker[id].arn
      security_group   = aws_security_group.worker[id].id
      log_group        = aws_cloudwatch_log_group.worker[id].name
      outbox_queue_url = aws_sqs_queue.worker[id].url
    }
  }
}
