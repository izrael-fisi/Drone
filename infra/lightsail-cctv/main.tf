resource "aws_lightsail_instance" "worker" {
  name              = var.name
  availability_zone = var.availability_zone
  blueprint_id      = "ubuntu_24_04"
  bundle_id         = var.bundle_id
  ip_address_type   = "ipv4"
  key_pair_name     = var.ssh_key_pair_name
  user_data         = "printf '%s' '${filebase64("${path.module}/bootstrap.sh")}' | base64 -d | bash"

  lifecycle {
    precondition {
      condition     = startswith(var.availability_zone, var.aws_region)
      error_message = "Choose an Availability Zone in the configured AWS Region."
    }
  }
}

resource "aws_lightsail_instance_public_ports" "worker" {
  instance_name = aws_lightsail_instance.worker.name

  port_info {
    protocol  = "tcp"
    from_port = 22
    to_port   = 22
    cidrs     = var.admin_cidrs
  }
}

resource "aws_lightsail_static_ip" "worker" {
  name = "${var.name}-admin"
}

resource "aws_lightsail_static_ip_attachment" "worker" {
  static_ip_name = aws_lightsail_static_ip.worker.name
  instance_name  = aws_lightsail_instance.worker.name

  depends_on = [aws_lightsail_instance_public_ports.worker]
}
