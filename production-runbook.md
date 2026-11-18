# Deployment, security and recovery runbook

## Environment separation
| Environment | Identity and data | Database | Purpose |
|---|---|---|---|
| Development / preview | Explicit synthetic fixtures, eight demo roles | Private Unix socket, PostgreSQL 15+ | Functional exploration; no real PII |
| Automated test | Per-test synthetic entities; generated secrets | Random, disposable schemas in a dedicated PostgreSQL instance | Rule tests, concurrency tests, integration and real-browser E2E |
| Production pilot | Named Argon2id accounts, approved institutional datasets, demo disabled | Institution-provisioned private managed PostgreSQL | Controlled SET cohort only after UAT |

The source does not provision a cloud account, purchase a managed database, configure a public domain or claim an independently completed security audit.

## Development and testing
Follow the root README. `bash scripts/start-preview.sh` uses a non-root OS user, local peer authentication and no TCP database listener. It will not seed over an existing installation. Rebuild frontend changes using `npm run build`. For iterative frontend development, run the esbuild command with `--watch` under a process supervisor. FastAPI serves the resulting static bundle and same-origin API.

For testing, set `TEST_DATABASE_URL` to a **dedicated disposable** database whose test account can create/drop schemas (not production credentials). Install `requirements-dev.txt` and Chromium with system libraries. Run `python -m pytest tests -v --junitxml=docs/test-results.xml`. Tests use separate connections for the last-seat competition and a separate real HTTP server/schema for Chromium. The untouched institutional database is not a test fixture.

## Vercel option
For a Vercel-hosted FastAPI function and prebuilt UI, follow the separate [Vercel quickstart](../START-HERE.md). The container commands below are an **alternative host**, not a Vercel Build/Start Command. Vercel deployments use a pooled TLS database credential, a separate direct migration job, an external maintenance scheduler, and managed backups. The Vercel-specific app upload cap is 4 MiB before multipart overhead.

## Managed production deployment
1. Create a private, encrypted PostgreSQL 15+ database with encrypted snapshots, provider PITR, restricted networks and a pinned CA certificate. Design separate owner/migration, runtime and backup identities. The runtime account must not own the schema, grant privileges, create extensions or bypass the audit trigger. Establish institution-approved RPO/RTO and retention periods rather than treating fixture values as a retention policy.
2. Inject the **migration** DSN from a secrets manager. Use verified TLS (`sslmode=verify-full` plus the managed service CA). Run `python scripts/migrate.py` with a migration account. Each migration is versioned, transactional and protected by an advisory lock. Review and back up before any upgrade. Do not modify applied migration files in a deployed instance.
3. Grant the app identity `USAGE` on its schema, required table `SELECT/INSERT/UPDATE/DELETE` and sequence usage. Narrow these to its workflow requirements. In particular, grant log read/insert, **revoke audit UPDATE/DELETE/TRUNCATE**, deny schema changes, and do not grant ordinary write access to migration history. The append-only trigger is defense in depth, not protection against a database owner.
4. Bootstrap one named administrator: `python scripts/bootstrap-admin.py` prompts securely. Alternatively inject `BOOTSTRAP_USERNAME` and `BOOTSTRAP_PASSWORD` for a one-shot job, then remove both secrets. No default password or administrator account is created by production migrations.
5. Build: `docker build -t dsu-cfcs:1.0 .`. The multi-stage image builds React, uses a non-root runtime, and bundles local fonts, scripts and offline Swagger UI. `compose.yaml` expects **existing managed PostgreSQL**, not a publicly exposed database container. Supply `DATABASE_URL_SECRET_FILE` and `DATABASE_CA_FILE` from mounted secrets and run `docker compose up -d`. The app service binds to the host loopback for the reverse proxy. The provided files are deployment configuration, not a claim that an external image registry or managed host has been deployed.
6. Put an institutional HTTPS reverse proxy in front. Set HSTS after TLS validation, cap upload bodies at 6 MB including multipart overhead, configure trusted upstream proxy addresses and per-IP/per-account rate limits. Keep service port 8000 private. The Arena preview permits embedding; production should set `Content-Security-Policy: frame-ancestors 'none'` (or the exact authorised portal origin) at the edge. Never expose wildcard credentialed CORS.
7. `APP_MODE=production` disables demo authentication. Startup refuses to relabel a known synthetic database as institutional data. Configure a real academic year and independently approved master values via the administrator workflow. Accounts must have the correct role and object scope before enabling access.
8. Schedule `python -m backend.analytics` to refresh reporting snapshots. Multiple workers can coexist; cohort rebuilds use a separate advisory lock. Production housekeeping can be enabled with `ENABLE_MAINTENANCE=1` for a single maintenance worker; use one scheduled job rather than one job per web worker for larger deployments. Purge expired sessions and expired two-hour import payloads regularly. Do not store real database backups in a publicly served or shared workspace directory.
9. Perform UAT with a small authorised cohort, reconcile credits, test debarment and verified repeat attempts, rehearse roster and reporting access, and record Registrar/Dean sign-off before cutover.

