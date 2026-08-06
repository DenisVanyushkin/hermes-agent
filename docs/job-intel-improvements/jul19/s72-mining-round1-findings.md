# §7.2 — T3 mining round 1: findings (output NOT usable as-is)

**Date:** 2026-08-06 · DEV slice (2956 roles) · $0

## Outcome

The miner ran and is correct by its own tests, but the candidate list it
produced is dominated by noise and must NOT be turned into rules. Reporting
this rather than shipping rules built on it.

Representative top candidates:

| Fact | n | Top mined example |
|---|---|---|
| scope_breadth | 49 | "The reasonably estimated yearly salary … About Datadog: …" |
| revenue_proximity | 131 | "The median Ramp customer saves 5% and grows revenue 16% …" |
| expansion_mandate | 6 | "Partner with product management, engineering and various go-to-market functions …" |

## Three causes, all fixable

1. **Sentence segmentation fails on this corpus.** The stored text is
   cleaned and often concatenated without terminal punctuation, so one
   "sentence" becomes a multi-hundred-character blob mixing salary, company
   blurb and duties. Splitting must also break on bullet markers, list
   glyphs, and lowercase→Capital transitions.

2. **Company-marketing prose still leaks.** "The median Ramp customer …
   grows revenue …" is not caught by the company-description opener test: it
   describes the company's CUSTOMERS. Needs a subject check — a duty
   sentence's subject must be the candidate (you / the role), not the
   company, its customers or its product.

3. **The corpus is mostly non-target roles.** The eligible corpus spans
   sales, support and engineering vacancies whose duty language is
   irrelevant to executive product mandate. Mining should be scoped to roles
   that pass the production function/seniority filter, otherwise frequency
   ranking is dominated by the wrong population.

## Next step (T3 round 2, before any rule authoring)

Fix (1) and (2) in the miner, scope to target-population roles for (3), then
re-mine and re-review the candidate list. Rule authoring (T5) stays blocked
until the candidate list is defensible — writing rules from this list would
reproduce the original defect in a new form: rules fitted to text that does
not describe a mandate.
