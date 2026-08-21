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
FeeLoom requests read-only `shops_r` and `transactions_r` scopes. Owners and managers
can then connect each shop and run an incremental order sync from the Shops page.

For scheduled syncing, run `python manage.py sync_etsy` from the platform scheduler.

## Render preview

The Blueprint provisions a free web service and free PostgreSQL database. Render
supplies the public hostname automatically, and `/health/` verifies both Django and
the database. Free PostgreSQL is preview-only and expires after 30 days; use a paid
database before storing real seller data.
