# jev-stortinget

**Part of aotn**, a series of small educational example projects that go with the article "The classifier you don't have to train", about TypeSafe's Jev. Each project gives Jev a different kind of text, asks it narrow typed questions, and lets plain code make every decision. Each one then measures itself against an answer key it did not tune on, and says where it falls short.

The code is here to learn from, not to run in production. Each project shows one pattern in a form small enough to read in one sitting. The rules are illustrative, and none of it is tax, engineering or political advice.

- [aotn-jev-invoices](https://github.com/tameernoor/aotn-jev-invoices): receipts and supplier invoices, VAT codes and approval
- [aotn-jev-turbine-triage](https://github.com/tameernoor/aotn-jev-turbine-triage): a year of real wind turbine alarms, triaged and checked against the operator's own labels
- [aotn-jev-stortinget](https://github.com/tameernoor/aotn-jev-stortinget): did the minister answer the question? A full session of the Norwegian parliament

## What it does

Every member of the Storting can put a written question to a government minister, and every minister must reply. This project reads every such pair from the 2024-2025 session, question and reply both in Norwegian, and hands them to Jev, TypeSafe's typed-question model. Jev never makes one big judgment call about whether a reply answers a question. It answers a stack of narrow yes-or-no questions instead, three steps of them, and plain code decides what the answers add up to. Of the session's 3,234 written questions, 3,111 asked for something the classifier could recognise, an amount, a time, a plain yes or no, an action, a reason, the minister's own view, or a set of facts, and those 3,111 are what the dodge tree below runs on.

## The dodge tree

A small diagram of the three steps.

```
question
  │
  ▼
1. What does the question ask for?
   the seven asks_* nouls, one request per question (questions/pair.yaml)
  │
  ▼
2. For each paragraph of the reply: does it give that thing?
   one request per pair, one noul per paragraph per asked type
  │
  ├─ some paragraph reads yes ──────────────────────────► answered
  │
  ▼ no paragraph does
3. For each paragraph: why not?
   no data · later · someone else's job · can't comment · refers to an earlier reply
  │
  ├─ "later" fired on a paragraph ──► was a date given?
  ├─ "no data" fired on a paragraph ──► was collecting it promised?
  │
  ▼ nothing fired anywhere
  one last check: did the reply swap in a different number, date or yes/no
  from what was actually asked?
```

Each of these questions is narrow enough that Jev can actually be sure of the answer, instead of hedging on one fuzzy "did this answer the question" call.

## What it found

Three lists, all in `results/tree/`, and all with the reply's own words. All of it only looks inside the 1,885 of 3,111 pairs where no paragraph gave what was asked; a pair Jev already called answered is never checked for these tags, so the counts below (130, 751, 573) are shares of those 1,885, not of the full 3,111.

**"Norway doesn't count this."** 130 replies say straight out that the figures don't exist, aren't registered, or can't be pulled. Only 9 of those also promise to start collecting them.

> Det ligg ikkje føre tal som syner utviklinga i tilgang på beiteareal i perioden frå 2012. Det vert arbeidd med å utarbeide ei betre oversikt over dette.
> (`results/tree/no-data.json`, id 99802, landbruks- og matministeren, collection promised)

> Finansdepartementet har ikke en oppdatert oversikt over alle navngitte tilskuddsmottakere.
> (`results/tree/no-data.json`, id 105174, finansministeren, no promise)

**"Promised later."** 751 replies say the matter will be considered, decided or followed up later. 219 of them actually give a date.

> Regjeringen vil komme tilbake til saken i kommuneproposisjonen for 2026, som blir fremmet 15. mai 2025.
> (`results/tree/promised-later.json`, id 104529, kommunal- og distriktsministeren, date given)

> Jeg vil nå sette meg nærmere inn i rapporten.
> (`results/tree/promised-later.json`, id 101713, justis- og beredskapsministeren, no date)

**"Someone else's job."** 573 replies point to another body, a municipality, an agency, a health trust, the EU, as the one actually responsible. This one is more informative broken down by ministry than as a single number, alongside the other four tags. From `results/tree/by-minister.json`, ministries with at least 50 pairs; `n` is every tree-eligible pair for that ministry, `no hit` is how many of those had no paragraph give what was asked, and the five tag columns are shares of `no hit`, not of `n`.

| ministry | n | no hit | no data | can't comment | someone else's job | later | refers back |
| --- | --- | --- | --- | --- | --- | --- | --- |
| arbeids- og inkluderingsministeren | 122 | 65 | 5 | 4 | 11 | 26 | 5 |
| barne- og familieministeren | 70 | 36 | 4 | 1 | 8 | 18 | 7 |
| digitaliserings- og forvaltningsministeren | 56 | 27 | 6 | 0 | 9 | 15 | 5 |
| energiministeren | 242 | 154 | 7 | 3 | 41 | 56 | 4 |
| finansministeren | 245 | 144 | 17 | 7 | 14 | 50 | 17 |
| fiskeri- og havministeren | 118 | 75 | 2 | 9 | 19 | 45 | 5 |
| forsknings- og høyere utdanningsministeren | 73 | 42 | 4 | 0 | 17 | 13 | 2 |
| forsvarsministeren | 134 | 84 | 0 | 1 | 6 | 40 | 8 |
| helse- og omsorgsministeren | 423 | 295 | 15 | 6 | 138 | 119 | 29 |
| justis- og beredskapsministeren | 331 | 205 | 15 | 17 | 53 | 65 | 28 |
| klima- og miljøministeren | 175 | 104 | 3 | 4 | 28 | 50 | 8 |
| kommunal- og distriktsministeren | 143 | 83 | 5 | 15 | 29 | 28 | 7 |
| kultur- og likestillingsministeren | 100 | 56 | 3 | 2 | 14 | 29 | 3 |
| kunnskapsministeren | 134 | 72 | 14 | 4 | 35 | 24 | 7 |
| landbruks- og matministeren | 78 | 35 | 1 | 0 | 7 | 9 | 2 |
| næringsministeren | 123 | 88 | 6 | 7 | 43 | 24 | 6 |
| samferdselsministeren | 414 | 236 | 20 | 0 | 84 | 112 | 30 |
| utenriksministeren | 96 | 61 | 1 | 0 | 11 | 14 | 8 |

A pair can carry more than one tag (a reply can say both "later" and "someone else's job" in different paragraphs), so a row's tags don't have to add up to its `no hit` either, and these are ministry sizes, not a scorecard: a bigger ministry naturally answers more written questions and so naturally racks up bigger counts everywhere.

## How far to trust it

An independent reader, a separate AI model session that saw only the question, the reply and the label definitions, never Jev's own questions or answers, checked 15 random pairs from each category blind. For each thing Jev found, here is how often the reader agreed the reply actually does it.

| Jev found | reader agreed |
| --- | --- |
| can't comment | 15 / 15 |
| later | 13 / 15 |
| someone else's job | 12 / 15 |
| no data | 12 / 15 |
| answered | 14 / 15 |
| possible swap | 1 / 15 |
| no answer and no reason | 3 / 15 |

The last two rows failed the check. At 1 in 15 and 3 in 15, "possible swap" and "no answer and no reason" agree far less often than every other category, so neither is reported anywhere in `results/tree/`: they were checked and set aside, not shipped.

That is solid agreement on the other five, on what a paragraph says. It is not the same claim as "the reply as a whole failed to answer": across the 90 sampled non-answered pairs (the six categories above other than "answered", 15 each), the reader still judged 39 of them as answered overall, reading things the paragraph-by-paragraph pass didn't ask about, tone, an implied answer spread across two paragraphs, background that amounts to a yes. So this project does not say which questions went unanswered. It says what a reply's paragraphs actually contain, which turns out to be a more checkable claim than "answered or not" and, on this evidence, a more reliable one too.

The sample, Jev's verdicts and the reader's labels are in `results/tree/blind-check.json`.

## Cost and speed

Step 2, paragraph by paragraph, ran 3,111 pairs as 31,567 paragraph questions, one request per pair, for $0.29 in 96 seconds.

Step 3, why not, ran 1,885 pairs as 3,337 requests for $0.385 in 98 seconds.

## How to run

```
cp .env.example .env
```

Set `TYPESAFE_API_KEY` in `.env`. A fresh run needs the session's written questions and replies fetched first (a rerun fetches only what's missing, and needs no key):

```
uv run python -m jev_stortinget fetch
```

and the session's own `asks_*` classification (step 1 of the diagram above, `out/all/results.jsonl`), which decides which pairs even enter the tree:

```
uv run --env-file .env python -m jev_stortinget run --set all
```

Then build the dodge tree and write `results/tree/`:

```
uv run --env-file .env python -m jev_stortinget tree
```

The real run behind the numbers above is `out/tree-all.json`, kept on disk but gitignored like the rest of `out/`. Rebuilding `results/tree/` from it, without spending anything on Jev, is:

```
uv run python -m jev_stortinget tree --from out/tree-all.json
```

## Data and licence

Source: [data.stortinget.no](https://data.stortinget.no/), the Storting's own open data service. The data is free for anyone to use under the Norwegian Licence for Open Government Data (NLOD), provided Stortinget is credited as the source; the terms are at [data.stortinget.no/om-datatjenesten/bruksvilkar](https://data.stortinget.no/om-datatjenesten/bruksvilkar/).

Two endpoints, both open JSON with no login. `eksport/skriftligesporsmal?sesjonid=2024-2025&format=json` lists a session's written questions; `eksport/enkeltsporsmal?NSporsmalId=<id>&format=json` returns one question and its reply. The parameter has to be `NSporsmalId`; the similarly named `sporsmalid` redirects and often fails. The service rate-limits in bursts, usually without a `Retry-After` header, so `fetch.py` keeps at most 2 requests in flight, paces them, and on a 429 or 5xx response backs off for 15, 30, 60 then 120 seconds rather than retrying immediately.

The full session's question and reply texts are not committed here (`data/raw/` is git-ignored), so `fetch` is needed before a fresh run. `results/tree/` is committed. `tree-2024-2025.json` keeps only ids, paragraph indices and values, never any reply text; `no-data.json` and `promised-later.json` each carry one short quote per pair, the sentence that carries the reason or, failing that, the paragraph's first 300 characters, not the whole reply. So the numbers above are checkable without fetching anything.
