# GovCloud Preparation

## Purpose

Prepare for a future AWS GovCloud deployment without forcing the prototype to
start in GovCloud.

GovCloud is the regulated environment path for customers with CUI, ITAR,
restricted mission data, or contractual requirements that commercial cloud
cannot satisfy.

## Eligibility And Account Setup

AWS GovCloud requires eligibility review. Root account holders must satisfy U.S.
person requirements. GovCloud accounts use separate credentials and account IDs
from standard AWS accounts.

Before implementation:

- Confirm company eligibility.
- Identify U.S. person account owner.
- Create commercial AWS account relationship required for GovCloud.
- Define who can access GovCloud production.
- Define customer data categories that must be isolated.

## Target GovCloud Architecture

Core services:

- API Gateway or ALB.
- Lambda or ECS/Fargate.
- RDS PostgreSQL.
- S3 GovCloud.
- KMS customer-managed keys.
- Secrets Manager or SSM Parameter Store.
- CloudWatch.
- CloudTrail.
- GuardDuty.
- Security Hub.
- AWS Config.

Network:

- Private subnets for database and internal services.
- Public entry only through API Gateway or ALB.
- VPC endpoints for S3, KMS, CloudWatch, Secrets Manager, and other supported
  services where possible.
- No public database endpoints.

Storage:

- S3 buckets blocked from public access.
- Server-side encryption with KMS.
- Object Lock for immutable audit logs if required.
- Separate buckets for artifacts, logs, backups, and temporary uploads.

## What Changes From Commercial Prototype

Commercial:

- Vercel API routes.
- Supabase Auth/Postgres.
- Cloudflare R2 for non-regulated artifacts.
- Resend for email.

GovCloud:

- GovCloud-hosted API.
- RDS PostgreSQL.
- S3 GovCloud.
- AWS-native logs, monitoring, secrets, and KMS.
- Customer IdP/SAML/OIDC or AWS-approved identity approach.

Shared:

- `/v1` API contract.
- Map artifact manifest.
- Usage event model.
- Audit event model.
- Data classification policy.

## Data Boundary

Never send `cui`, `itar`, or `restricted` artifacts to commercial endpoints.

GovCloud-only data:

- Regulated map cuts.
- Regulated mission overlays.
- Regulated support bundles.
- Regulated telemetry/log artifacts.
- Customer-controlled mission areas under regulated contracts.

Commercial-safe data:

- Public marketing content.
- Non-regulated prototype maps.
- General product telemetry if allowed by policy.
- Billing metadata.
- Support requests that do not include regulated data.

## Infrastructure As Code

Recommended layout for future `ProxigoCloud` repo:

```text
infra/govcloud/
  network/
  api/
  database/
  storage/
  observability/
  security-baseline/
```

Use Terraform or AWS CDK, but pick one before writing production IaC.

## Migration Rehearsal

Rehearse migration with non-regulated sample data:

1. Export Supabase metadata.
2. Transform into GovCloud RDS schema.
3. Copy sample R2 artifacts to S3 GovCloud.
4. Verify checksums.
5. Run `/v1` contract tests against GovCloud API.
6. Switch desktop to `govcloud` environment.
7. Validate login, map list, signed download, usage event, and audit logs.

## Source Links

- AWS GovCloud overview: https://aws.amazon.com/govcloud-us/
- AWS GovCloud compliance: https://docs.aws.amazon.com/govcloud-us/latest/UserGuide/govcloud-compliance.html
- AWS GovCloud ITAR: https://docs.aws.amazon.com/govcloud-us/latest/UserGuide/govcloud-itar.html
- AWS Lambda pricing: https://aws.amazon.com/lambda/pricing/
- AWS S3 pricing: https://aws.amazon.com/s3/pricing/
