# Provider Cost And Stack Comparison

## Purpose

Compare the cloud providers and paid services relevant to the Proxigo website,
desktop app, map artifacts, future web app, and GovCloud migration.

Prices change. Verify every number with the linked official pricing pages before
procurement, investor planning, or customer quoting.

## Recommended Default

Use the hybrid current stack first:

- Cloudflare Registrar and DNS.
- Vercel Pro for website/dashboard/API facade.
- Supabase Pro for auth and relational metadata.
- Resend Free or Pro for transactional email.
- Stripe for billing.
- Cloudflare R2 for non-regulated map artifacts.

Expected early commercial baseline:

```text
Vercel Pro:        about $20/month plus usage and seats
Supabase Pro:      about $25/month plus usage
Cloudflare Workers: starts around $5/month if Workers Paid is needed
Cloudflare R2:     storage/operations based, no egress fees
Resend:            free tier first, Pro around $20/month when needed
Stripe:            transaction fees
Mapbox/Bing:       optional paid map sources if needed
```

Practical early range:

```text
$50-$150/month before paid map providers, high traffic, or heavy artifact use
```

## Comparison Table

| Provider | Use | Strength | Risk |
| --- | --- | --- | --- |
| Cloudflare Registrar/DNS | Domains, DNSSEC, WAF, R2, Workers | Strong domain/security posture and low artifact egress cost | Regulated data should not use commercial Cloudflare without approval |
| Vercel | Website, dashboard, commercial API facade | Fast Next.js deployment and preview workflow | Function and bandwidth usage can grow; not GovCloud |
| Supabase | Auth, Postgres, RLS, commercial metadata | Fast product iteration and Postgres-first model | Direct dependency must be hidden behind `/v1` contract for migration |
| Resend | Transactional email | Simple email integration | Do not send regulated data or secrets in email |
| Stripe | Billing | Mature checkout, portal, webhooks | Billing events must be audited and reconciled |
| Cloudflare R2 | Non-regulated map artifacts | No egress fees, S3-compatible API | Not GovCloud |
| AWS Commercial | Enterprise-grade cloud | Migration rehearsal and AWS-native patterns | More DevOps overhead than current stack |
| AWS GovCloud | Regulated workloads | FedRAMP High baseline support, ITAR/CUI path | Eligibility, cost, operational complexity |
| Mapbox/Bing | Optional premium imagery | Higher-quality commercial map sources | Usage-based cost and provider terms |
| Esri/USGS/OpenFreeMap | Free or low-friction map sources | Useful for default/free-first strategy | Coverage, licensing, and resolution vary |

## Commercial MVP Cost Notes

Vercel:

- Good fit for public website, dashboard, and API facade.
- Pro starts around $20/month and includes usage credit.
- Watch seats, bandwidth, serverless/edge usage, and image optimization.

Supabase:

- Good fit for auth, Postgres, RLS, and early org/account models.
- Pro is the likely first paid production plan.
- Watch database size, egress, storage, backups, and log retention.

Cloudflare:

- Registrar is at-cost/no-markup.
- Workers Paid starts around $5/month.
- R2 charges for storage and operations and has no egress fees.
- Strong fit for map artifact cost control.

Resend:

- Free tier can cover early transactional email.
- Pro starts around $20/month for larger send volume.

Stripe:

- No monthly platform fee for basic usage.
- Costs are per transaction and can include extra fees depending on payment
  method, country, disputes, tax, and subscriptions.

Map providers:

- USGS and Esri are useful free-first sources but must be checked for terms,
  reliability, and coverage.
- Mapbox and Bing are optional paid sources for higher-quality or more
  predictable imagery.

## GovCloud Cost Notes

Early GovCloud development environment:

```text
$150-$500/month is a reasonable planning range
```

Production regulated environment:

```text
$1,000+/month is plausible once multi-AZ database, logging, monitoring,
backups, WAF, support, and security services are enabled
```

Primary cost drivers:

- RDS PostgreSQL size and multi-AZ.
- NAT gateway and data transfer.
- S3 storage and requests.
- CloudWatch logs and metrics.
- GuardDuty/Security Hub/Config.
- Support plan.
- WAF/API Gateway/Lambda/ECS usage.

Use AWS Pricing Calculator before committing to a design.

## Source Links

- Vercel pricing: https://vercel.com/pricing
- Vercel Pro plan: https://vercel.com/docs/plans/pro-plan
- Supabase pricing: https://supabase.com/pricing
- Cloudflare Registrar: https://www.cloudflare.com/products/registrar/
- Cloudflare Workers pricing: https://developers.cloudflare.com/workers/platform/pricing/
- Cloudflare R2 pricing: https://developers.cloudflare.com/r2/pricing/
- Resend pricing: https://resend.com/pricing
- AWS GovCloud: https://aws.amazon.com/govcloud-us/
- AWS GovCloud compliance: https://docs.aws.amazon.com/govcloud-us/latest/UserGuide/govcloud-compliance.html
- AWS Lambda pricing: https://aws.amazon.com/lambda/pricing/
- AWS S3 pricing: https://aws.amazon.com/s3/pricing/
- Mapbox pricing: https://www.mapbox.com/pricing
