# Deploy DSU–CFCS on Vercel

**Goal:** Open the full academic workspace at a Vercel HTTPS address, including sign-in, the dashboard and a persistent PostgreSQL database. The quickest route below is for a **disposable synthetic demo**. Anyone with access to that demo can choose a demo role. Do not enter real student, staff or examination records.

**What to download:** `DSU-CFCS-Vercel.zip`. Extract it first. The `web/` directory contains the complete deployable FastAPI app, compiled React dashboard, assets and Python 3.13 configuration. `setup/` contains a separate, one-time synthetic SQL installer and blank import templates. The SQL installer was generated from the sample data generator in a fresh database, not copied from users of the running preview. No database URL, named password, live session, vendor account or public deployment is included.

## 1 · Make a database for the demonstration

1. Open the [Neon Console](https://console.neon.tech/) and create a **new, disposable PostgreSQL 17 project** (for example, `dsu-cfcs-demo`). The default empty database is fine. Pick a permitted region close to your Vercel function; review any plan or billing prompts yourself. Do not use a database that contains other applications or university records.
2. In that project, select **Postgres database → SQL Editor**. Check the selected branch and database. On your computer open `setup/DEMO-SETUP.sql` in a text editor; copy the **whole file** into a new query. Click **Run** once. There is no need to install Python or to run the migrations separately on this route.
3. The final result should show **1,248 synthetic students, 96 teaching sections, 8 demo accounts**. Setup runs as one guarded database operation and refuses an existing application schema. Do **not** clear or drop an existing database to make it pass. If the editor warns that query *history* will be truncated, that does not truncate the query you submit. If your browser cannot handle the file, use the terminal fallback below instead.
4. Click **Connect**. Select the same branch and database, select the intended application login and turn **Connection pooling** on. Copy the full PostgreSQL URL, with its TLS query parameters. It is a **password-bearing secret**, not a browser URL. Keep it out of source, chat, screenshots and client-side variables. For a restricted application login, see “Database identities” below; never use an owner credential in an institutional deployment.

**Optional terminal fallback (same demo setup, not an additional step):** From the extracted ZIP directory, using the `psql` PostgreSQL client, connect as the provisioning user to the **direct** host with pooling off. This example prompts for the password rather than placing it in history. Set the host, database name and user to the ones shown by your provider; keep the TLS settings it requires.

```sh
psql -X -W -v ON_ERROR_STOP=1 "host=YOUR_DIRECT_HOST dbname=YOUR_DEMO_DATABASE user=YOUR_SETUP_USER sslmode=require" -f setup/DEMO-SETUP.sql
```

## 2 · Publish just the `web/` directory

1. Create a **private Git repository** in your own Git account. Copy or commit the **contents of `web/`** at its top level, including its dotfiles. You should see `app.py`, `pyproject.toml`, `requirements.txt`, `vercel.json`, `backend/` and `frontend/` there. Do not upload the ZIP itself, `setup/` or an `.env` file. GitHub Desktop's **Publish repository → Keep this code private** is one option; the **Add file → Upload files** web flow also works for this small bundle if the folder structure is retained.
2. Visit [Vercel New Project](https://vercel.com/new) in the Vercel team you control. Select **Import Git Repository** and grant access only to the intended repository. If the repository is not visible, check the selected Git organisation and app permissions.
3. Use **Framework Preset: FastAPI** and **Root Directory: `.`** when the repo was created as above. If you deliberately kept `web/` as a subfolder, use `web` as the root instead. The rule is: choose the directory containing **both `app.py` and `vercel.json`**. Never choose `frontend/` or `backend/`. Leave **Install Command, Build Command and Output Directory** at their defaults; this release already contains the compiled frontend and its dependencies are Python pins. Do not enter `npm run build`, `uvicorn`, a Docker command or `start-preview.sh`.
4. Before clicking **Deploy**, expand **Environment Variables**. Set these in the **Production** target of this **demo-only Vercel project**:

   | Name | Value |
   |---|---|
   | `DATABASE_URL` | The full **pooled TLS URL** for the *demo* database; store as a secret/sensitive value. |
   | `APP_MODE` | `synthetic` (lowercase, without quotes). |

   “Production” in Vercel means the target behind this project's main domain; it does **not** turn the sample data into institutional data. Scope the values to the intended environment. Preview deployments must use a separate synthetic database or branch and their own variables; never point them at an institutional production database. Do not prefix the secret with `VITE_`, `NEXT_PUBLIC_` or another browser-visible name.
5. Click **Deploy**. Open the HTTPS address Vercel actually assigns to your project. If you added or edited environment variables after building, **redeploy** the intended target so it receives them.

### Alternative: Vercel CLI, no Git repository

After the database is ready, open a terminal in the extracted `web/` directory. Use a current Node.js version supported by the current Vercel CLI. These commands use your authenticated account; no token is required in source files. Link/create the **demo** project before the first deploy so the environment is set correctly. Review team, root (`.`), plan and permissions in the prompts.

```sh
npx vercel@latest login
npx vercel@latest link
npx vercel@latest env add DATABASE_URL --environment production --sensitive
npx vercel@latest env add APP_MODE --environment production
npx vercel@latest deploy --prod
```

Paste the pooled URL **at the variable prompt**, not into a shell command with `--value`; for `APP_MODE` enter `synthetic`. The dashboard and CLI paths are alternatives. Only deploy once.

## 3 · Verify the published pilot

- Append `/api/health` to the assigned URL. Expected: `{"status":"online","database":true,"mode":"synthetic"}`. This proves a database query succeeded; it is not by itself a login or data test.
- Open `/`. The demo should open as **Dean – SET**, or offer **Explore the Dean workspace**. No username/password is required. Open **Student records** and check that the initial, unfiltered data includes 1,248 synthetic students. A direct unauthenticated `/api/students` request should return `401`, not a roster. `/docs` serves the API reference.
- Do not upload real records. Add deployment access protection if only invited reviewers should see the demo. A private source repository does not in itself restrict visitors to the website.

## First-deploy troubleshooting

| Symptom | What to check |
|---|---|
| Build wants `dist/`, Next.js or Vite; `/api/health` is HTML or 404 | Select **FastAPI** and the folder containing `app.py` and `vercel.json`; remove build/output overrides. Redeploy. |
| Function fails; logs mention `DATABASE_URL`, a database connection, or `workspace_metadata` | Check scope and spelling of both variables; confirm TLS, role rights, correct branch and successful SQL setup. Use **Runtime Logs**, not just a successful build. Redeploy after changes. Do not post an unredacted URL or token from logs. |
| “Synthetic preview requires…” / demo sign-in disabled | Mode and dataset do not match. Use `synthetic` with the initialized sample DB; use `production` with a separate clean DB and named accounts. Never change the dataset marker by hand. |
| It works on the main URL but not on a branch preview | The Preview target needs its **own** synthetic database and `APP_MODE`; redeploy that preview. |
| Upload or export returns `413` | This release caps each Vercel import file at **4 MiB / 2,000 rows** to leave multipart headroom under Vercel's 4.5 MB request/non-streamed response ceiling. Split imports or scope exports. Large institutional exports need protected object storage or a separate worker before rollout. |
| Slow reports or stale dated snapshots | Keep function/DB regions close, use the pooled runtime URL, review slow queries and run maintenance outside the web function. A reporting snapshot never grants registration eligibility. |

## A real university deployment is a separate installation

Do **not** edit `APP_MODE` on the demo project and start uploading official files. Use a separate authorised Vercel project/team and a **new clean managed database**, institution-approved regions, privacy and retention controls, least-privilege credentials and managed backup/PITR. Rehearse a restore and complete academic/security UAT. Select providers and plans suited to institutional use and expected registration load.

1. From a trusted admin machine or one-off release job, enter `web/`, create a Python 3.13 virtual environment and install `python -m pip install -r requirements.txt`. Set `APP_MODE=production` and inject the **direct migration-owner** `DATABASE_URL` through your secret manager, with certificate/host-verified TLS. For a Bash session, you can use `read -rsp 'Direct migration URL: ' DATABASE_URL; echo; export DATABASE_URL` so the value is not in shell history.
2. Run `python scripts/migrate.py` and then `python scripts/bootstrap-admin.py`. The latter prompts for a named account and password. Never run `DEMO-SETUP.sql` or `backend.seed --demo` on this database. Do not put bootstrap credentials or the migration-owner URL in the deployed app. Remove them from the operator environment afterwards.
3. Provision the restricted runtime login described below. In Vercel **Production**, set `APP_MODE=production` and `DATABASE_URL` to its **pooled production TLS URL**. Keep Preview/Development credentials and databases separate. Re-deploy, check `/api/health` shows `production`, sign in as the named admin, and verify **Workspace settings**. The demo endpoint must return `403`. Configure an institution-approved current academic year and master data before expecting dashboard or registration results. There is no default institutional password, regulation or credit policy.
4. Use a trusted **external scheduled job** with the intended `DATABASE_URL` and explicit `APP_MODE` to run `python scripts/maintenance-once.py`. It clears expired sessions/staged import payloads and refreshes eligibility snapshots; schedule it per the approved operating/retention policy. Do **not** configure this as a Vercel Build or Start command. On Vercel the app does not start a perpetual worker or create local backups. Enable provider-managed backups, monitoring, network controls and login rate limiting. For production release gates, see `setup/production-runbook.md` (in the full source: `docs/03-deployment-and-recovery.md`).

### Database identities

The *provisioning* role owns the DSU schema and runs migrations. The *web app* must not own it, bypass auditing, or be able to modify migration history, and must not inherit a powerful provider role. In a dedicated DB, have the DBA provision a new ordinary login `dsu_cfcs_runtime` (for example `CREATE ROLE dsu_cfcs_runtime LOGIN;` as the role administrator, then `\password dsu_cfcs_runtime` in `psql` to set its secret privately). As the schema owner, review and apply `setup/runtime-grants.sql` after the four migrations; supply database CONNECT if your organisation revoked it from PUBLIC. This script only grants explicit application-object access; it is intended for a **new** non-owner/non-member login, not to repair a previously privileged one. Verify there are no additional public/default grants in the target. Review again on schema upgrades. Put **only the pooled runtime role URL** into Vercel. Use separate identities for backups/administration.

### If you have the full source release instead

In `DSU-CFCS-Pilot.zip`, from the extracted `DSU-CFCS/` source root, run `python scripts/make-vercel-web.py path/to/new-web` with Python 3.13. It copies only the release's deployable files into a new folder; use that folder as the repository root. If the UI source changed, run `npm ci` and `npm run build` first. This source path does not contain a database copy. As an alternative to the standalone SQL installer, on a new, empty **dedicated synthetic** DB set `APP_MODE=synthetic` and a **direct** `DATABASE_URL`; from the generated web directory with its Python requirements installed, run `python scripts/migrate.py`, `python -m backend.seed --demo`, then `python -m backend.analytics` once. Run these on a trusted machine close to the DB (the Python seed makes many round trips), not on every deployment or function request. Do not use both initialization methods. The source version of the role grants is `deploy/vercel/runtime-grants.sql`.

### Official references

- Vercel FastAPI and supported entrypoints: [1](https://vercel.com/docs/frameworks/backend/fastapi)
- Vercel project import: [1](https://vercel.com/docs/projects/managing-projects); environments: <https://vercel.com/docs/deployments/environments>
- Neon SQL Editor: [1](https://neon.com/docs/get-started/query-with-neon-sql-editor); pooled vs direct URLs: [4](https://neon.com/faqs/find-database-connection-string)
- Vercel Function limits: [1](https://vercel.com/docs/functions/limitations)

**Verification boundary:** The packaged runtime, both database initialization paths (synthetic and clean named-account), assets, protected routes and a real Excel template were checked locally against PostgreSQL 17. This is a deployment handoff, not a claim that Vercel has built or hosted your project, that a Neon account has been provisioned, or that institutional processing is approved.
