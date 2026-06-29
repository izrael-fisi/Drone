# Hybrid To GovCloud Roadmap

## Goal

Use the hybrid commercial stack for prototype and development, then migrate
regulated workloads to AWS GovCloud when the product and customer requirements
are stable.

This roadmap avoids two traps:

- Overbuilding GovCloud before product-market fit.
- Coupling the desktop app directly to commercial Supabase/Vercel surfaces that
  would be painful to replace later.

## Phase 0: Documentation And Contracts

Status: planning only.

Deliverables:

- Cloud docs in this folder.
- Provider-neutral `/v1` API contract.
- Data classification policy.
- Map artifact manifest shape.
- Usage event and quota rules.
- Commercial vs GovCloud boundary statement.

Exit criteria:

- Team agrees desktop calls only `/v1`.
- Team agrees regulated data is blocked from commercial cloud.
- Team agrees which files and repos own docs, API clients, and infrastructure.

## Phase 1: Commercial Secure MVP

Target stack:

- Cloudflare Registrar and DNS.
- Vercel website and dashboard.
- Supabase Auth and Postgres.
- Resend email.
- Stripe billing.
- Cloudflare R2 for non-regulated artifacts.

Build:

- Commercial `/api/v1/*` facade in the website or a separate commercial API app.
- Supabase schema cleanup for profiles, orgs, memberships, modules, usage,
  maps, artifacts, quotas, and audit events.
- RLS tests for every Supabase table.
- R2 signed upload/download URL flow for non-regulated map artifacts.
- Desktop cloud sign-in and environment selector.
- OS keychain storage for desktop auth tokens and cloud secrets.

Exit criteria:

- Desktop can sign in to commercial cloud.
- Desktop can see org, quota, registered device/module, and cloud map list.
- Desktop can upload/download a non-regulated map artifact with signed URLs.
- Usage events are idempotent and visible in the website dashboard.
- No regulated data is accepted into the commercial artifact path.

## Phase 2: Operational Hardening

Build:

- Durable rate limiting.
- Audit log viewer.
- Security event export.
- Admin access review process.
- Backup and restore drill.
- Signed desktop releases.
- Tauri CSP and capability review.
- Pi companion API pairing token or mTLS plan.
- Vendor/subprocessor inventory.

Exit criteria:

- Security controls are ready for early enterprise review.
- Incident response and access review runbooks exist.
- Cloud logs, audit logs, backups, and release provenance are testable.

## Phase 3: GovCloud Foundation

Target stack:

- AWS GovCloud account and commercial parent/linked account as required.
- RDS PostgreSQL.
- S3 GovCloud buckets.
- KMS customer-managed keys.
- API Gateway plus Lambda, or ALB plus ECS/Fargate.
- Secrets Manager or SSM Parameter Store.
- CloudTrail, CloudWatch, GuardDuty, Security Hub, AWS Config.

Build:

- GovCloud infrastructure as code.
- GovCloud implementation of the same `/v1` API contract.
- GovCloud artifact storage and signed URL service.
- GovCloud auth integration through customer IdP/SAML/OIDC or AWS-native
  identity.
- Desktop `govcloud` environment profile.

Exit criteria:

- Contract tests pass against GovCloud APIs.
- GovCloud artifacts are encrypted with KMS and never routed through commercial
  services.
- Audit events are written for all security-relevant workflows.

## Phase 4: Migration Rehearsal

Build:

- Supabase metadata export script.
- R2 to S3 GovCloud artifact copy script for eligible artifacts.
- RDS import script.
- Integrity verification and checksum report.
- Desktop environment switch smoke test.

Exit criteria:

- Sample org, devices, maps, usage, and artifacts can be migrated.
- Desktop can use the migrated data against GovCloud.
- Regulated-data routing tests pass.

## Migration Rule

Commercial to GovCloud migration is allowed only for data that is legally and
contractually permitted to move. If a customer's data is already regulated, it
should be created directly in GovCloud rather than staged in commercial cloud.
