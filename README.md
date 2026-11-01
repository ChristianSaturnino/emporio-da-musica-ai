# Empório da Música — Conversational AI Assistant

A Python customer-service agent for Empório da Música, a fictional musical instrument store in
Campo Grande/MS, built for the Artefact AI Engineer technical challenge.

## Status

Work in progress. The challenge datasets and the read-only data layer exist so far: the agent is
not implemented yet. This README will document the architecture, known limitations and
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
