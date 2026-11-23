# Global View cloud analytics pilot

Use the ECS on Fargate Terraform stack for early customer deployments. It supports separate site stacks, per-camera sizing, and durable cloud detection delivery. Use a single Lightsail or DigitalOcean VM for demos and benchmarks. Add streams after measuring CPU, memory, decode lag, actual inference rate, and end-to-end incident delay. [ECS deployment](../infra/ecs-cctv/README.md)

| Option | Listed compute cost in USD | Fit |
| --- | --- | --- |
| AWS Lightsail Linux with public IPv4 | $24/month for 2 vCPUs, 4 GB, 80 GB SSD and a 4 TB transfer allowance in the listed regions. | One VM and Docker Compose. Use the Lightsail firewall for public ingress and host rules for private traffic. [Bundles](https://docs.aws.amazon.com/lightsail/latest/userguide/amazon-lightsail-bundles.html) · [Firewall behavior](https://docs.aws.amazon.com/lightsail/latest/userguide/understanding-firewall-and-port-mappings-in-amazon-lightsail.html) |
| DigitalOcean Basic Droplet | $24/month for 2 shared vCPUs, 4 GiB, 80 GiB SSD and 4,000 GiB transfer. Dedicated CPU starts at $42/month for 2 vCPUs and 4 GiB. | Quick setup with the Docker 1-Click image. The dedicated CPU tier is an option if the shared instance misses its inference rate under sustained load. [Droplet pricing](https://www.digitalocean.com/pricing/droplets) · [Docker image](https://docs.digitalocean.com/products/marketplace/catalog/docker/) |
| Amazon ECS on Fargate | Metered vCPU and memory, plus storage and network charges. | Initial customer deployment using private routes, per-camera tasks and SQS. ECS on EC2 or ECS Managed Instances can provide a separate GPU pool when needed. [Pricing](https://aws.amazon.com/fargate/pricing/) · [Fargate capabilities](https://aws.amazon.com/fargate/faqs/) |

VM prices exclude overlay networking, backups, logs, operator portal hosting, and any overage. A continuous 1 Mbps camera stream transfers approximately 324 GB in 30 days before protocol overhead. Measure bit rate on the substream that preserves the detail needed by the rule. A commercial Tailscale Standard plan lists $8 per user per month. An approved corporate VPN can provide the same routing. [Tailscale pricing](https://tailscale.com/pricing)

The Lightsail demo stack provisions a VM, restricted SSH firewall, static management IP, and Docker packages. The Compose workflow below runs on the demo VM or an on-premises host. [Lightsail Terraform](../infra/lightsail-cctv/README.md)

## Pilot topology

The site gateway owns the site configuration and incident state. The worker connects to the approved camera or recorder channel and the site's HTTPS analytics API through private routing. Deploy the existing Global View enterprise portal as the browser-facing HTTPS service with the customer's OIDC provider when remote operator sign-in is part of the pilot. [Enterprise portal setup](global-view-monitoring.md#enterprise-sign-in)

1. Give the worker host private routes to the exact camera or recorder address and the gateway's TLS endpoint. With Tailscale, use a site subnet router, approve only the required routes, and scope grants to the worker identity and the approved RTSP and HTTPS ports. Keep public inbound ports closed for the worker. Use an existing approved VPN and a separate worker scope for each customer. [Subnet router setup](https://tailscale.com/docs/features/subnet-routers) · [Access grants](https://tailscale.com/docs/features/access-control/grants)
2. Keep the site gateway listener private. At the worker's private HTTPS origin, permit `GET` and `POST` for `/api/cameras/<camera-id>/analytics` and `POST` for `/api/site/telemetry`. Give that worker its own camera-scoped cloud token. For a private CA, install the PEM bundle in the worker's private directory and set `SSL_CERT_FILE=/private/ca-bundle.pem` when invoking Compose. Use an origin whose certificate name resolves inside the container.
3. Stage a trusted ONNX model in the model directory. Use the author's YOLOX-Nano checkpoint and record its SHA-256. Store the worker JSON and token under restricted host permissions, readable by container UID 10001. Keep secret files and customer footage out of the repository and images. Start the first pilot with public footage or footage approved for commercial cloud processing. [Official model files](https://github.com/Megvii-BaseDetection/YOLOX/tree/main/demo/ONNXRuntime)

## Start one Compose worker

Save the following as `/srv/proxigo/yard/private/worker.json`. Replace the site addresses and device ID with the approved values. Place the camera worker token in `/srv/proxigo/yard/private/gateway.token`.

```json
{
  "sensorId": "yard-camera",
  "organizationId": "example-org",
  "siteId": "example-site",
  "placement": "cloud",
  "source": "rtsp://192.0.2.12:554/analytics",
  "gateway": "https://analytics.site.example",
  "tokenFile": "/private/gateway.token",
  "state": "/state/outbox.sqlite3",
  "width": 640,
  "height": 360,
  "fps": 4,
  "models": {
    "ground": {
      "kind": "yolox",
      "path": "/models/yolox_nano.onnx",
      "sha256": "c789161ed43c8269fcd4e67c67eeeb4e80c622da2eb296a20bc6007bd18a0b7d",
      "fps": 4
    }
  }
}
```

Run from the repository root on the target host. Each camera gets a distinct Compose project name, private configuration directory and persistent outbox. The read-only container publishes no application ports. The model directory may be shared between workers.

```bash
export PROXIGO_CCTV_PRIVATE_DIR=/srv/proxigo/yard/private
export PROXIGO_CCTV_MODELS_DIR=/srv/proxigo/models
docker compose -f docker/cctv-analytics/compose.yaml -p proxigo-yard config --quiet
docker compose -f docker/cctv-analytics/compose.yaml -p proxigo-yard build
```

Publish the camera with **Cloud** and its first rule in **Observe only**. Start with a public RTSP fixture. Run the connection check from the worker container, then start the service.

```bash
docker compose -f docker/cctv-analytics/compose.yaml -p proxigo-yard run --rm --no-deps \
  worker --config /private/worker.json --check-connection
docker compose -f docker/cctv-analytics/compose.yaml -p proxigo-yard up -d
docker compose -f docker/cctv-analytics/compose.yaml -p proxigo-yard logs --tail 30 worker
```

The check fetches the authenticated policy, matches the organization, site, camera and placement, decodes one RTSP frame, then requests a receipt from the worker telemetry endpoint. The gateway verifies the worker token and current policy version. The receipt is read-only and contains connection status and identifiers. The running worker also checks the configured organization and site on each policy refresh. Use **Posture → CCTV analytics** after startup to confirm the expected model and sampling rate. Verify observation delivery and recovery after worker restart, and keep the named volume when replacing the container.

For a site that requires local inference, use the same Compose file on a site host with `"placement": "on-premises"`, a local RTSP source, and a site HTTPS gateway reachable from the container. Publish the new placement, confirm the site worker, and revoke the cloud network and stream credentials for that camera. [Compute placement and enrollment](cctv-analytics.md#compute-placement-and-video-access)

## Qualification

Run the labeled MEVA replay first. Then replay the same stream through the private cloud pilot and the on-premises worker in Observe mode. Score each rule against the reference events and separately review continuous ordinary activity for false alerts. Measure end-to-end alert delay with synchronized clocks. Before enabling alerts, repeat on the installed cameras and apply the customer's acceptance targets for missed events, false alerts per camera-day, and delay. [Labeled replay](cctv-analytics.md#score-a-labeled-entrance-tripwire) · [Field qualification](cctv-analytics.md#qualification-before-customer-alarms)
