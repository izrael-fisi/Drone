# Global View release with Supabase Auth

## Development and release

Use `npm --prefix desktop-app run dev:global-view` for day-to-day development at `http://127.0.0.1:1420`. It opens the synthetic workspace immediately, with local browser storage and no Supabase connection or MFA prompt. Keep development gateway data and credentials separate from customer systems. Use the existing loopback Supabase pilot for authentication testing with fixture sites.

When ready for production, provision a dedicated Supabase project for the production environment, use a private copy of `portal.example.json`, and keep `production` set to `true`. Give production its own HTTPS origin, provider registrations, portal database, gateway state, and proxy credentials. Use a TLS reverse proxy for the configured origin and keep the portal listener and gateways private. Keep database credentials, OAuth client secrets, operator passwords, TLS keys, and gateway proxy tokens outside the repository. The portal uses the publishable key and verifies access tokens against the project's public signing keys.

From the repository root, build and serve the release through the enterprise portal. Install the dependencies in `services/camera-gateway/enterprise-requirements.txt` in the portal's Python environment first.

```bash
npm --prefix desktop-app run build:global-view:production
python services/camera-gateway/enterprise_portal.py \
  --config /private/proxigo/production/portal.json \
  --frontend desktop-app/dist-global-view-production \
  --state /private/proxigo/production/portal.sqlite3
```

The release opens at enterprise sign-in. The portal rejects a non-loopback origin unless production mode and the release frontend are configured. To run the local Chrome sign-in fixtures against the release build, set `PROXIGO_ENTERPRISE_FRONTEND` to the absolute path of `desktop-app/dist-global-view-production` and use the [enterprise fixture command](../../docs/global-view-monitoring.md#enterprise-sign-in). Rehearse the release with the production identity providers and complete the Chrome checks below before granting customer access.

## Configure and verify authentication

1. In Supabase Authentication, enable email/password, email confirmation, and TOTP enrollment and verification. In JWT Keys, use an asymmetric ES256 or RS256 signing key. For pilot email/password testing, an administrator can create synthetic confirmed test users under Authentication → Users. Configure a transactional email sender before inviting real customers or relying on password recovery. [Signing keys](https://supabase.com/docs/guides/auth/signing-keys) · [TOTP](https://supabase.com/docs/guides/auth/auth-mfa/totp) · [SMTP](https://supabase.com/docs/guides/auth/auth-smtp)
2. Set Supabase Authentication → URL Configuration → Site URL to the HTTPS portal origin. Add `https://PORTAL_HOST/api/enterprise/supabase/callback/ORG_SLUG` as an exact allowed redirect for each organization. For loopback testing, configure the test project's redirects for its local portal origin, such as `https://localhost:8776`. [Redirect URLs](https://supabase.com/docs/guides/auth/redirect-urls)
3. Create a Google web OAuth client and a dedicated Microsoft Entra multitenant web app for customer work accounts. Use `https://PROJECT_REF.supabase.co/auth/v1/callback` as the provider callback for both. Configure provider credentials in the Supabase dashboard. Include the `xms_edov` and `email` ID token claims for Entra and leave support for unverified email domains disabled. Add Google test accounts to the OAuth test audience while the consent screen is in Testing. List enabled providers in `supabaseAuth.methods`. [Google](https://supabase.com/docs/guides/auth/social-sign-in/auth-google) · [Microsoft Entra](https://supabase.com/docs/guides/auth/social-sign-in/auth-azure)
4. Enroll each operator by their Supabase Auth user UUID from Authentication → Users. Use it as the site gateway account subject and grant the required camera scope. Match `directoryTenant` to the gateway's `organization.directoryTenant` and use a distinct binding for each customer. A first social sign-in can create an Auth user if Supabase sign-ups are enabled. That user gains site access only after a supervisor enrolls the UUID and the operator completes MFA. Enable `--require-camera-session` on each gateway. Existing gateway state must match its configured directory binding when migrating a site.
5. In Chrome, test each sign-in method, including first-time MFA enrollment and repeat sign-in. Before MFA, the session API should return `401`. After MFA, log in as operators from two organizations in separate browser profiles. Confirm each sees only their own sites and assigned cameras. Requests for another organization's site or stream should return `404`, and a camera outside the operator's scope at their own site should return `403`. Exercise sign-out and access removal.

The Supabase Data API is optional. Global View uses the Auth API and reads camera and site data from its gateways. If a future feature needs Postgres REST or GraphQL access, enable the Data API in the project's Data API integration settings and grant access only to the required schemas and tables using grants and Row Level Security. [Data API settings](https://supabase.com/docs/guides/api/securing-your-api)
