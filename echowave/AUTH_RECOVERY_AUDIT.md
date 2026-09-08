# Authentication audit follow-up — 8 September 2026

This extends the [platform audit and sidebar/billing fixes in PR #123](https://github.com/Stratfiy/echowave-redesign/pull/123). Authentication changes are proposed in [draft PR #122](https://github.com/Stratfiy/echowave-redesign/pull/122). Neither PR is a deployment.

## Findings fixed in code

- Login/signup now normalize structured validation errors and require successful session persistence before redirecting. The original five regression tests remain.
- Local authentication lacked password recovery. New request/reset forms and endpoints issue random 256-bit, 30-minute email links; store only token digests; limit issuance; and atomically consume the challenge while incrementing the session version. A successful reset invalidates existing browser JWTs and preserves MFA and API keys. Google-created accounts can set a password through the same verified inbox, enabling the existing password-plus-MFA login path.
- OAuth state was signed but not bound to the initiating browser. The start route now sets an HttpOnly SameSite cookie, with the `__Host-` prefix on HTTPS deployments. The callback rejects mismatched/missing state cookies before exchanging the authorization code. The UI uses a top-level redirect, and a separate status endpoint checks configuration without minting state.
- New Google and reset links carry credentials in fragments rather than HTTP query URLs, and forms immediately clear them from the address bar. Auth pages do not initialize PostHog/Sentry or inject Chatwoot. This limits credential exposure to third-party URL capture; it is not protection against arbitrary JavaScript compromise.
- Signup now links the existing public Terms and Privacy pages, both verified as HTTP 200. Google sign-in explains its name/email access; Calendar permissions stay separate. This is not a durable caller-recording consent ledger or a marketing opt-in implementation.
- API documentation/client regeneration exposed existing type drift. Agent references, outcome defaults and sandbox API-key payloads now use their generated types without the older temporary casts. No product capabilities were removed.

## Validation and remaining work

Local validation includes 236 frontend tests, TypeScript checking, 23 isolated Python recovery/OAuth service tests and seven real-route HTTP tests with external DB/provisioning dependencies stubbed. Database tests were added for single-use consumption, expiry, rate limits, session revocation and MFA preservation; local PostgreSQL/Docker was unavailable, so their execution must be confirmed in CI. The OpenAPI auth definitions were generated from the changed router in an isolated harness; the existing full-app drift job remains the definitive repository check.

A live Google login still requires Google Cloud OAuth client credentials, branding/audience setup and the exact backend callback URI. A real reset still requires verified SMTP delivery and an owned test inbox. The live app URL alone does not provide that access. No customer emails, calls or payments were initiated in this audit.

Run migration `f4c9b31a82de` before starting the new API. Full instructions, consent-screen URLs and acceptance checks are in [DEPLOY.md](DEPLOY.md#google-sign-in-consent-and-password-recovery). Verify old JWT rejection and simultaneous reset consumption in the database-backed environment before merging/deploying.
