---

# FEELOOM — Master Build Guide

## What FeeLoom Is

FeeLoom is a profit-intelligence SaaS for Etsy sellers. It pulls together sales,
fees, ads, refunds, shipping, and product costs across multiple shops so sellers
see true per-order net profit and margin — not just revenue.

The app is server-rendered Django with vanilla JS and a single hand-written CSS
file. There is no frontend framework, no build step, no Node.js dependency.

## Tech Stack

- **Django 5.2** (Python 3.12+) — server-side rendering, templates, ORM
- **PostgreSQL** in production (Render), **SQLite** for local dev and tests
- **Gunicorn** + **Whitenoise** for production serving
- **cryptography** (Fernet) for encrypting Etsy OAuth tokens at rest
- **sentry-sdk** for optional error monitoring
- **dj-database-url** for database URL parsing
- **No JS framework** — small inline `<script>` blocks in `base.html`
- **No CSS preprocessor** — single `dashboard/static/app.css` file

## Project Structure

```
config/          Django project settings, root URLs, middleware, error pages
accounts/        Signup, login, email verification, password reset, beta invites
workspaces/      Workspace, Membership, Shop models + workspace/shop selectors
sales/           Order, OrderItem, FeeLine, ProductCost, ImportBatch models + CSV importer
dashboard/       ALL authenticated views, forms, Feedback/AuditEvent models,
                 management commands (seed_demo, launch_check, backup_database, bootstrap_admin),
                 audit helper
integrations/    EtsyConnection/EtsySyncRun models, Etsy OAuth flow, API client,
                 order sync, token encryption
templates/       Single template root — base.html, landing.html, dashboard/, accounts/,
                 registration/, legal/, errors/, includes/
sample_data/     EtsySoldOrdersSample.csv for import testing
```

## Core Architectural Patterns

### 1. Workspace Context (used by every authenticated view)

Every dashboard view calls `workspace_context(request)` which delegates to
`workspaces.selectors.shop_selection(request)`. This returns four values:

```python
membership, shops, shop, all_shops = shop_selection(request)
# membership: user's active Membership in their workspace (or None)
# shops: list of active Shop objects in the workspace
# shop: the currently selected Shop (or None if "all shops" is selected)
# all_shops: bool — True when the user chose "All shops" in the switcher
```

The selected shop ID is stored in `request.session[ACTIVE_SHOP_SESSION_KEY]`.
When the session has no value or the ID is stale, the first shop is used.

**Pattern**: every view starts with `context = workspace_context(request)`,
then calls `context.update({...})` to add view-specific data, then renders.

### 2. Order Scoping

`selected_orders(context)` returns the base queryset scoped to the active shop
or all workspace shops. All sales queries filter through this — never query
Order directly without shop scoping.

### 3. Permission Checks

Three helper functions gate access:

- `can_manage_shops(membership)` — Owner or Manager role
- `is_workspace_owner(request, membership)` — Owner role AND workspace.owner_id
  matches request.user.id
- `can_manage_beta_invites(request, membership)` — workspace owner AND
  `request.user.is_staff`

Superuser-only views (system_status, feedback_inbox, beta_accounts) check
`request.user.is_superuser` directly.

### 4. Audit Trail

`dashboard.audit.record_audit(request, workspace=, shop=, action=, summary=,
metadata=)` logs every significant action. Actions use dotted namespaces:
`shop.created`, `sales.imported`, `etsy.connected`, `workspace.deleted`,
`beta_invite.created`, etc. The Activity page shows these to workspace owners.

### 5. Rate Limiting

`accounts.rate_limits` provides cache-backed fixed-window rate limiting:
`is_limited(key, identifier, limit)`, `record_hit(key, identifier, window)`,
`clear_limit(key, identifier)`, `rate_limited_response(request, window)`.
Applied to login, signup, password reset, and verification email requests.

### 6. Token Encryption

Etsy OAuth tokens are encrypted with Fernet before storage:
`integrations.crypto.encrypt_token()` / `decrypt_token()`. The key is derived
from `FEELOOM_TOKEN_ENCRYPTION_KEY` (or falls back to `SECRET_KEY`).

## Data Model Relationships

