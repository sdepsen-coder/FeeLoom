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
