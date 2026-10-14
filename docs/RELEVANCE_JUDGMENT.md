# Relevance judgment: the judge reads, it does not discard

Since 0.4.13 the document judge guides the collection. What it returns for a
page is a **reading** (`ghimera.models.DocumentReading`; `Verdict` names the
same record, so ledgers written before 0.4.12 load unchanged). Nothing in a
reading removes a page: every fetched and extracted page is kept in the corpus,
the graph input, the receipt and the ledger.

## What a reading carries

| field | meaning |
|---|---|
| `relevance` | the grade, below |
| `subject` | the subject of the intent, in one phrase, that the page was graded against |
| `informs` | ids of the research plan's sub-questions the page helps answer |
| `contribution` | one sentence: what the page says about the subject |
| `follow` | up to 8 leads: `query` (a person, outlet, account, hashtag or event worth a search) or `link` (an absolute URL on the page worth fetching) |
| `names` | up to 16 people, organisations, accounts and claims, each with its kind |
| `decision` | `accept` (read and graded) or `hold` (the excerpt was too partial to grade; a wider second look follows) |

## The grades

- `direct`: the page answers part of the intent.
- `supporting`: the page is about the same subject (the event, the actors,
  the reactions or the context the intent names), even if it does not answer
  the question as worded.
- `background`: related material useful for the graph or the brief.
- `unrelated`: about something else entirely.
- `unjudged`: no reading was possible because the served judge failed or was
  unavailable. Only the client sets this grade; the model is never offered it.

The judge is told to name the intent's subject first and grade against the
subject, not the literal question. The prompt uses the case that prompted the
change as its example. For "What are influencers saying about the PRC reaction
to the 10/10 speech by the Taiwanese President?", Focus Taiwan's "Lai's National
Day speech steady, avoided provoking Beijing: Scholar" is `supporting`: a
scholar is not an influencer, but the page is about the speech and the
reaction. A page about engagement-rate formulas is `unrelated`.

## How the client turns a reply into a kept page

`ghimera.judgment_types.reading_disposition` is the one rule. The loop and
ledger validation both use it:

1. A model `hold` gets the second, wider look. If the second look also holds,
   the page is kept as `background` ("undecided after the second look").
2. A model `unrelated` grade (or a legacy `reject`, which reads as
   `unrelated`) on a page whose intent cosine is at or above
   `document_judgment.keep_at_or_above` is kept as `background`, with the
   reason "kept by similarity floor F at C (model said: ...)".
3. Below the floor, an `unrelated` grade on a partial first look (text was
   omitted) gets the second look first. After that the page is kept as
   `unrelated`.
4. Every other reading keeps the page with the model's grade.

A served judge that fails for any reason (`served_judge_failed`, including
`model_unavailable` and `bound_exceeded`) keeps the page as `unjudged`, and the
round continues. Only a core or adapter contract failure outside the judge
call still fails the run.
A link lead is fetched only if the page really carries that link. A page
graded `unrelated` stays in the corpus, but its document references do not
seed reference expansion.

The model's reply is kept verbatim: the ledger's `verdict` row reads
`<decision>: <reason>` and the model-work ACK stores the exact returned record.
The client's grade, the page's `document_cosine` and the floor are recorded on
the judgment evidence (`client_relevance`, `document_cosine`,
`keep_at_or_above`, `reason`). A kept page whose grade the client changed also
gets a `policy` row, `reading_kept: <grade>: <reason>`.

## The floor and its default

`keep_at_or_above` is an intent cosine (the scoring row's `document_cosine`,
the best scored native window). The default is **0.80**. The only on-topic
scoring observation recorded in the test fixtures
(`tests/fixtures/retained_organization_judgment.json`) is at 0.88, and
e5/bge-family encoders put plainly unrelated text in the 0.70s. A floor of 0.80
only relabels a page the encoder already reads as close to the intent. The floor
never removes a page, since nothing does. It only decides whether a close page
the model called unrelated is graded `background` (reported as related) or
`unrelated`. Set it in the recipe (see
[`examples/source_first_document_judgment.toml`](../examples/source_first_document_judgment.toml));
1.0 effectively turns it off.

## What the grade feeds

- **Assessment, answer and review** use pages in grade order: `direct`, then
  `supporting`, `background` and `unjudged`. `unrelated` pages are left out.
  A legacy page without a grade counts as `supporting`. The order is stable,
  so a run of legacy pages keeps its original order.
- **The next plan** receives `PlanningRequest.readings`: for up to 32 kept
  pages, the grade, the sub-questions informed, the contribution, the query
  leads and the names. The planner prompt says to steer toward questions no
  reading informs and toward the suggested people, outlets, accounts and
  events. The judge sees the plan's current sub-questions, so `informs` names
  real question ids.
- **The frontier** receives a reading's `link` leads that appear on the page.
- **Round reports** (`ResearchRound`) add `pages_by_grade`,
  `informed_questions` (question id to the number of the round's kept pages
  whose reading informs it) and `followed_leads`.
- **The corpus and the graph** take every kept page whatever its grade.
