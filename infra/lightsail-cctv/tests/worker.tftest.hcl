mock_provider "aws" {}

variables {
  ssh_key_pair_name = "test-admin"
  admin_cidrs       = ["203.0.113.10/32"]
}

run "closed_media_ingress" {
  command = plan

  assert {
    condition     = aws_lightsail_instance.worker.ip_address_type == "ipv4"
    error_message = "Use IPv4 only for the pilot worker."
  }

  assert {
    condition = alltrue([
      for port in aws_lightsail_instance_public_ports.worker.port_info :
      port.protocol == "tcp" && port.from_port == 22 && port.to_port == 22 && port.cidrs == var.admin_cidrs
    ]) && length(aws_lightsail_instance_public_ports.worker.port_info) == 1
    error_message = "Allow only SSH from the specified administrators."
  }
}

run "reject_public_ssh" {
  command = plan

  variables {
    admin_cidrs = ["0.0.0.0/0"]
  }

  expect_failures = [var.admin_cidrs]
}