### Configuration
- `APP_MODE`: `production` (default) or explicit `synthetic`.
- `DATABASE_URL`: secret DSN; use `DATABASE_URL_FILE` in the production launcher to read a mounted secret instead.
- `PGSSLMODE`, `PGSSLROOTCERT`: certificate-verified database transport.
- `PORT`, `WEB_WORKERS`: server port and worker count. Increase only after a managed-DB connection and load test.
- `TEST_DATABASE_URL`: separate disposable test database.
- `DSU_PREVIEW_BACKUP`: `1` enables the local synthetic recovery snapshot. A guard restricts this snapshot to the known private preview socket/database.
- `ENABLE_MAINTENANCE`: optional production maintenance task. Use platform scheduling for managed backup, retention, monitoring and alerts.

## Security controls implemented
- Argon2id password hashing; opaque random session tokens; only SHA-256 token digests stored server-side; expiry, active-account checks and explicit revocation.
- Browser token remains in memory, never a URL, HTML data attribute or local-storage credential. Browser authentication uses the explicit `X-CFCS-Session` header, not ambient cookies. The API also accepts `Authorization: Bearer` for existing clients. The proxy must forward the application session header without logging its value. Both transports validate the same hashed, expiring, revocable session. Cross-origin form submissions do not acquire a session credential; no cookie-authenticated state-changing endpoints exist. If cookie authentication is added later, add origin checks, appropriate SameSite controls and anti-CSRF tokens.
- Database-backed login throttling across workers, anti-abuse limit on public synthetic sessions, generic invalid sign-in messages. An edge rate limiter is still required for internet-facing traffic.
- Server-enforced role, school, department, advisor, instructor and student scope. Ordinary registration cannot edit a regulation or override results. Mandatory prerequisites cannot be waived by a general request.
- Bound SQL values; dynamic identifiers drawn from a fixed table/field catalogue; Pydantic request validation; safe database-constraint errors instead of SQL traces.
- Size/record/column/archive bounds on imports; no macro execution, formula evaluation, external workbook links or public upload paths. Authenticated owner-bound staging; revalidation on confirmed commit; filenames are display metadata, not filesystem paths.
- Transactional row locks plus a common allocation mutex and partial unique constraints prevent overbooking and duplicates. School-scale serialisation favours correctness. Do not claim university-wide throughput without an institutional load test.
- Append-only academic audit records, independent decision checks, soft archival safeguards, separate admission and teaching mappings and per-attempt history.
- Local assets; content policy, `nosniff`, referrer and permissions headers; no caching of API response bodies. No telemetry is sent to external analytics services.

## Backup and recovery
**Managed production:** enable provider PITR, encrypted snapshots, independent protected backup storage, rotation, access audit and recovery drills. Provider-specific storage, retention, keys and failover must be configured by authorised IT staff. A local dump does not replace these controls.

**Logical backup:** pass PostgreSQL connection variables and a protected `PGPASSFILE` at runtime; never place passwords in shell history or source. Run:
```sh
scripts/backup.sh /approved/encrypted/backup-location
```
This creates a custom-format dump, checks its archive, sets a restrictive umask and writes a SHA-256 checksum. Sessions and login-attempt records are excluded. Store the output on institution-approved encrypted storage; `pg_dump` itself does not encrypt its output.

**Recovery rehearsal:** provision a separate empty recovery database. Verify the expected target, then:
```sh
export PGDATABASE=cfcs_recovery_rehearsal
export RESTORE_CONFIRM=restore-to-empty-target
scripts/restore.sh /approved/encrypted/backup-location/selected-backup.dump
```
The restore script refuses a nonempty target, verifies a supplied checksum and restores in a single transaction. It does not use `--clean`. Compare student, enrolment, attempt, earned-credit, section occupancy and audit counts, check foreign-key integrity and run read-only smoke tests. Do not switch traffic until the recovery authority approves. All users must obtain new sessions after recovery.

**Executed rehearsal in this workspace:** a fresh synthetic backup was checksum verified and restored into a separate PostgreSQL database. All four schema versions were retained, 1,248 students reconciled, enrolment and result counts matched the source, and no authenticated sessions were restored. The rehearsal database was then removed. This validates the local logical recovery procedure, not a cloud provider's RPO/RTO.

## Release checklist / not yet externally verified
- Institution supplies and approves real curriculum, equivalence, attendance, workload, exception and credit policies.
- Confirm appropriate lawful basis, access retention, evidence-document handling and DSU privacy procedures. No regulatory compliance certification is asserted.
- Run independent security review and academic UAT; test accessibility with actual users and assistive technology.
- Test expected registration-window concurrency, query plans, database connection limits, failover and monitoring under institutional peak load.
- Integrate a university identity provider/MFA, attendance SIS or examination feed if required. The delivered integration surface is authenticated API plus validated file import, not a claimed existing connection.
- Maintain a rollback plan, reconciliation signatures and a named operational owner. Do not repurpose the public synthetic role selector for actual records.
