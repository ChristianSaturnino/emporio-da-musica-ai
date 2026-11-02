# Empório da Música — AI Customer Service Agent

A Python agent that answers customers of Empório da Música, a fictional musical instrument store in
Campo Grande/MS. Built for the Artefact AI Engineer technical challenge.

**The core idea: the model interprets and writes, and Python decides what it can.** Prices, stock and
access to orders are worked out by code from the data. The model turns messages into a structure and
writes most replies, so their wording and its reading of the store's policies still depend on it. The
agent replies to customers in Brazilian Portuguese.

## Project overview

The store's team is overloaded with recurring questions. This agent handles them over text:

| The customer asks about | Example | Where the answer comes from |
| --- | --- | --- |
| Products and availability | "Quais violões têm até R$ 1.000?" | Product data |
| Prices and promotions | "Quanto custa o Takamine GD20?" | Product data and pricing rules in Python |
| Comparing products | "Qual a diferença entre a Telecaster e a Stratocaster?" | Each product's description and specs |
| An order | "Onde está meu pedido 8?" | Order data, after checking the customer's email |
| Store policies | "Me arrependi, posso devolver?" | Sections of the policy manual |
| Store information | "Abre domingo? Qual o endereço?" | Policy manual, plus the real date from code |
| Instruments in general | "O que é um captador?" | The model's general knowledge (see limitations) |

**Out of scope:** unrelated subjects get a fixed message with no second model call. If a message
mixes a store question with something else, the store question is answered and the rest is declined.
Accessories are declined too, as the manual says the store does not sell them. The agent only
informs: buying, cancelling and changing an order are handed over to the team.

## Demo

A real exchange, not edited (from [example 5](examples/05_multi_intent.md)), after listing the
guitars:

> **Cliente:** Quero a Fender
>
> **Assistente:** A Fender Telecaster Player Series custa R$ 5.400,00 (R$ 5.130,00 no PIX), e a Fender
> Stratocaster American Pro II custa R$ 12.500,00 (R$ 11.875,00 no PIX). Você prefere a Telecaster ou
> a Stratocaster?
>
> **Cliente:** Qual a diferença entre a Telecaster e a Stratocaster?
>
> **Assistente:** As duas são guitarras elétricas de 6 cordas, com corpo em alder e braço em maple. A
> Telecaster Player Series tem 2 captadores Alnico 5 e é descrita com o autêntico “twang” da Fender e
> tocabilidade moderna. A Stratocaster American Pro II tem 3 captadores V-Mod II single-coil e é descrita
> como brilhante e versátil para diversos estilos. [...]

Running the app needs an OpenAI key. Without one, the [examples](examples/) show real conversations.

## Architecture

```
Customer message
   |   Python masks anything in email format before the model sees it
   v
1. understand   LLM     Turns the message into a validated structure. Writes nothing for the customer
2. validate     Python  Rejects invalid output, removes padding, corrects what the model refused wrongly
3. execute      Python  Runs one known handler per intent: SQL, pricing rules, policy search
4. respond      LLM     Writes from those results only, when a fixed message is not enough
   |            Python  Looks for values in the reply that are not in the data, before sending it
   v
Reply in Brazilian Portuguese
```

The model has no direct access to the database, writes no SQL and cannot choose a tool outside a
closed list. It is not blind to the data, though: the writing step receives what the tools returned,
and both steps see the recent conversation, which can hold prices and a verified order's status.
There is no autonomous loop: each turn takes this path once. A greeting needs no model, and the price
of a product that can be bought needs no writing step, which makes those replies faster.

More detail is in [docs/architecture.md](docs/architecture.md).

**Why LangGraph:** a linear flow does not strictly need it, but it makes the state and the failure
routes explicit and each step testable, at little cost in code. I chose this over a ReAct agent or
free function calling because this is a domain where a wrong price or a leaked order is worse than a
less flexible conversation.

## Tech stack

