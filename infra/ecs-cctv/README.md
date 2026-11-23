# Global View ECS site stack

Use this root for the first customer deployments. It provisions one ECS cluster per site and one Fargate service per cloud camera. Each camera gets an outbound-only security group, a startup role for its own secret, a runtime role for its own encrypted SQS queue, and a log group. Start with 0.5 vCPU and 1 GiB per ground worker and right-size from its measured sampling rate and alert delay. A service initially has zero tasks. Set `enabled = true` after staging its private configuration and publishing the camera policy.

## Deploy the development release

1. Use a private VPC with routes to the site's approved RTSP endpoint and HTTPS analytics gateway. Supply the existing VPC and task subnet IDs. To pull images, load secrets, emit logs, and use queues, the tasks need HTTPS access to ECR, S3, Secrets Manager, CloudWatch Logs, and SQS. Use private endpoints and DNS or approved NAT egress. For endpoint-only deployments, allow the interface endpoints' IPs or subnet CIDRs, their security groups' inbound HTTPS, and the S3 gateway endpoint prefix list. Routes for different sites must resolve to the intended camera and gateway, including when customer subnets overlap. [Fargate networking](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/fargate-task-networking.html) · [ECR endpoints](https://docs.aws.amazon.com/AmazonECR/latest/userguide/vpc-endpoints.html)
2. Authenticate Docker to an existing private ECR repository in the site's Region and set `PROXIGO_ECR_IMAGE` to its repository URI. Build and push the ECS target. Use an approved model directory containing the author-released `yolox_nano.onnx`. The build verifies the model's SHA-256. Record the image digest and use that digest for both evaluation and deployment. [YOLOX checkpoints](https://github.com/Megvii-BaseDetection/YOLOX/tree/main/demo/ONNXRuntime)

   ```bash
   docker buildx build --platform linux/amd64 --target ecs \
     --build-context cctvmodels=/path/to/approved/models \
     -f docker/cctv-analytics/Dockerfile \
     --tag "$PROXIGO_ECR_IMAGE:pilot" --push .
   ```

3. Create one Secrets Manager secret per camera using the AWS-managed Secrets Manager key. Its value is JSON with `source`, `gateway`, and `gatewayToken`. Use the same enrolled cloud token at the site gateway. Optional keys are `caBundle` with the private CA PEM, `width`, `height`, and `fps`. Default dimensions are 640 × 360 at 4 fps. Match the source aspect ratio. The task writes the configuration on its private ephemeral volume before invoking the worker. Pass only the secret ARN to Terraform. [ECS secret injection](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/secrets-envvar-secrets-manager.html)
4. Copy `pilot.tfvars.example` to `pilot.tfvars` and supply the real values. The camera map keys must match the published camera IDs. Use isolated Terraform state per site. Configure an existing encrypted, versioned S3 state bucket with a distinct key and the lockfile when sharing changes across a team. [Terraform S3 state](https://developer.hashicorp.com/terraform/language/backend/s3)

   ```bash
   terraform -chdir=infra/ecs-cctv init
   terraform -chdir=infra/ecs-cctv fmt -check -recursive
   terraform -chdir=infra/ecs-cctv validate
   terraform -chdir=infra/ecs-cctv test
   terraform -chdir=infra/ecs-cctv plan -var-file=pilot.tfvars -out=pilot.tfplan
   ```

5. Apply the approved plan. Use the cluster, task definition, and security group from the Terraform outputs for a one-off ECS task. Use the same private subnets, public IP disabled, and override the worker command with `["--check-connection"]`. Publish the camera's cloud policy in Observe mode before the check. The task verifies the site identity, RTSP stream, and telemetry path. Then enable the service in Terraform and use Posture to confirm the actual worker heartbeat. Deploy a fresh task after rotating credentials. [Cloud commissioning](../../docs/cctv-cloud-pilot.md)

## Growth and recovery

- Add cameras by adding map entries. Increase the CPU and memory of an individual worker after measuring it. With one service per camera, new deployments use a stop-before-start policy and can briefly interrupt inference. Add exclusive worker ownership before introducing overlapping releases or a scheduler that assigns the same camera across pools. [ECS deployment parameters](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/deployment-type-ecs.html)
- Add sites by repeating the stack with separate state, secrets, routes and camera roles. Depending on the customer's requirements, deploy it in a dedicated VPC or AWS account. For higher stream density or GPU models, add ECS on EC2 or ECS Managed Instances and adapt task storage to that compute option. Keep per-camera isolation and the site gateway API. [ECS compute options](https://aws.amazon.com/ecs/pricing/) · [ECS bind mounts](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/bind-mounts.html)
- The cloud worker sends detection metadata to its private SQS queue and deletes it after the gateway accepts or rejects the observation. Stable event IDs let the gateway deduplicate repeated delivery. SQS-acknowledged detections survive task replacement within the four-day retention window. If SQS is temporarily unavailable, the worker stages events in its task-local SQLite database and retries. That staging data exists only for the life of the task. Drain the queue before retiring a service or switching a camera to on-premises. [SQS delivery semantics](https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/standard-queues.html)
- Keep site recordings on the existing recorder. If a customer approves cloud archive, use S3 with lifecycle rules and AWS Batch jobs with their own compute limits for replay and backfills. The live worker processes the current approved stream. In on-premises mode the local outbox supplies the retry path. [S3 lifecycle](https://docs.aws.amazon.com/AmazonS3/latest/userguide/object-lifecycle-mgmt.html) · [AWS Batch compute environments](https://docs.aws.amazon.com/batch/latest/userguide/compute_environments.html)

The first deployment test should run two sites in Observe mode, replace one task while its site is unreachable, then confirm that its queued detections replay into the correct site once service resumes. Measure real-time inference rate and alert delay on each site before promoting rules.
