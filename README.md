# jev-stortinget

**Part of aotn**, a series of small educational example projects that go with the article "The classifier you don't have to train", about TypeSafe's Jev. Each project gives Jev a different kind of text, asks it narrow typed questions, and lets plain code make every decision. Each one then measures itself against an answer key it did not tune on, and says where it falls short.

The code is here to learn from, not to run in production. Each project shows one pattern in a form small enough to read in one sitting. The rules are illustrative, and none of it is tax, engineering or political advice.

- [aotn-jev-invoices](https://github.com/tameernoor/aotn-jev-invoices): receipts and supplier invoices, VAT codes and approval
- [aotn-jev-turbine-triage](https://github.com/tameernoor/aotn-jev-turbine-triage): a year of real wind turbine alarms, triaged and checked against the operator's own labels
- [aotn-jev-stortinget](https://github.com/tameernoor/aotn-jev-stortinget): did the minister answer the question? A full session of the Norwegian parliament

A small example in the aotn series. It reads every written question a member of the
Storting put to a government minister in the 2024-2025 session, and the minister's
reply, and asks Jev 18 literal yes/no questions about the two texts. Plain code
combines the answers into one of six outcomes: answered, premise_corrected,
deferred, pointed_elsewhere, not_answered or unclear. The result ranks ministries by
how often their replies leave a question unanswered, with the reply text behind
every judgment. The judgment is checked against 60 held-out pairs labelled by an
independent reader, a separate AI model session that saw only the label
definitions and the two texts, never Jev's questions, its answers or this
project's code, and that labelled from the definitions alone before any Jev call
on that set.

## What it shows

Every member of the Storting can put a written question to a government minister,
and every minister must reply. The Storting's own bookkeeping marks all 3,234
written questions from the 2024-2025 session as answered, meaning a reply was
filed for each one; replies run from a couple of sentences to 7,066 words, a median
of 270. Filing a reply is not the same as answering what was asked, and that gap is
what this project measures.

For every pair, Jev sees two things only, the question's own Norwegian text and the
reply's own Norwegian text, never the MP's reasoning paragraph, the ministry or any
label, and answers 18 literal yes/no questions about them in one request: seven
about what kind of thing the question asks for (an amount, a time, a plain yes or
no, an action, a reason, the minister's own view, or a factual account), seven about
whether the reply gives that same kind of thing, three escape questions (did the
reply correct a fact in the question, defer the matter, or point to someone else),
and one shadow question that asks the single broad thing directly and is never read
by the rules. Plain code, not Jev, turns those 18 answers into an outcome, never a
guess when the read that would decide it comes back hesitant.

Once every pair has an outcome, it is grouped by the minister who actually answered,
since 280 of 3,234 pairs, about 9 %, are transferred to a different minister than the one the
question was addressed to before it gets a reply, and each ministry's share of
decided pairs that are deferred, pointed elsewhere or not answered is reported, with
unclear counted separately, never folded into either side. Two lessons carried over
from the sibling turbine-triage project shaped the design before any Jev call was
made: literal questions with code doing the combining, and an uncertain read that
goes to unclear rather than a guess.

## Data and licence

Source: [data.stortinget.no](https://data.stortinget.no/), the Storting's own open
data service. The data is free for anyone to use, provided Stortinget is credited
as the source; the terms are at
[data.stortinget.no/om-datatjenesten/bruksvilkar](https://data.stortinget.no/om-datatjenesten/bruksvilkar/).

Two endpoints, both open JSON with no login. `eksport/skriftligesporsmal?sesjonid=2024-2025&format=json`
lists a session's written questions; `eksport/enkeltsporsmal?NSporsmalId=<id>&format=json`
returns one question and its reply. The parameter has to be `NSporsmalId`; the
similarly named `sporsmalid` redirects and often fails. The service rate-limits in
bursts, usually without a `Retry-After` header, so `fetch.py` keeps at most 2
requests in flight, paces them, and on a 429 or 5xx response backs off for 15, 30,
60 then 120 seconds rather than retrying immediately.

The full session's question and reply texts are not committed here (`data/raw/` is
git-ignored). The drawn dev and holdout sets, with their question and reply text
already extracted, are committed (`data/sets/`), but `run` still reads its records
from `data/raw/`, so a fetch is needed first, even to re-run dev or holdout.

## How to fetch and run

```
cp .env.example .env
```

Set `TYPESAFE_API_KEY` in `.env`. Fetch the session's written questions and replies
into `data/raw/2024-2025/` (a rerun fetches only what is missing, and needs no key):

```
uv run python -m jev_stortinget fetch
```

This step is required because the question and reply texts are not committed to the
repository; only the drawn dev and holdout sets (`data/sets/dev.json`,
`data/sets/holdout.json`), already extracted, are. `python -m jev_stortinget sets`
redraws those two sets from the fetched cache with a fixed seed, so it reproduces
the same two id lists rather than changing them; both are already committed and do
not need to be redrawn to run against.

Ask Jev about a set and let the rules decide each outcome. This needs
`TYPESAFE_API_KEY` in the environment, and only once a pair actually has to be
asked; a pair already in the cache costs nothing and needs no key:

```
uv run --env-file .env python -m jev_stortinget run --set dev
uv run --env-file .env python -m jev_stortinget run --set holdout
uv run --env-file .env python -m jev_stortinget run --set all
```

`--set all` runs every fetched pair, dev and holdout included, and also writes
`out/all/ranking.json` and prints the ministry table. Each run writes
`out/<set>/results.jsonl` (one row per pair, with the outcome, the reasons, and all
18 values) and `out/<set>/summary.json`; a set with a labels file also gets
`out/<set>/evaluation.json`.

To reproduce `## Measured` below without spending anything on Jev, once the texts
are fetched:

```
uv run python -m jev_stortinget run --set all --cache results/judgments-2024-2025.json
```

`--cache FILE` seeds the run from a committed judgments cache instead of
`out/judgments.json`, without writing back to `FILE` itself; every pair already in
`FILE` costs nothing, so seeding from the full session's cache asks Jev nothing at
all and needs no key.

## The questions

Written as `questions/pair.yaml`. All 18 are `noul` (yes/no), sent in one request
per pair.

| id | looks at | asks, in one line |
| --- | --- | --- |
| `asks_amount` | question | Does the question ask how much, how many or how large? |
| `asks_time` | question | Does the question ask when, or by when? |
| `asks_yes_or_no` | question | Would "ja" or "nei" be a complete answer? |
| `asks_action` | question | Does the question ask what the minister will do, or how they will ensure something? |
| `asks_why` | question | Does the question ask for a reason or cause? |
| `asks_assessment` | question | Does the question ask for the minister's own view? |
| `asks_facts` | question | Does the question ask for facts, a status, a list or an overview? |
| `states_amount` | reply | Does the reply give a figure or bounded estimate for the quantity asked? |
| `states_time` | reply | Does the reply give a date, season, session or year for the thing asked? |
| `says_yes_or_no` | reply | Does the reply affirm or deny the thing asked, in words or in substance? |
| `names_action` | reply | Does the reply name a concrete measure taken, decided or to be taken? |
| `gives_reason` | reply | Does the reply state a cause or reason for the thing asked why? |
| `states_position` | reply | Does the reply state the minister's own view on the matter asked? |
| `gives_facts` | reply | Does the reply give the facts, status, list or overview asked for? |
| `corrects_premise` | reply | Does the reply say a factual claim in the question is wrong? |
| `defers` | reply | Does the reply say the thing asked will be considered or decided later? |
| `points_elsewhere` | reply | Does the reply say another body is responsible for the thing asked? |
| `gives_what_is_asked` | both, shadow | Does the reply give the specific thing the question asks for? |

## Why these questions

TypeSafe's own documentation, at docs.typesafe.ai, describes the general
difference between a `choice` and a set of `noul`s, which is the reasoning this
design applied to rule out a single seven-option `choice` over "what does the
question ask for", even though it looks at first like a natural fit.

> A Choice over options and one Noul per option answer different questions: the
> Choice is relative, settling which option, while each Noul is absolute and can be
> low for all of them.
> (https://docs.typesafe.ai/model-jaggedness/jev-1.13)

A crude regex looking for a second question joined by "og" or "eller" fires on 35 %
of the prior session's titles: a good third of written questions ask two things in
one sentence, sometimes three. A `choice` can only settle which one thing was
asked; seven `asks_*` nouls can all read yes on the same compound question, and the
rules take an OR over whichever ones fired.

The single broad "did the reply answer the question" judgment survives only as a
shadow, `gives_what_is_asked`, written to every result and never read by the rules.

> Ask every question your code might need, including ones whose answer only matters
> for some inputs, and let the code decide which answers to use.
> (https://docs.typesafe.ai/primitives)

That is the same shape TypeSafe describes for speculative questions, and it costs a
few tokens on top of the seventeen the rules actually read. It is the kind of gut
check a reader could give in a few seconds,

> the kind of judgment a highly knowledgeable person could make in a few seconds
> given the right context
> (https://docs.typesafe.ai/introduction)

against a question that, a third of the time, is really two or three questions at
once. The sibling turbine-triage project already measured this trade on a different
kind of text: its one broad question scored higher on raw agreement than its five
literal ones, and was confidently wrong three times as often. The 60-pair holdout
below repeats that comparison on Norwegian ministerial replies, though the shadow
and the decomposition's reply side were not scored on the same threshold; the
shadow kept the untuned 0.8 yes cut, the reply side used the 0.65 cut chosen on
dev. At the untuned 0.8 the shadow matched the reader on 18 of 60 (40 unclear, 2
confidently wrong); at the same 0.65 yes cut it would match on 31 of 60. The
17-question outcome matched on 36 of 60 (18 unclear, 6 confidently wrong). The
decomposition decides more pairs and gets more of them right, while the single
question hedges more and makes fewer confident mistakes (see Measured).

Norwegian carries the two texts Jev reads, `question` and `reply`; English carries
every instruction and criterion.

> English is the primary training language and where accuracy is currently best.
> Other languages, including CJK scripts, are handled but not equally well; test on
> your own content before relying on Jev for a non-English workload.
> (https://docs.typesafe.ai/models)

The criteria quote the Norwegian bokmål and nynorsk phrases a reader would look for
on each side of the yes/no boundary, which is TypeSafe's own remedy for a subtle
boundary, but the questions and reply text were never compared against English
translations of the same texts before this was used for real.

## Rules

A noul at or above 0.8 counts as yes, at or below 0.2 as no; anything between is
uncertain. The seven reply-side nouls (paired one-to-one with an `asks_*` type) use
0.65 as their yes threshold instead of 0.8; see the dev-round change below. In
order:

1. A confident `corrects_premise` (>= 0.8) wins outright: `premise_corrected`,
   whatever else the question asks.
2. What the question asks for is read from the seven `asks_*` nouls. Every
   confident yes counts; if none is confident, the uncertain ones are used instead,
   so one hesitant question-side read does not end the pair by itself. If none is
   even uncertain, the pair is `unclear` ("no ask type recognised").
3. The reply is `answered` if it confidently gives any one of the things the
   question asked for. This is an OR: one confident yes among the asked types is
   enough, and every one of them has to be a confident no before the reply can fail
   to answer.
4. If nothing was confidently given, but a reply-side read on one of the asked
   types was itself uncertain, the pair is `unclear` ("reply uncertain on ...").
5. If nothing asked was given and nothing there was uncertain either, an uncertain
   `corrects_premise` now makes the pair `unclear` too, since it is the read still
   deciding the outcome.
6. Past that, `defers` is read before `points_elsewhere`: a confident yes on either
   gives `deferred` or `pointed_elsewhere`, and an uncertain read on either (checked
   in that same order) gives `unclear`.
7. Nothing asked was given, nothing deferred, nothing pointed elsewhere:
   `not_answered`.

The dev round changed one number. On the 20 dev pairs, with 0.8 as the yes
threshold everywhere, Jev's reply-side answers for replies the independent reader
called answered mostly sat between 0.6 and 0.76, short of 0.8 without being
genuinely uncertain. Lowering the yes threshold to 0.65 for the seven reply-side
nouls only (`states_amount`, `states_time`, `says_yes_or_no`, `names_action`,
`gives_reason`, `states_position`, `gives_facts`) raised agreement with the reader
from 7 of 20 to 11 of 20, without raising the count of pairs wrongly called
answered, which stayed at 1. The seven `asks_*` nouls and the three escape nouls
kept 0.8. This is the one rule change made after seeing Jev's answers, and it was
made on the dev set alone, before the holdout pairs were ever sent.

## How the evaluation stays honest

The dev and holdout ids were drawn once: every cached pair with a non-empty reply,
sorted by id, then `random.Random(20250928)` drew 80 of them. The first 20 draws
are dev, the next 60 are holdout, so the two sets never overlap.

An independent reader, a separate AI model session that saw only the label
definitions and the two texts, labelled every holdout pair from the label
definitions alone, never from Jev's questions, Jev's answers or this project's
code. Those holdout labels were committed at `e7d6915`, before this project's code
could make a single call to Jev; the dev labels followed, committed at `70c7113`.
On 34 of the 60 holdout pairs the reader was torn between two labels and recorded
that second choice alongside the label actually given.

The only rule change made after seeing Jev's answers is the reply-side threshold
described above, and it was made once, on the dev round only, before the holdout
pairs were ever run.

## Measured

Three real runs against `jev-1.13.0`, with the questions frozen before any of them
and the reply-side threshold frozen before the holdout run. The raw answers are in
`results/judgments-2024-2025.json`, and the run summaries and evaluations are the
rest of `results/*.json` (see `results/README.md`), so every number below
reproduces without spending anything on Jev.

### Dev round

20 pairs, $0.0035, 0.7 seconds. With the design's 0.8 threshold everywhere, Jev's
outcome matched the reader's label on 7 of the 20 pairs, and 9 were unclear. Jev's
reply-side answers, for replies the reader called answered, mostly sat between 0.6
and 0.76. Lowering the reply-side yes threshold to 0.65 raised that to 11 of 20
matching (13 of 20 counting the reader's second choice), left 5 unclear, and did
not raise the count of pairs wrongly called answered, which stayed at 1. That
threshold move is the only change made after seeing Jev's answers, and it was made
on dev only.

### Holdout

60 pairs, run once after the questions and thresholds were frozen: $0.0104, 8.1
seconds.

- 34 of 60 exact agreement with the reader's label (57 %); 38 of 60 (63 %) counting
  the reader's second choice; 18 unclear.
- Where the rules reached a decision at all (42 of 60 pairs), 34 were right (81 %).
- Agreement per reader label:

| label | agreement |
| --- | --- |
| answered | 30 / 42 (71 %) |
| pointed_elsewhere | 3 / 6 (50 %) |
| deferred | 1 / 6 (17 %) |
| premise_corrected | 0 / 2 (0 %) |
| not_answered | 0 / 4 (0 %) |

- Confusion matrix, reader label against Jev's outcome:

| reader label (n) | answered | unclear | premise_corrected | deferred | pointed_elsewhere |
| --- | --- | --- | --- | --- | --- |
| answered (42) | 30 | 10 | 1 | 1 | 0 |
| deferred (6) | 2 | 3 | 0 | 1 | 0 |
| pointed_elsewhere (6) | 1 | 2 | 0 | 0 | 3 |
| not_answered (4) | 2 | 2 | 0 | 0 | 0 |
| premise_corrected (2) | 1 | 1 | 0 | 0 | 0 |

- Scored as answered-or-not only: the 17-question outcome matched the reader on 36
  of 60 (60 %); the single gut-check shadow question, on the same 60 pairs, matched
  on only 18 of 60 (30 %).

### Full session

All 3,234 pairs in the 2024-2025 session. 80 were already cached from the dev and
holdout rounds, so this run made 3,154 new calls, 13,133,047 input tokens, $0.55,
113 seconds.

| outcome | count |
| --- | --- |
| answered | 1,683 |
| unclear | 1,113 |
| deferred | 177 |
| premise_corrected | 119 |
| pointed_elsewhere | 101 |
| not_answered | 41 |

Of the 1,113 unclear pairs, 851 are "reply uncertain": a reply-side noul for one of
the asked types landed between 0.2 and 0.65. By what was asked (a pair can count
under more than one type): yes_or_no 350, facts 259, action 168, assessment 148,
why 59, amount 58, time 27. That is likely because ministers rarely write "ja" or
"nei" outright, which would account for most of why yes_or_no leads. The rest of
the unclear pile is an uncertain escape
read: points_elsewhere uncertain 91, corrects_premise uncertain 87, defers
uncertain 84.

### Ranking

From `results/ranking-2024-2025.json`, all 20 ministries who answered at least one
question, by unanswered share (deferred, pointed_elsewhere and not_answered over
decided pairs; unclear reported beside it, never counted either way):

| ministry | n | unanswered | unclear |
| --- | --- | --- | --- |
| utviklingsministeren | 23 | 31.6 % | 4 |
| næringsministeren | 131 | 25.6 % | 53 |
| forsvarsministeren | 141 | 20.0 % | 61 |
| justis- og beredskapsministeren | 333 | 19.6 % | 124 |
| klima- og miljøministeren | 180 | 19.0 % | 59 |
| finansministeren | 259 | 17.2 % | 90 |
| kultur- og likestillingsministeren | 102 | 16.9 % | 19 |
| forsknings- og høyere utdanningsministeren | 74 | 16.0 % | 24 |
| energiministeren | 265 | 15.6 % | 92 |
| helse- og omsorgsministeren | 440 | 14.6 % | 186 |
| kommunal- og distriktsministeren | 151 | 14.0 % | 44 |
| arbeids- og inkluderingsministeren | 127 | 12.9 % | 42 |
| fiskeri- og havministeren | 120 | 12.8 % | 42 |
| statsministeren | 13 | 12.5 % | 5 |
| kunnskapsministeren | 139 | 12.4 % | 42 |
| samferdselsministeren | 428 | 11.6 % | 136 |
| digitaliserings- og forvaltningsministeren | 56 | 9.5 % | 14 |
| utenriksministeren | 97 | 8.5 % | 38 |
| barne- og familieministeren | 73 | 5.7 % | 20 |
| landbruks- og matministeren | 82 | 4.7 % | 18 |

At 57 % exact agreement on holdout and a third of the full session left unclear,
this ranking is a demonstration of the method, not a verdict on any minister.
Several of the ns are small (utviklingsministeren 23, statsministeren 13), and a
share built from 13 or 23 pairs moves a lot on one or two reclassified replies.

## What this shows

Decomposing the single "did the reply answer" judgment into seven typed asks and
seven typed reply-nouls, combined with an OR in code, decided more pairs and got
more of them right against an independent reader than the single gut-check shadow
question sent alongside it, though the two were not scored on the same threshold.
The shadow, at the untuned 0.8 yes cut, matched 18 of 60; the decomposition's
binary outcome matched 36 of 60. At the same 0.65 cut used for the reply side, the
shadow would match 31 of 60, still fewer than the decomposition, and with more of
its answers landing unclear rather than confidently wrong (see "Why these
questions"). That gap is consistent with what the sibling turbine-triage project
saw between its five literal questions and its three broad ones, and the reason is
the same one the design work found before any pair was sent to Jev: a third of
these written questions ask more than one thing in a single sentence, which is
exactly the shape a single broad question answers worst.

The cost of that gap is size. Exact agreement with the reader is 57 %, not a number
to build a public ranking on by itself, and a third of the full session comes back
unclear rather than a guess. That is the trade the rules are built to make: an
uncertain reply-side read on the type actually asked about sends the pair to a
person instead of picking a side, and it does that on 851 of the 3,234 pairs. A
lower reply-side threshold pushed further would have called more of those answered
without knowing better; 0.65 is where the dev round's 20 pairs stopped supporting
the move.

## Limits

One held-out reply ends mid-sentence, "... følgende statistikk:" ("... the
following statistics:"), and nothing after it. The API only carries the reply
text; not one of the 3,234 replies in this session contains an actual table. A
minister who answers with an attached table rather than words reads here as a
non-answer, not because the reply failed to answer but because the text this
project can see stops before the numbers do.

The two texts are Norwegian, in both bokmål and nynorsk, sent to a model whose own
documentation says English is its strongest language and other languages are
"handled but not equally well". The criteria quote the Norwegian phrases a reader
would look for on each side of the yes/no boundary, but that is a partial remedy,
not a fix, and the questions and reply text were never compared against English
translations of the same texts.

The OR rule that decides `answered` treats a compound question as answered once
any one of its parts is given, which matches the label definitions ("the reply
gives what the question asks for") but not necessarily a reader's felt sense of a
question that got mostly ignored. A question asking both when and how much,
answered only on the date and silent on the money, comes out `answered` here. A
reader who weighed the unanswered part more heavily would disagree with some of
these outcomes even though the rules and the label definitions agree.

The 0.65 reply-side threshold was tuned against 20 dev pairs, a small sample to set
a number that then ran unchanged over 3,234.

## results/

`results/` holds the artifacts behind `## Measured`: the full session's judgments
cache, the dev and holdout evaluations against the independent reader's labels, and
the ministry ranking, placed here so the numbers above reproduce without spending
anything on Jev. See `results/README.md`.