| Layer | Choice | Why |
| --- | --- | --- |
| Language | Python 3.11+ | Required by the challenge. Developed on 3.12 |
| Model | OpenAI `gpt-6-luna`, configurable in `.env` | Measured ahead of `gpt-4o-mini`, and cheaper ([comparison](evals/RESULTS.md#choice-of-model)) |
| Workflow | LangGraph | Explicit state and failure routes |
| Validation | Pydantic | The model's output must fit a fixed contract |
| Operational data | SQLite, rebuilt from the CSV files | Reliable joins and no server to install |
| Policies | `pypdf` and lexical search over PDF sections | Short, structured manual, and every result can be explained |
| Interface | Streamlit, plus a command-line chat | Little code, keeping the effort on the agent |
| Tests | `pytest` with a fake model, `ruff` | Fast, free and repeatable |

## Getting started

You need Python 3.11 or newer and an OpenAI API key.

```bash
git clone https://github.com/ChristianSaturnino/emporio-da-musica-ai.git
cd emporio-da-musica-ai
```

Windows (PowerShell). Use a folder with a short path, as some dependencies can exceed the Windows
path limit in very deep folders:

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

`pyproject.toml` allows compatible versions of each dependency. To get the exact versions this was
last tested with (Python 3.12 on Windows), run this before the install command above:

```bash
python -m pip install -r requirements-lock.txt
```

Fill in `OPENAI_API_KEY` in `.env`, then create the local database. This can be repeated safely, as
it rebuilds the database from the CSV files.

```bash
python scripts/seed_db.py
```

Start the chat, then open <http://localhost:8501>:

```bash
streamlit run app.py
```

For a quick check without a browser, there is also a command-line chat:

```bash
python main.py
```

If OpenAI refuses the key, the model or a setting, or the account has no credit, the chat says which
settings to check, and with Streamlit the terminal shows the error type. If your key cannot use
`gpt-6-luna`, set `OPENAI_MODEL=gpt-4o-mini` in `.env`. It runs, and it scored lower when measured.

Using the agent has a small cost. As an estimate, the last complete evaluation came to about
US$ 0.0003 per conversation, most of them one to three messages, at the list prices of `gpt-6-luna`.
This varies with the length of the messages and history and with retries, and nothing caps it.

## Data sources and assumptions

`data/` holds the synthetic files supplied with the challenge: six CSV files and the policy manual
(version 2.1, June 2025). All customers and orders are fictional. They are a static snapshot, so
replies about stock and orders reflect that snapshot and not a live system.

The challenge invites documenting assumptions where the material is unclear. Where the manual
contradicts itself, the conservative reading wins:

- **Two phone numbers** appear for the same channel, so both are given.
- **The email in section 1.2** has an accent in the domain, so the one on the cover is used.
- **Installments:** section 3.1 prevails over the R$ 100 minimum in the section 3 table, as it is more
  specific. Its limit for up to 3 installments is unclear, so those are not confirmed.
- **An order of exactly R$ 500** is neither "above" nor "below" the free shipping limit, so free
  shipping is not promised.
- **Withdrawal** needs the purchase channel and delivery date. The model is told not to assert
  eligibility and to refer the customer to the team, through notes written in code. It is an
  instruction it follows, not a decision made by code.

**What I would hand back to the store to fix.** These are kept as they are, and handled in code:

- Six product descriptions name a different brand from the product (135, 137, 139, 140, 142 and 144).
  A "Yamaha" drum kit is described as a Pearl Export. Those descriptions are not shown, so comparisons
  involving these products are poorer.
- Product 130 is `coming_soon` but appears in orders 3 and 14. Product 136 is `discontinued` with 13
  units, and product 96 is `active` with none. Status comes first when deciding what is on sale.
- Orders 3 and 20 have a stored total lower than their items at list price.
- Customers 11 and 43 share an email address.
- The manual mentions more than 300 instruments, including wind and orchestral ones. The data has 65,
  in six of the nine categories.

## Business rules

These are worked out in Python and covered by tests. When the model writes the reply, it restates
them, and the limits of that are described under security below.

- **PIX:** 5% off the list price, and never on top of a promotion.
- **Promotions:** only active ones count. The original price, percentage and final price are shown,
  and they do not apply to products that cannot be bought.
- **Availability:** a product is on sale only if its status is `active` and it has stock. A product
  that cannot be bought is reported as such, with alternatives where they exist.
- **Accessories:** the store does not sell them, so asking for one is declined. Detection uses a word
  list, which is incomplete by nature.
- **Orders:** the order number and the email of the customer who placed it are both required. A wrong
  email and an unknown order get the same answer, so nothing confirms that an order exists.
- **Money:** stored as integer cents, handled as `Decimal` and rounded half up.

**Policies are different.** Python finds the relevant sections of the manual and attaches notes where
it is ambiguous, but the model applies them to the customer's case. Answers about withdrawal, exchange
and warranty depend on its interpretation.

## Testing

These need no API key and cost nothing. The tests use a fake model and cannot reach the network.

```bash
python -m pytest
ruff check .
python scripts/inspect_data.py
```

They prove what the system does once a message is understood. How well the real model understands is
measured separately, at a small cost:

```bash
python scripts/run_eval.py fixes service clean closing --max-cost 0.05
```

That script runs hand-written conversations against the real model. A case can check any message of a
conversation, though most only check the last, and a technical failure at any message fails the case. It
exits with an error if a case fails or if the run was cut short. `--max-cost` is an
estimate-based limit checked before each message, not a hard ceiling.

In the latest complete run, on the final code, **470 of 479 conversations passed**, up from 465 in the
run before. Of the 9 failures, 2 were real, 5 were acceptable replies that a check rejected, and 2 were
known trade-offs. A set written for a last audit, with 71 more conversations, scored 66 on its first run
and 69 after the fixes it led to. Its 2 failures were real. The 4 real failures depend on how the model
reads the message, and they are described in the report.

| Set | Covers | First run | Latest complete run |
| --- | --- | ---: | ---: |
| `audit` | Context, model names, several requests, paraphrased policies | 66/71 | 69/71 |
| `fixes` | Failures found by the technical review, with checks on some middle messages | 8/8 | 8/8 |
| `clean` | Everyday questions | 32/34 | 34/34 |
| `attack_final` | Prompt injection, with legitimate messages as controls | 19/19 | 17/19 |
| `review_final` | Typos, price ranges, follow-ups, attempts to dictate prices | 36/36 | 36/36 |
| `review_last` | Buying, withdrawing, warranty, mixed requests | 20/20 | 20/20 |
| `closing` | Everyday and edge cases | 18/20 | 20/20 |
| `service` | Comparing products, questions about items, choosing by name | 32/32 | 35/35 |

Each set was written before the code it first measured, but all of them came from the same process as
the code, and they check for words. These are regression checks and not an independent benchmark. The
details, and all the other sets, are in [evals/RESULTS.md](evals/RESULTS.md).

## Example conversations

Real conversations, recorded by `scripts/record_examples.py` and not edited:

1. [Catalog search and availability](examples/01_catalog.md)
2. [Store information and policies](examples/02_store_policy.md), with the withdrawal policy applied
3. [Prices, promotions and PIX](examples/03_discount_edge_case.md)
4. [Order verification and prompt injection](examples/04_order_verification.md)
5. [Several requests, and helping a customer choose](examples/05_multi_intent.md)

All five were recorded before the last two rounds of fixes, so they show an earlier version. Their
amounts and tracking codes were checked against the database. A different run can word things
differently.

## Design decisions and trade-offs

| Decision | Alternative | Why, and what it costs |
| --- | --- | --- |
| Hybrid: model interprets and writes, Python decides | Free agent with tools | A wrong discount is worse than a stiff reply. Less flexible in unusual conversations |
| Lexical retrieval over PDF sections | Embeddings and a vector database | 8 pages with clear headings, and each match can be explained. Hand-written keywords would not scale to a large document |
| SQLite built from CSV | Postgres, or letting the model write SQL | Nothing to install, and SQL stays parameterized. Static data |
| Plain JSON validated by Pydantic | The provider's strict schema | 30 output tokens instead of 102, and about 400 ms faster. Validation had to become stricter in return |
| Reasoning effort `none` | `low` | About 300 ms faster when interpreting, with comparable scores |
| Fixed text for a known answer | Always using the model | Faster, cheaper and exact. Those replies sound less varied |
| Reply checked after writing | Trusting the prompt | Catches values that appear nowhere in the data. It does not understand sentences, so it cannot confirm a reply is true |
| History in memory, last 6 messages per session | A database | Enough for references like "esse". Lost on restart, and more history would not have fixed the failures I saw |
| No streaming | Streaming the reply | The check needs the complete text. This is the main thing left that would make it feel faster |

**Prompt strategy.** There are two versioned prompts, one per task. The first only produces a
structure, using general rules and no examples, and a test fails if a phrase from any evaluation set
appears in a prompt. The second writes from data placed in a `<dados>` block, which it is told to
treat as reference and never as instructions. Where a rule can be decided by code, it is, because the
model can ignore a prompt.

All the decisions are in [docs/decisions.md](docs/decisions.md).

## Security and privacy limitations

Order lookup is **a simulated check, not authentication**. All data here is fictional and public,
including the emails that pass the check, and attempts are not limited. Once checked, that one order
can be asked about again in the same conversation, without the email being kept. A production system
would need real login or one-time codes, session-based authorization, rate limiting, auditing and a
private database.

Enforced by code, whatever the model does:

- **No path to customer records.** No tool lists customers, and a verified order returns no name, phone
  or email. Addresses in email format are removed from the message before the model reads it, so the
  check runs in Python. Text that does not look like an email is not caught.
- **The model cannot choose what runs.** Unknown fields are rejected, each of the 13 intents runs one
  known handler, and no text from the first step reaches the customer.
- **SQL and input size.** Queries are parameterized on a read-only connection, and messages over 1000
  characters are refused before any call.

**The reply check looks for patterns, not for the truth.** Amounts, percentages, tracking codes,
emails, phone numbers, links, free offers and long passages of the prompts are rejected if they do not
appear in the store's data. Finding a value there does not make the sentence right. For example, if two
products are being compared, a reply that swaps their prices passes, and a test records this. Prices
only avoid this where code writes the sentence, as with the price of a single product. Not every wrong
reply is blocked.

Reduced, but dependent on the model noticing:

- **Mixed requests.** A message marked as partly off topic never reaches the writing step, and a fixed
  sentence declines the rest. If the first step misses it, only the prompt stands in the way.
- **Planted instructions.** Messages marked as off topic are dropped from the conversation context.

Not covered:

- **Claims without a checkable shape.** "Custa um real" or an invented service pass the checks, though
  the prompt forbids them.
- **Paraphrased prompts.** Only long verbatim passages are caught. The repository is public, so the
  prompts hold no secret.
- **Other personal data.** Only emails are masked. A CPF or phone number typed into the chat is sent to
  OpenAI, with `store=False`, and is not written to the logs.
- **Cost.** There is no limit on messages, which a public deployment would need.

## Known limitations and what I would do with more time

Current limitations:

- **General knowledge.** Questions about instruments in general use the model's own knowledge, and
  nothing verifies those answers. It is limited to instruments, and a model the store sells is answered
  from the catalog. I accepted this because a store attendant would answer them.
- **No holiday calendar.** The date comes from code, but "pode sim, amanhã abre" is possible on a
  holiday, with a general warning that the store closes on holidays.
- **Results vary between runs.** The same message can be understood differently. For example, "Prazo de
  troca?" sometimes explains only the defect rule and leaves out the 7 days for preference, and "me ajuda
  a escolher uma guitarra" can ask about budget without showing the guitars.
- **At most three requests per message.** Beyond that, the reply says so and asks for the rest.
- **Hand-written word lists.** Accessory names and policy keywords are incomplete by nature.
- **Hard-coded data fixes.** The six unreliable descriptions are listed by ID in the code.

With more time, in this order:

1. **Measure real conversations.** The evaluation sets have blind spots, and real conversations found
   failures every time I tried. I would track how many conversations end without a human, which is what would
   actually relieve the team, and where they get stuck.
2. **Hand over with context.** The agent currently gives the store's contacts. The team should receive
   the conversation, so the customer does not have to repeat it.
3. **Connect to live systems,** replacing the snapshot with real stock and orders, behind real
   authentication.
4. **Add a holiday calendar,** and persist conversations between sessions.
5. **Use semantic retrieval** if the manual or catalog grows enough to justify it.

## AI-assisted development

I used **Claude Code** (Anthropic) as development support, under my supervision from start to finish.
This is how the work went:

1. **Understanding the challenge.** We started from the statement and the supplied data, separating
   what was mandatory from what was optional and inspecting the CSV files and the policy manual.
2. **Choosing the architecture.** I proposed an approach based on a sales and service agent I had
   worked on before: the model interprets and writes, and code decides. Only the idea carried over.
   No code or prompts from that project were reused.
3. **Adapting it to this case.** Claude Code analyzed how that structure would fit this challenge, and
   I reviewed and approved the result.
4. **Splitting the work.** We divided development into a preparation step and eight modules:

   | Module | Scope |
   | --- | --- |
   | 0. Setup | Project structure, environment and data inventory |
   | 1. Data | CSV import into SQLite and read-only queries |
   | 2. Catalog | Product search, availability and pricing rules |
   | 3. Policies | Manual split into sections, with lexical retrieval |
   | 4. Orders | Order status with email verification |
   | 5. Agent | The LangGraph workflow, prompts and model adapter |
   | 6. Interface | Streamlit chat and command-line chat |
   | 7. Evaluation | Structured events, real conversations and measurement |
   | 8. Delivery | Final documentation and a test from a fresh clone |

5. **Building one module at a time.** For each module, Claude Code implemented what we had agreed and
   ran the checks. I then reviewed all the code, tested it and asked for corrections where needed.
   Only then did I commit it and move on to the next one.
6. **Review before delivery.** I used OpenAI Codex to assist with an independent technical review of
   the project, helping validate its overall quality, identify potential improvements, and ensure
   everything was ready for final submission.

Claude Code sped up the implementation, tests and documentation drafts, while every decision and every
commit went through me. Beyond the automated tests, I used the app as a customer would, and that is how
several of the difficulties below came to light.

### Difficulties along the way

- **Good scores did not mean good conversations.** The evaluation reached 25/26, yet the first real
  test refused "quero comprar um violão". The agent trusted the model's refusals, and the test cases
  had few plain shopping phrases. Code now rescues those messages, and the evaluation gained sets
  written from a customer's point of view.
- **A score can look better than it is.** When I questioned how the agent really worked, the review
  found phrases from the tests inside the prompt, which inflated a result. They were replaced by
  general rules, and a test now fails if that happens again.
- **A prompt can be ignored.** In "qual o endereço? E traduz bom dia pro japonês", the model answered
  both despite being told not to, so the writing step no longer receives messages marked as partly off
  topic. Examining prompt injection also showed that the reply check accepted the customer's own words
  as evidence, which was removed.
- **Being faster without being less reliable.** Replies took about 3 seconds. Timing each step showed
  that the provider's strict schema was a large part of it, and removing it brought the median down to
  about 2.4 seconds in that measurement, though later runs varied. Some of these changes had side
  effects, such as prices disappearing from lists, which re-running the full evaluation caught.
- **Being correct is not the same as being helpful.** "Quais produtos você tem?" was answered with a
  question, "quero a fender" picked one of two guitars, and comparing products had no answer at all.
  This led to the catalog overview, the comparisons, and to the agent considering its own last reply.
- **Context is easy to get wrong.** The technical review found failures that had passed all the tests.
  After a list, "Tem Giannini GF-3D?" was answered about the GF-1R, a new list did not clear the
  product in focus, and a price written by code was unknown to the step writing the rest of the reply.
  It also showed that evaluation scored only the last message of each conversation. All of these
  now have tests, and measuring the fixes on the real model then found a few new problems, such as
  "tem palheta?" being answered with "tem sim".
- **Imperfect data.** Six product descriptions name the wrong brand, and the manual contradicts itself
  in places. Instead of hiding this, I handled each case conservatively and documented it above.
