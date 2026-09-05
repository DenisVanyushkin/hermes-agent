# Job-Intel Search: Planning Addendum

Version: `1.0.0`
Status: planning authority, not a product gate
Date: 2026-09-05
Authors: Claude and Codex, jointly reviewed
Supersedes: nothing. Amends nothing.

This addendum exists because the planning vocabulary in the job-intel documents
has one word — "search" — for three states that have different owners,
different acceptance criteria, and different costs. Naming them separately is
the whole point of this file.

---

## 1. Why this document exists

The approved product route for job-intel is
[`docs/superpowers/specs/2026-08-10-job-intel-search-product-redesign-design.md`](superpowers/specs/2026-08-10-job-intel-search-product-redesign-design.md)
(`v1.0.0`, Approved, SHA-256
`430340de2613ee733926d73ce276c93676fe64b1841bb2f68f3f9303b61fc3a8`) and its
implementation plan
[`docs/superpowers/plans/2026-08-10-job-intel-search-product-redesign.md`](superpowers/plans/2026-08-10-job-intel-search-product-redesign.md),
whose phase gates run Gate A through Gate E.

That route is not in dispute and this addendum does not narrow it. What this
addendum adds is a named, versioned, explicitly non-gate route for producing
value from the corpus that already exists, so that such work cannot later be
misread as gate evidence.

The failure this prevents is specific and has already happened once in this
project: an artifact that is internally valid but misstates what it is,
producing a claim about the system that the artifact never checked.

---

## 2. Three states, named separately

These are not stages of one thing. Each has its own acceptance criterion and
its own decision owner. A claim about one is never a claim about another.

| # | State | What it means | Who decides it is reached |
|---|---|---|---|
| 1 | **First manual release** | A human, using existing stored data plus manual verification, produces a shortlist a human can act on. | Owner, by confirming at least one useful opportunity or a meaningful next step. |
| 2 | **Reproducible assisted workflow** | The same result can be produced again by following a written procedure, with dedup across releases, recorded misses, and recorded reasons. | Owner, after a second release produced by the written procedure. |
| 3 | **Autonomous service** | The system produces and delivers the result without a human in the loop. | The approved Gate C, Gate D, and Gate E sequence. Nothing else. |

State 1 does not imply state 2. State 2 does not imply state 3, and does not
shorten the gate sequence by one step.

---

## 3. The `manual_shortlist_route`

Route id: `manual_shortlist_route`
Route version: `1.0.0`
Route status: `specified; execution not started`
Gate status: **none — this route has no gate and cannot acquire one**

### 3.1 What the route is

A bounded, human-authored review of vacancies already present in the canonical
database, producing a read-only artifact and a human decision.

### 3.2 What the route may do

- Read the canonical database `mode=ro` with `PRAGMA query_only=ON`.
- Read stored raw payloads to establish text lineage.
- Re-check a candidate's official employer page for current status and for any
  claim carried into the artifact.
- Apply the six product dimensions by human judgement.
- Write a review artifact under `/home/hermes/.hermes/job_intel/manual-shortlist/<release-id>/`, outside the public repository. Publish only a redacted process/evidence summary to `docs/evidence/manual-shortlist/` when requested.
- Record `unknown` as a first-class value.

### 3.3 What the route may not do

- Emit or impersonate a machine `SystemVerdict`, `SelectionMode`,
  `RecommendedActionKind`, or other Decision v2 output. Clearly attributed
  human interpretation, suggested next steps, and Denis's decisions are allowed;
  they must not be labelled machine decisions or written into product state.
- Write to the canonical database, or to any production table.
- Deliver through any production Slack channel.
- Be cited as evidence for Gate A, Gate B, Gate C, Gate D, or Gate E.
- Be cited as evidence of market coverage, of source breadth, or of the
  representativeness of the corpus it read.
- Change source policy, preference policy, or any pinned authority document.

### 3.4 Artifact self-description requirement

Every artifact produced by this route states, in its own header, that it was
produced by `manual_shortlist_route` version `1.0.0`, that it is not gate
evidence, and that it carries no machine verdict. An artifact that does not
state this is not a product of this route regardless of how it was made.

The rule exists because a correct artifact with a false provenance line is
worse than a missing artifact: it is trusted and wrong.

---

## 4. Policy constraints this route inherits and must not relax

These are read from existing authority, not decided here.

