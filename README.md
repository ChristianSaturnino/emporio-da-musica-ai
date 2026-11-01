# Empório da Música — Conversational AI Assistant

A Python customer-service agent for Empório da Música, a fictional musical instrument store in
Campo Grande/MS, built for the Artefact AI Engineer technical challenge.

## Status

Work in progress. Only the project scaffolding and the challenge datasets exist so far: the agent
is not implemented yet. This README will document the architecture, technical decisions, known
limitations and AI-assisted development workflow as each part is completed.

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
