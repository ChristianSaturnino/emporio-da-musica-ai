# Empório da Música — Conversational AI Assistant

A Python customer-service agent for Empório da Música, a fictional musical instrument store in
Campo Grande/MS, built for the Artefact AI Engineer technical challenge.

## Status

Work in progress. The datasets, the read-only data layer, the catalog, policy and order tools and
the conversation workflow exist so far, but there is no user interface yet. Automated tests use a
fake model. This README will document the architecture, known limitations and
AI-assisted development workflow as each part is completed. Decisions made so far are in
[docs/decisions.md](docs/decisions.md).

## Requirements

- Python 3.11 or newer

## Setup

Windows (PowerShell):

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
```

macOS/Linux:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
cp .env.example .env
```

Then create the local database. The command can be repeated: it rebuilds the database from the CSV
files without duplicating data.

```bash
python scripts/seed_db.py
```

## Checks

```bash
python scripts/inspect_data.py
python -m pytest
ruff check .
```

None of these commands call an external API.

## Data

`data/` holds the synthetic datasets supplied with the challenge: six CSV files and the store
policy manual (version 2.1, June 2025). All customers and orders in them are fictional. They are a
static snapshot, not a live backend.

Known quirks in the supplied data, kept as they are:

- Orders 3 and 20 have a stored total lower than the sum of their items at list price. The stored
  total is what the data layer returns.
- Product 130 has status `coming_soon` but appears in orders 3 and 14.
- Customers 11 and 43 share the same email address.
- Product status and stock are independent: product 96 is `active` with no stock, while product 136
  is `discontinued` with 13 units.
- Six descriptions name a different brand from the product name (products 135, 137, 139, 140, 142
  and 144), and product 142 is described with 88 keys while its specs say 61. The name and the
  structured fields are treated as authoritative, and those six descriptions are not shown.
- The policy manual contradicts itself in places, such as two different WhatsApp numbers. See
  decision 014 in [docs/decisions.md](docs/decisions.md) for how each case is handled.
- The manual mentions more than 300 instruments, including wind and orchestral ones. The product
  snapshot has 65, in six of the nine categories.

## Security and privacy limitations

Order lookup asks for the order ID and the email of the customer who placed it, and it returns only
the status, date, items, tracking code and estimated delivery. **This is a simulated check, not
authentication.** All data here is fictional and public, including the emails that pass the check,
and attempts are not limited. A production system would need real login or one-time codes,
session-based authorization, rate limiting, auditing and a private database.
