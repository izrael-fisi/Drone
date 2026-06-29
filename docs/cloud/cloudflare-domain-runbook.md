# Cloudflare Domain Runbook

## Purpose

Use Cloudflare as registrar and authoritative DNS for the commercial Proxigo
domains while keeping future GovCloud endpoints clearly separated.

Cloudflare Registrar provides at-cost domain registration and renewal, DNSSEC,
and registrar security features. Verify pricing and TLD support before purchase
or transfer.

## Domains

Recommended:

- `proxigo.io` for production.
- `proxigo.dev` or `proxigo.app` for engineering and staging if desired.

Reserve but do not immediately use:

- `gov.proxigo.io`
- `gov-api.proxigo.io`

These records should remain placeholders until a GovCloud endpoint exists.

## Cloudflare Account Controls

Required before production DNS is moved:

- Enforce MFA or passkeys for every Cloudflare user.
- Use role-based access.
- Keep billing/admin DNS access limited to founders and DevOps owner.
- Enable registrar lock where available.
- Enable DNSSEC.
- Document every production DNS change.
- Keep emergency registrar/DNS recovery contacts current.

## DNS Records

Initial commercial records:

| Name | Target | Purpose |
| --- | --- | --- |
| `proxigo.io` | Vercel production | Website root |
| `www.proxigo.io` | Vercel production | Website alias |
| `app.proxigo.io` | Vercel or future web app | Commercial app dashboard |
| `api.proxigo.io` | Vercel API or commercial API app | Provider-neutral API facade |
| `maps.proxigo.io` | Cloudflare Worker/R2 route | Non-regulated map artifacts |
| `downloads.proxigo.io` | Cloudflare Worker/R2 route | Desktop releases and artifacts |
| `staging.proxigo.io` | Vercel staging | Staging website |
| `staging-api.proxigo.io` | Staging API | Staging API facade |
| `gov-api.proxigo.io` | Placeholder | Future GovCloud API |

Do not use proxied Cloudflare routes for regulated data unless compliance review
explicitly approves that architecture.

## DNSSEC

Enable DNSSEC after Cloudflare is authoritative for the zone. If transferring an
existing domain, ensure old-provider DNSSEC state is handled correctly before
changing nameservers.

## Suggested Environments

Commercial production:

- `proxigo.io`
- `app.proxigo.io`
- `api.proxigo.io`
- `maps.proxigo.io`
- `downloads.proxigo.io`

Commercial staging:

- `staging.proxigo.io`
- `staging-api.proxigo.io`
- `staging-maps.proxigo.io`

Future GovCloud:

- `gov-api.proxigo.io`
- `gov-app.proxigo.io` if a regulated portal is needed

## Change Process

For every DNS change:

1. Record requester, reason, record name, old value, new value, and TTL.
2. Apply change in Cloudflare.
3. Verify DNS resolution.
4. Verify TLS certificate issuance.
5. Log completion in the deployment notes.

## Source Links

- Cloudflare Registrar: https://www.cloudflare.com/products/registrar/
- Cloudflare Registrar docs: https://developers.cloudflare.com/registrar/
- DNSSEC setup: https://developers.cloudflare.com/registrar/get-started/enable-dnssec/
- Cloudflare Workers pricing: https://developers.cloudflare.com/workers/platform/pricing/
- Cloudflare R2 pricing: https://developers.cloudflare.com/r2/pricing/
