# Cloud Open Questions

## Business

- What exact production domain should be used: `proxigo.io`, another TLD, or
  both?
- Do we need separate domains for regulated customers?
- What is the first paid plan structure?
- Will map usage billing be based on km2, storage, download count, device count,
  or a bundled plan?
- Will organizations require Enterprise plan only, or can smaller teams create
  organizations?

## Compliance

- Is the first target SOC 2 Type I, SOC 2 Type II, GDPR/CCPA readiness, CMMC,
  FedRAMP, or customer-specific security review?
- When do we expect the first regulated customer?
- What data will be considered CUI, ITAR, or restricted?
- Who owns compliance signoff before data is allowed into commercial cloud?
- What retention period is required for map artifacts, support bundles, and
  audit logs?

## Architecture

- Should `ProxigoCloud` be a new repo or a package inside the website repo?
- Should the commercial API facade live in Vercel API routes or a separate API
  service?
- Should R2 signed URLs be issued by Vercel API routes or Cloudflare Workers?
- Should GovCloud use Lambda or ECS/Fargate for the first API implementation?
- Should the future web app share the website repo or become separate?

## Desktop

- Which OAuth/login flow should the desktop app use first?
- Which keychain library should Tauri use?
- Should users be allowed to switch environments manually?
- Should GovCloud builds be locked to GovCloud endpoints?
- How long can usage events remain queued offline?

## Maps

- Which map providers are approved for commercial use?
- Which map providers are approved for regulated use?
- What is the default map retention policy?
- What is the maximum commercial artifact size?
- What is the maximum regulated artifact size?
- Should cloud maps preserve raw tiles, PMTiles, MBTiles, `satellite.png`, or all
  of them?

## GovCloud

- Who will be the U.S. person root account holder?
- Which AWS support plan will be required?
- Will each regulated customer receive an isolated GovCloud tenant?
- Will customer IdP federation be required in v1?
- Will immutable audit logs with S3 Object Lock be required?
