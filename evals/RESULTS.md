# Evaluation results

Measured with `scripts/run_eval.py`, against the real OpenAI API, on 2026-11-01.

**This is a regression check, not a benchmark, and its limits have already mattered twice.** The
cases were written by the author and the reply checks look for words. Real customers will say things
that none of these sets contain.

## The number to read

**32/34 on `cases_clean.json`**, using `gpt-6-luna` with reasoning effort `low`.

That set was written before the last prompt was, none of its phrases are in the prompt, and it was
scored once with no changes made in response to the score. It is the most trustworthy number here,
though the same person wrote the cases and the prompt, so it is still not independent.

Of the two failures, one was a real bug and was fixed afterwards, without rescoring (see "Token
caps"). The other, "vende encordoamento?", said the instrument was not found rather than that the
store does not sell accessories, and was left as it is.

## All the scores

| Stage | Model | Dev | Held-out | Final | Shopper | Fresh | Clean |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Baseline | `gpt-4o-mini` | 49/58 | 33/44 | | | | |
| Fixes guided by dev | `gpt-4o-mini` | 58/58 | 34/44 | | | | |
| Same code | `gpt-6-luna` | 57/58 | 42/44 | | | | |
| Fixes from reading held-out | `gpt-6-luna` | 58/58 | 43/44, 44/44 | 25/26 | 23/32 | | |
| Fixes for everyday shopping | `gpt-6-luna` | 58/58 | 43/44 | 25/26 | 30/32 | 29/30 | |
| Prompt cleaned of test phrases | `gpt-6-luna` | | | | | | **32/34** |
| Token caps fixed | `gpt-6-luna` | 57/58 | 43/44 | 26/26 | 31/32 | 29/30 | |

Only the 42/44 and the 32/34 were measured before their sets could influence the code or the prompt.
The rest show that nothing important broke, and say little about new questions.

## What went wrong along the way

These are kept here because they say how far to trust the numbers.

**The first sets missed the most basic case.** After a 25/26, a real conversation failed at once:
"quero comprar um violão" was refused as out of scope. Nearly every case named a model or a policy
topic. Plain shopping phrases scored 23/32 on the same code.

**The prompt contained phrases from the tests.** Examples were written into the prompt from cases
being measured, including phrases from the set meant to be unseen, so the 29/30 was not a clean
number. The examples were replaced by general rules, and `tests/test_eval.py` now fails if a phrase
from any set appears in a prompt.

**The tuning set reached 58/58 while new questions got 34/44.** That was `gpt-4o-mini`, and a large
part of that gain was the prompt fitting the cases it had seen.

**Token caps.** `gpt-6-luna` counts reasoning against the output cap, and one message used 243
reasoning tokens out of 425. With a cap of 400, it failed on every attempt, and the customer was told
the message was not understood. The caps were raised. Only tokens used are paid for, so this does not
change the normal cost. It was only found by reading one failed case.

**Fixes caused regressions.** Treating "instrumentos" as generic made "o que vocês vendem?" list five
ukuleles. Allowing a how-to-buy answer made "me vende cabo p10" explain how to buy, with no mention
that accessories are not sold. Both were found by re-running earlier sets and reading the output. The
fix for the second was only checked with a fake model, as the budget for this round had run out.

## Passing is not the same as correct

Reading the replies found problems in cases that passed. Those marked as still open remain possible,
since nothing in the code prevents them:

- "Você pode trazer seu violão usado para avaliarmos": a service that does not exist. The prompt now
  forbids this, which reduces it without guaranteeing it.
- "Só temos essa opção disponível", after a customer picked the third item of a list. Fixed in code.
- "Não consigo confirmar qual é a mais barata", contradicting the tool. Fixed in code.
- "Foi enviado em 15/02/2026", giving the order date as the shipping date. The prompt now forbids it.
- **Still open:** "Amanhã, segunda-feira, a loja abre às 9h". The assistant has no clock, so the day
  was a guess, even if it happened to be right.
- **Still open:** "O violão de nylon usa cordas mais macias ao toque". This is general knowledge from
  outside the store's data, which the reply check cannot detect.

Some scored failures are false alarms: a correct reply can fail on a word check or on a harmless
extra action. One is a deliberate trade-off: when in doubt the assistant asks instead of refusing, so
"me conta uma piada de músico" gets a question.

## Choice of model

`gpt-6-luna` replaced `gpt-4o-mini` by a rule set before the held-out comparison: adopt it if within
2 cases, since it is cheaper and not scheduled for shutdown. It was 8 cases ahead.

| Model | Price per 1M tokens (input/output) | Cost of the 44 held-out cases, at most | Status |
| --- | --- | ---: | --- |
| `gpt-4o-mini` | US$ 0.15 / US$ 0.60 | US$ 0.0149 | Shutdown announced for 2027-01-14 |
| `gpt-6-luna` | US$ 0.10 / US$ 0.50 | US$ 0.0116 | Current |

## Known limits

- **Results vary between runs.** `gpt-6-luna` only accepts the default temperature, so the same
  message can be understood differently. Identical runs scored 43 and 44 on held-out.
- **Small samples, one author.** With 34 cases, one case moves the score by about 3 points.
- **Conversations are short.** Most cases have one to three messages.
- **No purchase flow.** The assistant informs and hands over to the store's team to close a sale.
- **Generic advice.** It lists options and asks about budget, without judging which is best.
- **Hand-written word lists.** Accessory names and policy keywords are incomplete by nature.