```
User
  ├── owned_workspaces (FK owner → Workspace)
  ├── workspace_memberships (M2M through Membership)
  ├── legal_acceptances
  ├── created_beta_invites
  ├── submitted_feedback
  └── audit_events

Workspace
  ├── owner (FK → User, on_delete=PROTECT)
  ├── memberships (→ Membership)
  ├── shops (→ Shop)
  ├── feedback (→ Feedback)
  ├── audit_events (→ AuditEvent)
  └── beta_invites (→ BetaInvite)

Membership
  ├── workspace (FK)
  ├── user (FK)
  ├── role: owner | manager | viewer
  └── is_active

Shop
  ├── workspace (FK)
  ├── name, marketplace (etsy), external_shop_id, currency, is_active
  ├── etsy_connection (OneToOne → EtsyConnection)
  ├── orders (→ Order)
  ├── product_costs (→ ProductCost)
  └── imports (→ ImportBatch)

Order
  ├── shop (FK)
  ├── import_batch (FK, nullable)
  ├── external_order_id (unique per shop)
  ├── ordered_at, currency
  ├── item_revenue, shipping_revenue, discounts, refunds
  ├── shipping_cost, marketplace_fees, ad_fees
  ├── product_cost, net_profit, margin_percent
  ├── profit_status: profitable | low_margin | loss | incomplete
  ├── items (→ OrderItem)
  └── fee_lines (→ FeeLine)

ProductCost
  ├── shop (FK)
  ├── sku (unique per shop)
  ├── title, materials, packaging, labor, overhead
  └── unit_cost (property: sum of the four cost fields)
```

## Profit Calculation Logic

`Order.calculate_profit(costs_complete=True)`:
1. `gross = item_revenue + shipping_revenue - discounts - refunds`
2. `net_profit = gross - total_fees - shipping_cost - product_cost`
3. `margin_percent = (net_profit / gross * 100) if gross else 0`
4. Status assignment:
   - `INCOMPLETE` if costs are not complete
   - `LOSS` if net_profit < 0
   - `LOW_MARGIN` if margin < 15%
   - `PROFITABLE` otherwise

`costs_complete` is True only when every sold SKU has a ProductCost entry AND
there is no shipping revenue (shipping cost is not tracked per-order in CSV
import, so orders with shipping revenue are marked incomplete to avoid
misleading margins).

## CSV Import Logic (`sales/importers.py`)

- Accepts Etsy Orders CSV (5 MB max, 20,000 rows max)
- Decodes UTF-8-sig or CP1252
- Maps flexible column names via `row_value(row, *aliases)`
- Estimates fees when Etsy CSV doesn't break them down:
  - Transaction fee: 6.5% of gross
  - Listing fee: $0.20 per item
  - Processing fee: from "Card Processing Fees" column
- Looks up ProductCost by SKU to compute product_cost
- Skips duplicate orders (same shop + external_order_id)
- Creates ImportBatch record with row counts and error messages

## Etsy API Integration (`integrations/`)

- OAuth 2.0 with PKCE (S256 challenge)
- Scopes: `shops_r transactions_r` (read-only)
- `new_oauth_request()` → redirect to Etsy consent
- `exchange_code()` → tokens stored encrypted in EtsyConnection
- `active_access_token()` → returns cached token or refreshes if expiring <5min
- `sync_connection()` → fetches receipts via `get_receipts()` (paginated,
  `min_last_modified` for incremental sync), creates/updates Orders with
  real fee data from Etsy Payments
- Sync has a stale-run cleanup (marks runs older than 30 min as failed) and
  a "sync already running" guard via `select_for_update()`

## Frontend Conventions

### Templates
- All authenticated pages extend `base.html` (sidebar + topbar + content)
- Landing page is standalone (`landing.html`, no base.html extension)
- Error pages extend `errors/error.html`
- `request.resolver_match.url_name` is used for active nav highlighting
- Messages framework: `{% if messages %}` block in base.html renders flash messages
- `{% include "includes/analytics_consent.html" %}` at bottom of body

### CSS (`dashboard/static/app.css`)
- CSS custom properties (variables) on `:root`
- Color system: `--green` (primary/positive), `--coral` (loss/error),
  `--amber` (warning/low margin), neutral grays
- 8px-based spacing (not strictly enforced but generally followed)
- Responsive breakpoints: 1180px, 900px, 800px, 600px, 520px
- Mobile: sidebar collapses to hamburger menu (JS in base.html)
- Landing page has its own extensive CSS section at the bottom of the file
- No purple/violet hues — green/coral/amber palette throughout

### JavaScript
- Minimal inline scripts only in `base.html` (nav toggle) and
  `includes/analytics_consent.html` (GA consent)
- No external JS libraries, no build step

## Settings & Environment (`config/settings.py`)

