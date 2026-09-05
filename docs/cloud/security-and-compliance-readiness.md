# Security And Compliance Readiness

## Target

Optimize first for SOC 2 and GDPR/CCPA readiness. Prepare the architecture so
GovCloud, CUI, ITAR, and defense/public-sector controls can be added later.

This is not a formal compliance attestation. It is an implementation readiness
scaffold.

## Commercial Security Controls

Required before production:

- Cloudflare account MFA/passkeys.
- Cloudflare DNSSEC.
- Vercel production/staging separation.
- Supabase RLS on every table.
- RLS tests for every table.
- No service-role keys in browser or desktop.
- Durable API rate limiting.
- Audit events for security-relevant actions.
- Desktop auth tokens stored in OS keychain.
- Desktop secrets not stored as plaintext JSON.
- Tauri CSP enabled.
- Signed desktop releases.
- Signed map upload/download URLs.
- Basic dependency scanning.
- Secret scanning.

## Current Known Gaps To Fix

Website:

- Some organization routes reference schema fields/tables not fully represented
  in the migrations that were reviewed.
- In-memory rate limiting is not durable and will not work reliably across
  serverless instances.
- `/api/admin/bootstrap` should be disabled or deleted after first admin setup.
- CSP currently allows development-friendly directives that should be tightened.

Desktop:

- Local profile and device data is stored as JSON.
- Cloud auth and keychain storage are not implemented yet.
- Tauri config currently has no CSP.
- Direct cloud account/org/map API client does not exist yet.

Pi companion API:

- LAN HTTP is useful for bench testing, but service-control endpoints need a
  pairing token or mTLS before any cloud-connected workflow.

## SOC 2 Readiness Areas

Security:

- Access controls.
- Least privilege.
- Vulnerability management.
- Secure development lifecycle.
- Change management.
- Incident response.
- Audit logging.

Availability:

- Backups.
- Restore drills.
- Monitoring and alerts.
- Dependency/service status review.

Confidentiality:

- Data classification.
- Encryption at rest and in transit.
- Key management.
- Vendor/subprocessor inventory.

Privacy:

- Account deletion.
- Data export.
- Retention policy.
- Privacy notice.
- Analytics opt-in/opt-out where required.

## GDPR/CCPA Readiness

Implement before public production:

- Account data export process.
- Account deletion process.
- Retention policy.
- Subprocessor list.
- Privacy policy.
- Data classification map.
- Customer support workflow for privacy requests.
- Audit trail for deletion/export requests.

## GovCloud Readiness

Future requirements:

- Separate GovCloud account.
- U.S. person root account holder eligibility.
- Regulated data stored only in GovCloud services.
- KMS customer-managed keys.
- CloudTrail in all accounts/regions.
- GuardDuty, Security Hub, AWS Config.
- S3 Object Lock for immutable audit logs where required.
- Private networking for databases and internal services.
- Customer IdP federation where required.

## Audit Events

Record audit events for:

- Login and logout where available.
- Token refresh failure.
- Org create/join/leave.
- Member approve/reject.
- Role change.
- Quota change.
- Device registration.
- Pairing token issuance.
- Map upload initiation.
- Map upload completion.
- Map download token issuance.
- Usage event ingestion.
- Admin promotion.
- Account deletion request.

## Source Links

- AWS GovCloud compliance: https://docs.aws.amazon.com/govcloud-us/latest/UserGuide/govcloud-compliance.html
- AWS GovCloud overview: https://aws.amazon.com/govcloud-us/
- Cloudflare Registrar: https://www.cloudflare.com/products/registrar/
- Cloudflare Turnstile: https://developers.cloudflare.com/turnstile/plans/
