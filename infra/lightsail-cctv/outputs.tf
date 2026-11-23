output "instance_name" {
  description = "Worker instance name in Lightsail."
  value       = aws_lightsail_instance.worker.name
}

output "ssh_address" {
  description = "Static IPv4 address for commissioning. Camera and gateway traffic use the private site route."
  value       = aws_lightsail_static_ip.worker.ip_address
}
