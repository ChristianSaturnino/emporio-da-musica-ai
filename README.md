# Empório da Música — Conversational AI Assistant

A Python customer-service agent for Empório da Música, a fictional musical instrument store in
Campo Grande/MS, built for the Artefact AI Engineer technical challenge.

## Status

Work in progress: the final documentation is still to come. The agent works through a Streamlit chat
and a command-line chat. Real conversations are in [examples/](examples/), and measured results are
in [evals/RESULTS.md](evals/RESULTS.md). This README will document the architecture, known limitations and
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

Fill in `OPENAI_API_KEY` in `.env`. Then create the local database. The command can be repeated: it
rebuilds the database from the CSV files without duplicating data.

```bash
python scripts/seed_db.py
```

## Running

```bash
streamlit run app.py
```

Then open <http://localhost:8501>. For a quick check without a browser, there is also a
command-line chat:

```bash
python main.py
```

Both call the OpenAI API, which costs a small amount per message.

## Checks

```bash
python scripts/inspect_data.py
python -m pytest
ruff check .
```

None of these commands call an external API: tests use a fake model and cannot reach the network.

Two scripts do call the OpenAI API, which costs a small amount:

```bash
python scripts/run_eval.py dev heldout final shopper fresh clean attack attack_fresh attack_final calendar review review_final review_last closing service
python scripts/record_examples.py
```

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

## Prompt injection and off-topic questions

The design assumes the model can be fooled, so what matters most does not depend on it.

Enforced by code, whatever the model does:

- **Customer data is out of reach.** No tool lists customers, and a verified order returns no name,
  phone or email. The email check runs in Python on an address the model never sees.
- **The model cannot choose what runs.** Its output is validated against a fixed structure that
  rejects unknown fields, and each of the 13 intents runs one known handler. There is no loop, and no
  text from the first step reaches the customer.
- **Off-topic questions cost nothing to refuse.** They get a fixed message without a second call.
- **Replies are checked against what the store said.** Amounts, percentages, tracking codes, emails,
  phone numbers, links, free offers and long passages of the prompts are rejected unless they are in
  the data. What the customer wrote does not count as evidence.
- **SQL and input size.** Queries are parameterized on a read-only connection, and messages over
  1000 characters are refused before any call.

Reduced, but dependent on the model noticing:

- **Mixed requests.** When the first step marks a message as partly off topic, the model writing the
  reply does not read that message at all, and a fixed sentence declines the rest. If the first step
  misses it, only the prompt stands in the way. In one measured run, it answered a translation.
- **Planted instructions.** Messages marked as off topic are dropped from the conversation context.

Not covered:

- **Claims without a checkable shape.** "Custa um real" or an invented service pass the checks,
  though the prompt forbids them.
- **General knowledge about instruments.** The agent may explain what a pickup is, for example, and
  nothing checks that explanation. Store facts and named models still come from the data.
- **Paraphrased prompts.** Only long verbatim passages are caught. The repository is public, so the
  prompts hold no secret.
- **Cost.** There is no limit on messages. A public deployment would need rate limiting, as each
  message can cost up to about US$ 0.003.

See [evals/RESULTS.md](evals/RESULTS.md) for how this was measured.

## Security and privacy limitations

Order lookup asks for the order ID and the email of the customer who placed it, and it returns only
the status, date, items, tracking code and estimated delivery. Once checked, that one order can be
asked about again in the same conversation, without the email being kept. **This is a simulated
check, not authentication.** All data here is fictional and public, including the emails that pass
the check, and attempts are not limited. A production system would need real login or one-time codes,
session-based authorization, rate limiting, auditing and a private database.

Only email addresses are masked before reaching the provider. A CPF or phone number typed into the
chat is sent to OpenAI, with `store=False`, and is not written to the logs.