Key settings read from environment:
- `DJANGO_SECRET_KEY` — required in production
- `DJANGO_DEBUG` — False in production
- `DATABASE_URL` — parsed by dj-database-url
- `FEELOOM_PUBLIC_HOSTNAME` / `RENDER_EXTERNAL_HOSTNAME` — for ALLOWED_HOSTS
- `FEELOOM_TOKEN_ENCRYPTION_KEY` — Fernet key for Etsy tokens
- `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD` — SMTP
- `EMAIL_VERIFICATION_REQUIRED` — auto-True when email delivery is configured
- `ETSY_API_KEY`, `ETSY_SHARED_SECRET`, `ETSY_REDIRECT_URI` — Etsy OAuth
- `SENTRY_DSN` — optional error monitoring
- `GOOGLE_ANALYTICS_MEASUREMENT_ID` — optional GA4
- `FEELOOM_LEGAL_VERSION` — legal acceptance version (currently "2026.01")
- `FEELOOM_BACKUP_READY` — flag for launch_check
- Rate limit settings: `LOGIN_IP_FAILURE_LIMIT`, `SIGNUP_IP_LIMIT`, etc.

## Testing Conventions

- Tests live in `<app>/tests.py` (single file per app)
- Django TestCase with `setUp` creating users, workspaces, shops, orders
- Tests use SQLite in-memory database
- Pattern: create test data → make request → assert response status and content
- Tests verify permission boundaries (other workspace's data is hidden)
- `seed_demo` management command creates a demo user (`demo`/`demo12345`)
  with sample shops and orders for manual testing

## Deployment (Render)

- `render.yaml` defines web service + free PostgreSQL
- Build: `pip install && collectstatic`
- Start: `migrate && bootstrap_admin && gunicorn`
- Health check: `/health/` (runs `SELECT 1`)
- Bootstrap admin: set `FEELOOM_BOOTSTRAP_ADMIN_ON_START=true` + credentials
  for first deploy, then disable
- Free Render Postgres expires in 30 days — not for real data
- `launch_check` management command: production readiness report

## What's Complete

- User accounts (signup with beta invites, login, email verification, password
  reset, rate limiting)
- Multi-shop workspace with role-based access
- Profit dashboard (metrics, trend chart, profitability breakdown)
- Sales table (search, filter, sort, CSV export, per-order detail)
- Etsy CSV import with fee estimation
- Etsy API OAuth integration with encrypted tokens and incremental sync
- Product cost library (materials, packaging, labor, overhead per SKU)
- Getting started wizard
- Privacy/data export and full workspace deletion
- Activity audit log
- Beta invite management
- Admin tools: beta accounts overview, feedback inbox, system status
- Landing page, legal pages, error pages
- CI workflow, Render deployment config, database backups

## What Could Be Added Next

### 1. SKU/Product Profitability Report
Aggregate orders by SKU showing total revenue, fees, costs, net profit, and
margin per product. Sortable by profit, volume, or margin. Highlights loss-making
SKUs. Reuses existing OrderItem + ProductCost data. New view in dashboard,
new template, new URL route. No new models needed.

### 2. Settings Page
Allow users to change workspace name, workspace currency, and their password.
Currently these are set at signup and never editable. Needs a settings view
with forms for workspace edits and password change. Reuses existing
Workspace model and Django's password change flow.

### 3. Team Member Invitations
The Membership model with roles (owner/manager/viewer) exists but there's no
UI to add or remove members. Needs an invite flow (email-based or
invite-code-based), a members list, and role management. Could reuse the
BetaInvite pattern or create a separate WorkspaceInvite model.

### 4. Shop Comparison View
Side-by-side table comparing all shops on gross sales, fees, net profit,
margin, order count, and loss count for the selected period. Makes the
multi-shop value proposition tangible. Reuses existing aggregation patterns
from the dashboard view.

### 5. Date Range Picker
Currently the dashboard offers 30/90/all-time periods. A custom date range
would let sellers analyze specific windows (e.g., holiday season). Needs a
date input in the period form and date filtering in the view.

## Code Style Rules

- Follow existing patterns exactly — look at neighboring code before writing
- Views: function-based, `@login_required`, start with `workspace_context(request)`
- Forms: Django ModelForm or forms.Form, validation in `clean_*` methods
- Models: clear `__str__`, sensible `Meta.ordering`, constraints where needed
- Templates: extend `base.html`, use existing CSS classes, no inline styles
  except dynamic values (e.g., `style="--bar-height:{{ point.height }}%"`)
- No comments unless explaining a non-obvious WHY
- No new dependencies without checking requirements.txt first
- Never query Order without shop scoping
- Always call `record_audit()` for significant actions
- CSRF tokens on all POST forms
- Use `get_object_or_404` with workspace scoping for detail views
- `url_has_allowed_host_and_scheme` for any redirect URL from user input
- `csv_safe()` for any user-controlled value written to CSV export

## Running Locally

```bash
pip install -r requirements.txt
python manage.py migrate
python manage.py seed_demo
python manage.py runserver
```

Demo login: `demo` / `demo12345`

## Running Tests

```bash
python manage.py test --verbosity=2
```

All tests must pass before any change is considered complete.

---
