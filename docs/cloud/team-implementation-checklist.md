# Team Implementation Checklist

## Product

- [ ] Decide final domain names.
- [ ] Decide commercial vs regulated customer onboarding.
- [ ] Define customer-visible data classifications.
- [ ] Define default map retention policy.
- [ ] Define quota and billing model.
- [ ] Define org member roles.
- [ ] Define support-bundle sensitivity rules.
- [ ] Decide if GovCloud is customer-specific or shared regulated tenancy.

## Website

- [ ] Add Cloud + GovCloud doc links to website docs when ready.
- [ ] Build account/org cloud dashboard.
- [ ] Build cloud map list and map details.
- [ ] Build usage dashboard.
- [ ] Build audit event view for admins.
- [ ] Build admin quota controls.
- [ ] Add Turnstile or equivalent bot protection where needed.
- [ ] Replace in-memory rate limiting with durable rate limiting.
- [ ] Disable or delete admin bootstrap route after first admin.

## Desktop

- [ ] Add cloud environment selector.
- [ ] Add commercial/staging/GovCloud API base URL config.
- [ ] Add browser-based login.
- [ ] Store auth tokens in OS keychain.
- [ ] Move cloud provider keys and secrets out of plaintext JSON.
- [ ] Add `/v1/me` account sync.
- [ ] Add org/quota status.
- [ ] Add cloud map list.
- [ ] Add signed map upload flow.
- [ ] Add signed map download flow.
- [ ] Add usage event sync.
- [ ] Add offline queue for usage events.
- [ ] Add blocked state for regulated data in commercial environment.

## Backend

- [ ] Create provider-neutral `/v1` API contract.
- [ ] Add OpenAPI file.
- [ ] Implement commercial API facade.
- [ ] Add Supabase schema cleanup migrations.
- [ ] Add RLS policies and tests.
- [ ] Add `api_idempotency_keys`.
- [ ] Add audit event writes.
- [ ] Add signed R2 URL service.
- [ ] Add quota enforcement.
- [ ] Add map upload completion checksum validation.
- [ ] Add usage event deduplication.
- [ ] Add GovCloud implementation later using same contract.

## DevOps And Security

- [ ] Register or transfer domains to Cloudflare.
- [ ] Enable DNSSEC.
- [ ] Enforce MFA/passkeys.
- [ ] Configure Vercel production and staging domains.
- [ ] Configure Supabase production and staging projects.
- [ ] Configure Cloudflare R2 buckets for non-regulated data.
- [ ] Add secret scanning.
- [ ] Add dependency scanning.
- [ ] Add release signing.
- [ ] Add backup and restore drill.
- [ ] Create incident response runbook.
- [ ] Create access review process.
- [ ] Start GovCloud eligibility checklist.
- [ ] Draft GovCloud IaC baseline.

## Compliance

- [ ] Data classification policy.
- [ ] Data retention policy.
- [ ] Privacy request process.
- [ ] Account deletion process.
- [ ] Account export process.
- [ ] Subprocessor inventory.
- [ ] Vendor evidence folder.
- [ ] Audit log matrix.
- [ ] Security control owner matrix.
- [ ] SOC 2 readiness checklist.
- [ ] GovCloud/CUI/ITAR readiness checklist.

## Acceptance Before Implementation Starts

- [ ] Team agrees the desktop app uses `/v1` APIs only.
- [ ] Team agrees regulated data is blocked from commercial cloud.
- [ ] Team agrees Cloudflare is registrar and authoritative DNS.
- [ ] Team agrees commercial MVP stack.
- [ ] Team agrees future GovCloud migration boundary.
