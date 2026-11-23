# Global View Lightsail worker

This Terraform root provisions one Ubuntu 24.04 Lightsail host with Docker and Compose, a static IPv4 address, and an SSH rule for named administrator IPv4 addresses. The camera worker uses the site's private media and HTTPS routes. Each site gateway owns its rules and incidents. The pilot bundle is `medium_3_0`. Review the account's active bundles and Region before planning. [Lightsail bundles](https://docs.aws.amazon.com/lightsail/latest/userguide/amazon-lightsail-bundles.html) · [Lightsail SSH keys](https://docs.aws.amazon.com/lightsail/latest/userguide/amazon-lightsail-ssh-keys-and-connecting-to-your-instance.html)

## Validate and plan

From the repository root, run the following local checks. The Terraform tests use a mocked AWS provider.

```bash
terraform -chdir=infra/lightsail-cctv init
terraform -chdir=infra/lightsail-cctv fmt -check -recursive
terraform -chdir=infra/lightsail-cctv validate
terraform -chdir=infra/lightsail-cctv test
```

For a real plan, use the team's AWS role and an existing Lightsail key pair in the selected Region. Copy `pilot.tfvars.example` to `pilot.tfvars` and enter the key pair name and the administrators' public IPv4 addresses. Set both the Region and Availability Zone if you deploy outside the defaults.

```bash
terraform -chdir=infra/lightsail-cctv plan -var-file=pilot.tfvars -out=pilot.tfplan
```

After approving the billable deployment, apply the saved plan.

```bash
terraform -chdir=infra/lightsail-cctv apply pilot.tfplan
terraform -chdir=infra/lightsail-cctv output -raw ssh_address
```

Lightsail installs its initial firewall rules at instance creation. Terraform reconciles them before attaching the static address. Wait for apply and verify Docker is running before enrolling the host on the site network. Use the instance address for commissioning SSH, then connect to the camera and the gateway over the approved private network. [Lightsail firewall behavior](https://docs.aws.amazon.com/lightsail/latest/userguide/understanding-firewall-and-port-mappings-in-amazon-lightsail.html)

## State and credentials

Store the camera URL, token, TLS trust bundle if required, and VPN enrollment outside Terraform. Inject these files on the host through the approved secrets workflow. For shared infrastructure state, configure an existing encrypted, versioned S3 bucket with `use_lockfile = true` and narrow IAM access. Keep the state and plan files out of source control. [S3 state backend](https://developer.hashicorp.com/terraform/language/backend/s3)

## Commission the interconnect

Install the current release on the site gateway and stage the matching worker container on the provisioned host. The cloud pilot runbook covers the private route, worker credentials, connection preflight, and service start. [Cloud pilot runbook](../../docs/cctv-cloud-pilot.md)
