# Security and release boundaries

## Source publication is not a deployment approval

This repository contains the complete sanitized development-source variant of AstraRelay. Source review and synthetic tests do not certify a production service, desktop installer, safe public charging, or a vulnerability-free dependency tree. No installer or production deployment is released by this source-publication step.

Keep credentials server-side in private process/deployment configuration. Never submit environment files, real prompts/conversations, account records, contact details, verification codes, payment information, private endpoints, or upstream account identifiers. Do not attach secrets to public issues or logs. The optional `public_demo.py` disables integrations; it does not harden or replace the full service for public operation.

## Known dependency evidence — 2026-10-20

The recorded desktop `npm audit` evidence reports **30 affected dependency entries: 25 high, 5 moderate, 0 critical**. This includes the Electron runtime and build-tool dependency chains. These are dependency entries in npm's report, **not 30 independent CVEs**; advisory chains can affect multiple packages, and runtime exposure differs from build-time exposure. A zero critical count is not a claim of safety or absence of exploitable vulnerabilities.

This publication deliberately retains the development dependency versions and lockfile for compatibility; it does not claim those findings have been remediated. A major Electron/build-tool migration requires its own compatibility, security, packaging and manual acceptance work. No exhaustive security audit was performed for this documentation/source-scope change. These counts are dated evidence, not a promise that a later audit will return the same result.

Before desktop binary distribution, triage reachable runtime and build paths, remediate or formally accept remaining risks, rerun regressions and packaging verification, and complete signing/update and manual acceptance review. Install dependencies only in an isolated environment; do not expose secrets to unreviewed dependency scripts.

## Remaining deployment and integration boundaries

- Flask's development server and the included Docker/Compose examples are not a production security baseline. Compose exposes development ports and Docker uses a broad build context; keep private files out of any image build.
- Review HTTPS, session/cookie settings, CSRF, authorization, safe errors/logging, rate limits, attachment limits, data retention/export/deletion, backups and dependency risk before public deployment.
- Existing accounts are not proof of enterprise tenant isolation. Model output and shared history are untrusted data, not authorization for privileged actions.
- Project-root permission on desktop does not authorize Shell, Git, Patch, Skills or publication. Later capabilities require explicit scoped approval and audit.
- Wallet/payment interfaces and local mocks are development foundations, not authorization for real public charging or withdrawals. Separate business and compliance review is required.
- Historical prototype files are preserved for source correspondence, not current runtime support; `ai_adapter_old.py` has a pre-existing syntax defect. The supported Flask entry point is `app.py`, not a legacy script.

## Reporting

Do not post real credentials, sensitive data or directly exploitable details in a public issue. Use a maintainer-provided private reporting channel; if none is listed, request a private contact method first. Provide a redacted minimal reproduction, affected paths/version and impact assumptions. Do not test against someone else's deployment or accounts without authorization.

See the [README](README.md) for isolated setup and the [architecture document](docs/architecture.md) for component and trust boundaries.
