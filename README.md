# jev-stortinget

**Part of aotn**, a series of small educational example projects that go with the article "The classifier you don't have to train", about TypeSafe's Jev. Each project gives Jev a different kind of text, asks it narrow typed questions, and lets plain code make every decision. Each one then measures itself against an answer key it did not tune on, and says where it falls short.

The code is here to learn from, not to run in production. Each project shows one pattern in a form small enough to read in one sitting. The rules are illustrative, and none of it is tax, engineering or political advice.

- [aotn-jev-invoices](https://github.com/tameernoor/aotn-jev-invoices): receipts and supplier invoices, VAT codes and approval
- [aotn-jev-turbine-triage](https://github.com/tameernoor/aotn-jev-turbine-triage): a year of real wind turbine alarms, triaged and checked against the operator's own labels
- [aotn-jev-stortinget](https://github.com/tameernoor/aotn-jev-stortinget): did the minister answer the question? A full session of the Norwegian parliament

## What it does

Jev reads all 3,111 written questions to ministers in the Storting's 2024-2025 session, and every paragraph of each reply. The questions to Jev are narrow yes/no questions. Code decides what the answers add up to.

## The Jev mechanism

Jev gets a **state** (the texts) and **typed questions**, and returns one calibrated value per question. Here every question is a `noul`: a yes/no answer as a value from 0 to 1. Code reads 0.8 or above as yes, 0.2 or below as no, and anything in between as unsure.

The questions come in three rounds. Code decides which questions each reply gets in the next round.

**1. What does the question ask for?** Seven nouls about the question text: `asks_amount`, `asks_time`, `asks_yes_or_no`, `asks_action`, `asks_why`, `asks_assessment`, `asks_facts` (`questions/pair.yaml`). Several can be yes.

**2. Does a paragraph give it?** Code splits the reply into paragraphs and **generates** one question per paragraph for each thing asked. All of them go to Jev in a single request, for example:

```json
{
  "state": {"question": "Når kommer ...?", "p1": "...", "p2": "...", "p3": "..."},
  "questions": {
    "time_p1": {"type": "noul",
      "instructions": "Does `p1` state a date, year, season or deadline for when the thing `question` asks about will happen or happened?",
      "criteria": {"true": "A concrete time for the thing asked, for example 'i 2026', 'før sommeren'.",
                   "false": "No time, or only a vague one ('så snart som mulig', 'fortløpende')."}},
    "time_p2": {"...": "same question for p2"},
    "time_p3": {"...": "same question for p3"}
  }
}
```

If any paragraph gets a yes, a paragraph gives what was asked.

**3. If none does: what does the reply say instead?** Each paragraph gets five more nouls: the figures don't exist, it will be dealt with later, another body is responsible, the minister can't comment, or it refers to an earlier reply. Where "later" or "no data" fires, one follow-up goes back to that paragraph: does it give a date? Does it promise to collect the figures?

This is a classifier defined at runtime. The question ids and their count change from reply to reply, and nothing is trained. Every question stays narrow enough for Jev to be sure. All the templates are in `src/jev_stortinget/tree.py`.

## What it found

| | Replies |
|---|---|
| A paragraph gives what was asked | 1,226 of 3,111 |
| ... in the first paragraph | 314 of those 1,226 |
| Says it will be dealt with later | 751, of which 219 give a date |
| Says it is someone else's job | 573 |
| Says the figures don't exist | 130, of which 9 promise to start collecting |
| Says the minister can't comment | 81 |

The "later", "someone else's job", "figures don't exist" and "can't comment" rows only count the 1,885 replies where no paragraph gave what was asked. The lists, with the deciding paragraph for each entry, are in `results/tree/`. Counts per minister are in `by-minister.json`.

## How far to trust it

An independent reader checked 15 random replies per outcome, blind. The reader was a separate AI model session that saw only the question, the reply and the label definitions.

| Jev found | Reader agreed |
|---|---|
| can't comment | 15 / 15 |
| a paragraph gives what was asked | 14 / 15 |
| later | 13 / 15 |
| someone else's job | 12 / 15 |
| no data | 12 / 15 |

Jev is reliable on what a paragraph says. It is not reliable on whether a whole reply failed to answer: of 90 sampled replies with no paragraph that gives what was asked, the reader still judged 39 as answered. So this project reports what replies contain, not which questions went unanswered. The data is in `results/tree/blind-check.json`.

## Cost

Each request carries all of one reply's questions, and Jev answers them in parallel. Up to 20 requests run at once.

| | Requests | Questions answered | Cost | Time |
|---|---|---|---|---|
| Step 2: does a paragraph give it? | 3,111 | 31,567 | $0.29 | 96 s |
| Step 3: what does it say instead? | 3,337 | 45,570 | $0.385 | 98 s |
| Total | 6,448 | 77,137 | $0.675 | 194 s |

## How to run

```
cp .env.example .env                                   # set TYPESAFE_API_KEY
uv run python -m jev_stortinget fetch                  # download the session
uv run --env-file .env python -m jev_stortinget run --set all   # step 1
uv run --env-file .env python -m jev_stortinget tree            # steps 2 and 3
uv run python -m jev_stortinget tree --from out/tree-all.json   # rebuild results/, no Jev calls
```

## Data and licence

The data comes from [data.stortinget.no](https://data.stortinget.no/). It is open under the Norwegian Licence for Open Government Data (NLOD), provided Stortinget is credited as the source ([terms](https://data.stortinget.no/om-datatjenesten/bruksvilkar/)). The texts are Norwegian. The service rate-limits bursts, so `fetch` keeps 2 requests in flight and backs off on 429.
