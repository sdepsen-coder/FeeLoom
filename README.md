# FeeLoom

FeeLoom shows marketplace sellers what each sale actually earns after fees, ads, shipping, product costs, and labor.

## Local setup

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py seed_demo
.\.venv\Scripts\python.exe manage.py runserver
```

Demo login: `demo` / `demo12345`

## Etsy order import

In Etsy Shop Manager, open `Settings > Options > Download Data`, choose the
`Orders` CSV type, and upload the downloaded file from FeeLoom's Import page.
The repository includes `sample_data/EtsySoldOrdersSample.csv` for local testing.

## Etsy API connection

Set `ETSY_API_KEY`, `ETSY_SHARED_SECRET`, `ETSY_REDIRECT_URI`, and a long random
`FEELOOM_TOKEN_ENCRYPTION_KEY`. Register the exact redirect URI in Etsy's app settings.

Public beta legal pages are available at `/privacy/` and `/terms/`. Set
`FEELOOM_SUPPORT_EMAIL` to publish a support address on both pages.

Password recovery is enabled automatically when `EMAIL_HOST`, `EMAIL_HOST_USER`,
`EMAIL_HOST_PASSWORD`, and a verified `DEFAULT_FROM_EMAIL` are configured. The
remaining SMTP settings are listed in `.env.example`.

On production, new accounts require email verification whenever email delivery
is configured. Verification links are single-use and activate the workspace on
first use.

Public authentication endpoints use cache-backed fixed-window rate limits for
login failures, signup attempts, password resets, and verification email requests.

Every response includes an `X-Request-ID`, and critical workspace actions appear
on the owner-only Activity page. Set `SENTRY_DSN` to enable optional error
reporting; personal information and performance tracing are disabled by default.
FeeLoom requests read-only `shops_r` and `transactions_r` scopes. Owners and managers
can then connect each shop and run an incremental order sync from the Shops page.

For scheduled syncing, run `python manage.py sync_etsy` from the platform scheduler.

## Backups

Create a consistent local or production database backup with:

```powershell
.\.venv\Scripts\python.exe manage.py backup_database
```

SQLite backups use the database backup API. PostgreSQL backups use `pg_dump` and
are written in its compressed custom format. Set `FEELOOM_BACKUP_DIR` or pass
`--output` to choose the destination. Existing files are never replaced unless
`--overwrite` is supplied.

Keep the production `FEELOOM_TOKEN_ENCRYPTION_KEY` and `DJANGO_SECRET_KEY` in a
separate password manager. A database backup without the original token encryption
key cannot restore Etsy connections. Test PostgreSQL restores with `pg_restore`
before relying on a backup, and never commit backup files or secrets to Git.

## Beta launch check

Run the production readiness report before inviting sellers:

```powershell
.\.venv\Scripts\python.exe manage.py launch_check --production --no-fail
```

The report checks the database, migrations, production security, email delivery,
Etsy credentials, support contact, monitoring, and backup readiness. It reports
configuration state only and never prints secret values. Remove `--no-fail` in a
deployment pipeline when blockers should stop a release.

For the beta email service, Brevo SMTP can be used without application code
changes. Configure `smtp-relay.brevo.com` on port `2525`, the Brevo SMTP login,
an SMTP key, and a verified sender address through the existing email environment
variables. Port `2525` is used because free Render web services block the standard
SMTP ports. A superuser can then open `/system-status/` and send a delivery test;
secret values are never displayed by the page.

For an existing Render Blueprint, add the new secret values manually on the web
service's Environment page. Render only prompts for `sync: false` values when a
Blueprint creates a service for the first time. The non-secret Brevo host, port,
and TLS settings are managed by `render.yaml`.

Free Render services can bootstrap a first administrator without shell access.
Temporarily set `FEELOOM_BOOTSTRAP_ADMIN_ON_START=true` together with the admin
username, email, and a strong password variables listed in `render.yaml`, then
deploy once. After login succeeds, set the switch to `false` and remove the
bootstrap password from Render.

## Render preview

The Blueprint provisions a free web service and free PostgreSQL database. Render
supplies the public hostname automatically, and `/health/` verifies both Django and
the database. Free PostgreSQL is preview-only and expires after 30 days; use a paid
database before storing real seller data.