1. **Compensation is not a selection criterion.**
   `job_intel/preference_model/career-preference-model.yaml`, block
   `compensation_policy`: `status: inactive`, `gating_effect: false`,
   `ranking_effect: false`, `missing_salary_is_negative: false`. Missing or low
   compensation must not influence gating or ranking until an explicit SoT
   change introduces a compensation floor. Product SoT §5.7 keeps compensation
   `unknown`.

2. **Timezone is not an automatic rejection reason** and may be recorded only
   as a risk or clarification.

3. **Title match is not fit.** A matching title is a retrieval signal, not a
   product conclusion.

4. **Text length is not sufficiency.** `length(description) > 500` is a
   diagnostic proxy. It is not evidence that mandate or feasibility can be
   assessed.

5. **`last_seen_at` is an observation time**, not a publication time and not a
   confirmation that hiring is open.

---

## 5. Text lineage: what is known and what is not

Text provenance in the canonical database is partially recoverable, and the
recoverable fraction is measured rather than assumed.

Two backfill paths update a vacancy description and only one of them writes
the dedicated persistent backfill state; source fetchers also supply descriptions:

- `job_intel/cli.py:_apply_text_backfill` fills `description` in memory before
  the row is stored, ahead of classification, with a per-run budget of `400`
  from `JOB_INTEL_TEXT_BACKFILL_BUDGET`. It does **not** write
  `text_backfill_state`.
- `job_intel/store.py:844–849` fills stored rows and does write
  `text_backfill_state`, selecting rows where that state is `NULL` or
  `'failed'`.

Consequence: a `NULL` in `text_backfill_state` does **not** mean "not
attempted". It means this particular accounting is silent. Any future backfill
accounting must distinguish raw source text, fetched detail, failed,
unavailable, not attempted, and budget-skipped, rather than collapsing all of
them into one nullable column.

Measured lineage on rows with `last_seen_at >= '2026-09-03'` and
`length(description) > 500`, by re-normalising stored raw payload fields with
the fetchers' own rules (`re.sub(r"<[^>]+>", " ")` then
`re.sub(r"\s+", " ").strip()`) and requiring byte equality with the persisted
description:

| Source | Rows with text | Lineage recovered by this method |
|---|---:|---:|
| greenhouse | 2312 | 2312 |
| ashby | 476 | 476 |
| lever | 89 | 89 |
| smartrecruiters | 441 | 0 |
| teamtailor | 55 | 0 |
| headhunter | 43 | 0 |
| recruitee | 13 | 0 |

Recovered: 2877 of 3429. Not recovered by this method: 552.

Three limits on that number, stated because the number is easy to over-read:

- This is **stored-lineage consistency**, not independent HTTP capture. It
  shows the persisted text is the normalisation of the payload stored beside
  it. It says nothing about whether the vacancy is still open.
- The 552 are **not recovered by this method**, which is not the same as
  principally unrecoverable. The scan walked string fields to depth three and
  compared single strings; composite or reassembled fields would not match.
- The absence of any matching raw field for all 441 SmartRecruiters rows, taken
  together with `job_intel/ats_sources.py:690` passing no `description=`
  argument at all, makes the in-memory backfill the only remaining producer on
  the daily path for those rows. This is an **inference**, not a proof, and is
  recorded as such.

Because lineage is not independent evidence of current status, official-page
re-check is mandatory for **every** shortlisted role, whether or not its
lineage was recovered. For the not-recovered set it additionally provides new
cited evidence; it does not retroactively prove who wrote the old database text.

---

## 6. Relationship to the approved gate sequence

| Statement | True? |
|---|---|
| This route shortens Gate C, D, or E | No |
| This route substitutes for Decision v2 | No |
| This route produces evidence usable at any gate | No |
| This route may run while gates are open | Yes |
| Output of this route may inform which repair is worth doing first | Yes |

Gate A was reopened. `docs/evidence/product-search-gate-a/2026-08-26-p0-authorization.md`
records `gate_a_reopened: true` and `prior_product_claim_superseded: true`. The
historical `proceed` of 2026-08-16 in `gate-closure.json` is superseded and must
not be cited as a current product claim.

The retired v3 launch protocol is not revived by this addendum. Future Gate B
work uses the current supervised runner, a reviewed corpus, and the current
spend contract.

---

## 7. Amendment rule

This addendum is versioned. Changing any constraint in section 3.3, or any
policy in section 4, requires a version increment and a recorded owner
decision. Adding measurements to section 5 does not.
