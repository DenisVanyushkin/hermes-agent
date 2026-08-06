# §7.2 — round 2 results: duty-scoped extraction, first trustworthy numbers

**Date:** 2026-08-06 · deterministic provider · $0
**Landed on `local/customizations`:** `6c758bce0f` (duty scoping, WIP),
`391c8683e8` (duty-filter recall holes), `d1e1e4c273` (apostrophe folding).

Round 1's 40.7% headline was withdrawn because recall was bought with false
positives. Round 2 puts the duty-sentence discipline into the provider,
repairs three defects that discipline exposed, and — the part round 1
skipped — reads the firings back against the text that produced them.

## Headline

| Slice | Population | Baseline | Round 1 (withdrawn) | **Round 2** |
|---|---|---|---|---|
| DEV (2956) | all eligible | 6.90% | 40.73% | **20.77%** |
| HOLDOUT (1212) | all eligible | 6.60% | 41.75% | **20.30%** |
| DEV (52) | **target roles** | — | — | **63.46%** |
| HOLDOUT (22) | **target roles** | — | — | **68.18%** |

The corpus-wide number fell by half against round 1, as predicted. It is now
worth reading. DEV and HOLDOUT agree to within 0.5 pp corpus-wide and 4.7 pp
on target roles, so the split still shows no overfit.

**The target-role rate is the number §7.2 is about.** The eligible corpus is
overwhelmingly non-target roles (52 of 2956 in DEV), for which a mandate fact
is mostly meaningless. `coverage_report(target_only=True)` was added for
exactly this reason.

## Precision: sampled and read, not assumed

Round 1's failure was reporting a rate without reading firings. Both broad
facts were sampled and judged manually.

**`team_build_mandate`, target roles, 12 sampled of 16 firings — 11 true.**
The one miss is descriptive, not a duty: *"work with a talented and growing
team of product managers"* (Director PM, Security, Datadog). Every firing
round 1 was withdrawn for — DEI boilerplate, company-growth prose — is gone.

**`strategy_ownership`, target roles, 15 sampled of 39 firings — ~12 true.**
The duty filter is doing its job here: the sentences genuinely assign the
duty. What is wrong is the *fact*: it fires on any strategy, not on product
or business strategy. See the open question below.

**Corpus-wide the same rules are much weaker**, which is consistent with the
target-only framing: on all 4168 rows, `strategy_ownership` fires on benefits
programmes (Vercel), search (Adyen), regional events (Grafana) and technical
sales (Airwallex). Those are true sentences and false mandates.

## Three defects the duty filter itself had

Duty scoping broke 5 positive synthetic controls. Each gate of
`responsibility_sentences()` was instrumented separately rather than guessed
at. None of the five turned out to be a synthetic artefact.

1. **Verb vocabulary gap.** `_DUTY` had no `present`, `prepare` or
   `redesign`. Ordinary executive duty language, silently invisible.
2. **`Label: <duty>` unreachable.** `_DUTY_CONTEXT` anchored only on `^`,
   `you…`, `to ` and `and `, so *"Responsibilities: Own the P&L"* matched
   nothing, and the leading noun of *"Product Lead - Pricing: own …"*
   additionally tripped `_NON_CANDIDATE_SUBJECT` as if "Product" were the
   subject. Gates now also run on the post-label clause; the
   company-description gate still runs on the full sentence first, so a label
   cannot smuggle company prose past it.
3. **Every gate was written with an ASCII apostrophe.** The corpus is scraped
   HTML: `We’re` / `You’ll` with U+2019 appear in 2724 vacancies against 550
   with `'`. So the gates were blind on the majority of the corpus, in both
   directions — `_COMPANY_DESC` missed *"We’re committed to building a diverse
   team …"* (the largest false-positive source in the sample), and
   `_DUTY_CONTEXT` missed *"You’ll own the roadmap …"*, the most canonical duty
   phrasing there is.

Defect 3 is worth remembering as a class: synthetic controls are typed by a
human in an editor, the corpus arrives from HTML. Validation closed on its own
fixtures reproduces the fixtures' orthography as well as their phrasing.

Its effect is visible in the numbers: corpus-wide coverage fell 23.27% →
20.77% (noise removed) while target-role coverage rose 61.54% → 63.46%
(signal added). A precision fix and a recall fix in one character class.

## Per-fact (DEV)

| Fact | all eligible /2956 | target roles /52 |
|---|---|---|
| strategy_ownership | 236 | 27 |
| team_build_mandate | 319 | 11 |
| scope_breadth | 81 | 4 |
| growth_mandate | 102 | 3 |
| pnl_ownership | 13 | 3 |
| revenue_proximity | 8 | 3 |
| executive_exposure | 28 | 1 |
| org_design_mandate | 2 | 1 |
| board_exposure | 2 | 1 |
| pricing_core | 12 | 0 |
| expansion_mandate | 6 | 0 |
| acquiring_core | 2 | 0 |
| monetization_core | 0 | 0 |

(all-eligible column measured before the apostrophe fold; the target column is
post-fold. The ordering of facts is unchanged by it.)

## Open question for the owner — blocks round 2

`mandate.strategy_ownership` currently fires on **any** strategy the candidate
owns. On target roles that is mostly product strategy and mostly right. Two
sampled target-role cases are arguable — *"own the design strategy"* (Director
of Product Design) and *"define and drive the product marketing strategy"*
(Director, Product Marketing) — and one is wrong outright: *"We set strategy
and drive products"* (Director of Product, Growth/AI), where the subject is
the team, not the candidate.

The existing negative fixture *"Senior Director, Enterprise Risk Strategy"*
says a functional strategy does not count. Before round 2 narrows the rule,
the boundary needs to be the owner's, not mine:

- Does owning a **design** or **product-marketing** strategy count as
  `strategy_ownership` for an executive product mandate?
- Should the rule require a product/business object (`product strategy`,
  `business strategy`, `commercial strategy`, `vision and strategy`), or
  should it stay broad and let role scoping carry the filtering?

## Still at or near zero on target roles

`monetization_core`, `acquiring_core`, `expansion_mandate`, `pricing_core` —
0 on target roles. `org_design_mandate` and `board_exposure` — 1 each. These
are the round-2 authoring targets, and they need mined constructions, not
contract control phrases.

## Against the acceptance target

The criterion is flagships (GPNI, Wise APAC) reproducibly in the top band
**and** `why_attractive` ≥ 60% on shown roles. Target-role extraction at
63–68% is the leading indicator for the second half and is now in the right
range — but extraction coverage is not the same measurement as
`why_attractive` on shown roles, and the two must not be conflated. Stage 2
remains blocked until §7.2 closes.

## Test posture

Semantic subset: **349 passed / 0 failed**. 158 synthetic controls green, all
22 owner-rejected negative fixtures green, 4 import-boundary guards untouched.
Full `tests/job_intel/` run: 612 passed / 42 failed against a baseline of
602 / 45 — the 42 are pre-existing order-dependent failures unrelated to §7.2
(they pass in isolation).
