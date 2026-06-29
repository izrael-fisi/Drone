# Cloud + GovCloud Readiness

## Purpose

Cloud integration and GovCloud readiness docs for Proxigo website, desktop app,
map usage, and future web app.

## Current Decision

Use a hybrid-first architecture for prototype and development, but keep every
cloud-facing workflow GovCloud-ready.

Default commercial stack:

- Cloudflare Registrar and DNS
- Vercel for the public website, dashboard, and commercial API facade
- Supabase for commercial auth, Postgres metadata, and early org/user flows
- Resend for transactional email
- Stripe for billing
- Cloudflare R2 for non-regulated map artifacts and release assets

Future regulated stack:

- AWS GovCloud API
- RDS PostgreSQL
- S3 GovCloud
- KMS customer-managed keys
- CloudTrail
- GuardDuty
- Security Hub
- AWS Config

## Key Rule

The desktop app must call provider-neutral `/v1` APIs. It must not depend on
direct Supabase tables, Supabase Storage URLs, Vercel internals, Cloudflare R2
bucket details, or future AWS implementation details.

## Do Not Store Regulated Data In Commercial Cloud

Until a GovCloud environment is deployed and approved, do not store CUI, ITAR,
restricted mission data, regulated customer map cuts, or regulated telemetry in:

- Vercel
- Supabase commercial
- Resend
- Cloudflare R2
- Cloudflare CDN
- Other commercial SaaS systems

Commercial cloud is acceptable for public marketing, normal prototype data,
non-regulated development data, billing metadata, support requests, and
non-regulated customer usage.

## Implementation Status

Planning scaffold only. No cloud implementation has been performed.

## Document Index

- [Hybrid To GovCloud Roadmap](hybrid-to-govcloud-roadmap.md)
- [Cloudflare Domain Runbook](cloudflare-domain-runbook.md)
- [Cloud API Contract](api-contract.md)
- [Map Artifact And Usage Model](map-artifact-and-usage-model.md)
- [Security And Compliance Readiness](security-and-compliance-readiness.md)
- [Provider Cost And Stack Comparison](provider-cost-and-stack-comparison.md)
- [GovCloud Preparation](govcloud-preparation.md)
- [Team Implementation Checklist](team-implementation-checklist.md)
- [Open Questions](open-questions.md)

## Working Model

Prototype quickly on the commercial stack, but preserve the ability to run the
same desktop workflows against GovCloud later:

```text
desktop app
  -> /v1 cloud API contract
    -> commercial implementation now
    -> GovCloud implementation later
```

The API contract, data classification model, audit events, usage events, and map
artifact manifests are the migration boundary.
