# Job Intel Search Product Redesign

**Version:** 1.0.0
**Status:** Approved
**Original design date:** 2026-08-10
**Approval date:** 2026-08-10
**Effective date:** 2026-08-10
**Owner:** Denis Vanyushkin
**Owner decision record:** `PS-SOT-2026-08-10-v1` (§18)
**Document type:** Product Search Source of Truth; no technical implementation
**Proposed pilot:** six weeks

## Normative scope

This approved document is the canonical Product Search Source of Truth for:

- the purpose and boundaries of Job Intel search;
- the career-search positioning used to define the relevant market;
- discovery portfolio and market-coverage policy;
- rules for selecting and presenting opportunities;
- the daily and weekly user experience;
- feedback and learning governance;
- pilot metrics, counterfactual, guardrails, and exit decision.

This document supersedes legacy opportunity theses, scoring policies, and company-discovery rules wherever they:

- use industry, geography, title, or company-list membership as an independent fit signal;
- treat a predefined company list as the boundary of the observable market;
- treat exploration as an acquisition source or a primary verdict;
- optimize machine scores without first establishing auditable, decision-relevant market coverage;
- classify Kazakhstan as a manually activated fallback market.

This document does not replace Candidate Facts, the structured resume, CRM lifecycle rules, source-specific evidence contracts, or technical architecture. Existing Career Preference, Semantic, and Decision contracts remain authoritative outside the explicit product changes listed here. Conflicting provisions require named, versioned migrations; implementation may not silently choose between contracts. Appendix B contains the compatibility and supersession map.

Changes to this document require an explicit version, a written rationale, expected product impact, and owner approval. No observed click, reaction, or model output can amend it automatically.

## 1. Executive summary

Job Intel currently evaluates vacancies inside a market that is too narrow. The stable acquisition flow comes mainly from a small, fintech-oriented set of companies. This repeatedly produces roles from the same employers and concentrates the feed in the US, London, and Australia while leaving most of Europe, GCC, APAC, Kazakhstan, Central Asia, global remote, and adjacent industries weakly observed.

The primary problem is therefore not scoring precision. It is insufficient market coverage combined with a career profile that has collapsed from a broad executive capability model into an industry proxy.

This redesign turns Job Intel from a curated-fintech vacancy monitor into an executive opportunity intelligence product. It asks:

> Where in the global market can Denis's combination of product leadership, business ownership, monetization, organization building, and transformation create unusual value?

The product has two independent discovery origins:

1. **Open Market** — vacancies found outside a predefined company list.
2. **Strategic Watchlist** — vacancies found through deliberate monitoring of selected companies.

It also has two selection modes that can occur within either origin:

1. **Core** — the opportunity is selected using established career hypotheses.
2. **Exploration** — the opportunity is selected to test one named, uncertain career hypothesis.

The six-week pilot favors recall and learning over perfect precision while protecting Denis's attention. The standard digest cadence is Monday–Friday in the Asia/Almaty timezone. On those days the product sends one digest with a working range of 5–7 unique vacancies when qualifying supply exists; fewer items are correct when it does not, and the authoritative weekly cap is 35. Exploration is a cross-cutting mode with a weekly working range of 5–7 items, normally one per day. A second exploration item is allowed only when it tests a strong and distinct hypothesis. Separate urgent delivery defaults to zero and is capped at one objectively time-sensitive item per day.

Opportunities are evaluated through separate dimensions: feasibility, mandate, company, transferability, career value, and evidence confidence. Industry, country, source, and title provide context; none is an independent fit score. User behavior can create a hypothesis, but career rules change only through explicit approval.

## 2. Product problem and evidence status

### 2.1 Product problem

The current product effectively asks:

> Does one of the selected fintech-oriented companies have a senior product title that passes the evaluator?

The redesigned product must ask:

> Which currently available roles across a deliberately broad market offer a feasible, valuable, and career-convertible mandate that fits Denis's demonstrated executive strengths?

The detailed production snapshot is informative evidence, not permanent product policy. It is preserved in Appendix A with its date and evidence boundaries.

### 2.2 Why additional scoring work does not solve the problem

Better vacancy understanding can reduce false positives after a vacancy has been found. It cannot by itself:

- discover companies outside the monitored universe;
- create geographic or industry observability;
- reveal non-standard but relevant executive titles;
- test whether existing career assumptions are wrong;
- compensate for a missing broad-market discovery origin.

Existing semantic and evaluation work remains useful. It becomes an enabling subsystem rather than the product's primary roadmap. The immediate objective is auditable, decision-relevant market coverage followed by better decisions within that coverage.

## 3. Product objective

### 3.1 Job to be done

When Denis is searching for his next executive role, Job Intel should continuously map the relevant market, surface a manageable set of new opportunities, explain why each deserves attention, and learn explicitly from his decisions without narrowing the market around accidental historical bias.

### 3.2 Desired user outcome

Denis should regularly see:

- opportunities from companies he did not already know;
- multiple viable geographies rather than the same few hubs;
- Kazakhstan opportunities as normal candidates when they genuinely qualify, without artificial volume;
- Central Asian opportunities assessed country by country rather than through a Kazakhstan-specific rule;
- mandates broader than a feature, a delivery function, or internal platform ownership;
- roles outside fintech where his capabilities transfer credibly;
- realistic relocation, local, or remote options;
- enough controlled exploration to discover missed career directions;
- few enough items that review remains sustainable.

### 3.3 North star

The north-star metric is:

> Activated opportunities per 60 minutes of user review attention.

Two user-outcome stages are deliberately separate:

- a `positive decision` exists when Denis chooses **Pursue** or **Investigate**;
- an `activated opportunity` exists only when a positive decision is followed by a concrete next step, such as a sponsorship check, company research, recruiter question, referral request, networking action, or application work.

Positive decisions per 100 delivered are a leading indicator of recommendation quality. Machine verdicts do not count as outcomes. Attention means actual review minutes or one stable proxy chosen and locked before the pilot. The same definition must be used for the behavioral baseline and pilot.

## 4. Product principles

### 4.1 Mandate over labels

The shape of the mandate matters more than title, industry, or country. A Product Director role may be too narrow; a GM Digital or Chief Customer Officer role may be ideal. A fintech role may be weak; a marketplace, telecom, travel, retail, or consumer-services role may be strong.

### 4.2 Recall before precision during discovery

During the pilot the product deliberately widens the observed market. Some precision loss is acceptable if it yields useful information, does not violate hard gates, and remains inside the attention budget.

### 4.3 Portfolio, not one funnel

No source, company list, industry, geography, role title, or previous positive example may define the whole search.

### 4.4 Feasibility is separate from desirability

A good role can be infeasible. A visa unknown is not a weak mandate. Company risk is not the same as role mismatch. These conclusions must remain separate.

### 4.5 Industry and geography govern observation, not fit

They determine whether the market has been searched too much or too little. They do not make an opportunity attractive on their own.

### 4.6 Evidence before conclusion

Every material claim distinguishes official evidence, reliable external evidence, reasonable inference, and unknown.

### 4.7 No silent learning

Behavior creates hypotheses, not automatic career rules. Material preference or profile changes require explicit owner approval.

### 4.8 User attention is a budget

Discovery may be broad internally, but poor filtering may not be externalized as Slack volume.

### 4.9 No-fill beats quota-fill

Portfolio targets are diagnostic and directional. If qualifying supply is insufficient, the product sends fewer items and reports the coverage gap. It never lowers the career or evidence bar to satisfy a daily, regional, or industry quota.

### 4.10 Exceptional opportunities may exceed a portfolio target

Coverage rules prevent systematic blindness; they must not hide a genuinely exceptional role. Exceptions are labeled and reported instead of silently changing the portfolio.

## 5. Career Profile v2

### 5.1 Core positioning

The proposed core positioning is:

> Executive product and digital-business leader who combines product strategy, business ownership, monetization, growth, organization building, and transformation in complex B2C and B2B2C environments.

Short version:

> Executive product and digital-business leader for growth, monetization, and transformation at scale.

This is broader than “fintech product leader” and broader than classic product craft alone.

### 5.2 Evidence-backed differentiators

The search profile is built around demonstrated capabilities:

- leadership of product and business organizations of approximately 50–90 FTE, with broader commercial organizational responsibility of 170+ employees;
- direct management of 10 reports in the SuperApp tribe, matrix leadership across five product tribes, and mentoring of product-marketing teams;
- explicit P&L and business-unit responsibility;
- acquisition, retention, customer lifecycle, pricing, and monetization;
- product portfolio and go-to-market ownership;
- organization design and operating-model transformation;
- turnaround of fragmented, overloaded, or declining environments;
- new-product and new-business-line launches;
- strategic pivots and decisions to stop non-viable initiatives;
- executive and board-level stakeholder work;
- large-scale consumer products and complex partner environments.

These are Candidate Facts claims and remain subject to the structured resume and Denis's explicit corrections.

### 5.3 What the profile is not

The product must not position Denis primarily as:

- a fintech or payments specialist;
- a feature-level product manager;
- a pure product-discovery specialist;
- a delivery, agile, project, or program-management leader;
- a technical platform or developer-infrastructure leader;
- a sales or marketing executive without digital product and business ownership;
- an internal transformation consultant without operating authority.

### 5.4 Discovery role vocabulary

Titles are search vocabulary, not final truth.

| Role family | Representative titles | Inclusion test |
|---|---|---|
| Product executive | CPO, VP Product, Head of Product, VP Consumer Product | Owns a material product portfolio, organization, or business outcome |
| Digital-business executive | Chief Digital Officer, VP/Director Digital Business, Digital Business Director | Owns a real digital business, portfolio, growth system, or P&L |
| Customer, growth, and commercial hybrid | Chief Growth Officer, Chief Customer Officer, Chief Commercial Officer, Consumer Business Director | Explicit ownership of digital product/business, customer lifecycle, growth, portfolio, or P&L; sales-only and marketing-only roles are excluded |
| Product/business-unit leader | Product Director, Digital Products Director, Business Unit Director, Platform Director | Scope is a business line, platform-as-business, region, portfolio, or multi-team organization |
| GM and market leader | GM Digital, GM Product, GM Market, Regional GM | Has product, growth, monetization, customer, or P&L authority rather than sales-only accountability |
| Growth and monetization executive | VP/Head of Growth Product, Monetization Director, Lifecycle Director | Owns a broad growth or revenue system; the narrow monetization exception may apply |
| Transformation builder | Digital Transformation and Growth Director, Product Transformation Director, Product Organization Lead | Has operating authority to change organization, priorities, and business outcomes; advisory-only work is excluded |
| Hybrid executive exploration | COO-adjacent digital role, Strategy and Product leader, Chief Commercial/Product hybrid | Eligible only when real digital product or business ownership is explicit |

### 5.5 Transferable problem patterns

The search prioritizes situations where a company needs one or more of the following:

- a fragmented product portfolio becoming an integrated customer proposition;
- a founder-led or delivery-led organization becoming product-led;
- unclear ownership requiring a new operating model;
- a declining or stalled consumer business requiring turnaround;
- monetization, pricing, retention, or lifecycle improvement;
- regional or multi-market expansion;
- integration of products, teams, or platforms;
- creation of a new digital business line;
- transition from individual products to a portfolio or platform-as-business;
- stronger connection between product decisions and P&L.

### 5.6 Domain portfolio

Domains are coverage groups, not hard ranks.

**Demonstrated contexts:**

- telecom and digital services;
- B2C subscription and lifecycle businesses;
- superapps and consumer digital portfolios;
- fintech, e-wallet, and regulated digital products;
- OTT and media-adjacent products;
- consumer and commercial transformation.

**Strong adjacent contexts:**

- marketplaces and classifieds;
- mobility, delivery, travel, and logistics platforms;
- ecommerce, retail technology, and digital consumer services;
- consumer subscription applications;
- digital health and education at scale;
- media, content, and creator platforms with an organizational or business mandate;
- B2B2C and mass-SMB platforms;
- traditional consumer companies building a real digital business.

**Controlled exploration contexts:**

- gaming;
- climate and energy consumer platforms;
- developer products where the role is commercial platform-as-business rather than infrastructure;
- Big Tech director-level roles;
- Series A–B Head of Product roles with unusually broad authority;
- COO/GM paths with substantial digital ownership.

### 5.7 Hard eligibility gates

Unless Denis explicitly changes them, the following are product-level hard gates:

- work in sanctioned countries or clearly unstable environments;
- Africa as a proactive search region at the current stage;
- US onsite/hybrid without explicit visa and relocation sponsorship;
- onsite/hybrid in any country where Denis lacks work authorization when the employer explicitly offers no viable path;
- scope below the minimum executive threshold;
- a non-product function without real digital-business ownership;
- a non-transferable required domain or language barrier;
- pure delivery, project, or program ownership;
- internal tools, technical infrastructure, or back-office ownership without meaningful business scope.

A role meets the minimum executive scope when it has at least one of:

1. ownership of a material business line, region, portfolio, or platform-as-business;
2. meaningful revenue or P&L authority;
3. multi-team organizational ownership combined with material strategy and cross-functional business decision authority.

A Director title, large team, or multi-team coordination alone is insufficient. The narrow monetization exception remains governed by Decision non-regression invariant 1 in §9.5.

Unknown sponsorship outside onsite US, an unknown reporting line, compensation, or timezone is an uncertainty to investigate, not an automatic rejection. Remote roles are evaluated by their actual country eligibility and timezone constraints, not by the employer's headquarters alone.

Kazakhstan is a normal eligible market, not a fallback. It uses the same mandate, company, evidence, and career-value standards as every other market. Because suitable executive supply is objectively small, it has no minimum volume or delivery quota. Low supply must be reported honestly and must never be compensated by lowering the bar.

Other Central Asian countries are evaluated independently, using the same global rules for role quality and country-specific feasibility. They do not inherit a Kazakhstan status, allocation, or career assumption.

## 6. Product model: four independent fields

Every selected vacancy has three vacancy-level fields and may produce one separate company-level action:

- vacancy-level `discovery_origin`;
- vacancy-level `selection_mode`;
- vacancy-level `system_verdict`;
- optional company-level `company_action`.

### 6.1 Discovery origin

`Open Market` means the vacancy was found without requiring prior watchlist membership.

`Strategic Watchlist` means the vacancy was found through active monitoring of a company with an existing thesis.

All acquisition provenance is retained, but each canonical vacancy has exactly one immutable primary origin for portfolio reporting:

- `Strategic Watchlist` applies only when the company already had `watchlist_status=active` before discovery and watchlist monitoring formed the canonical vacancy candidate;
- all other cases use `Open Market`, including vacancies found before or while a company is merely nominated or in `candidate` status;
- after canonical deduplication, later rediscovery or watchlist promotion does not rewrite the primary origin.

Origin explains how the canonical candidate entered the funnel, not whether it is a good opportunity.

### 6.2 Selection mode

`Core` means the vacancy was selected using established career hypotheses.

`Exploration` means it passed all hard gates and was selected to test one named uncertain hypothesis. Exploration can originate in either Open Market or Strategic Watchlist. It is not a weaker verdict and not a source.

An Exploration item changes exactly one material axis relative to a named Core reference where practical. A multi-axis exploration requires an explicit exception and cannot update any individual-axis hypothesis from a single reaction.

A vacancy is labeled Exploration only when its selection depends on an uncertain hypothesis. If it already qualifies under approved Core policy, it remains Core even when the company, industry, or geography is unfamiliar.

During the six-week pilot, 5–7 exploration items may be delivered in a full week when qualifying supply exists. Normally there is one per active day. A second item on the same day requires a strong, distinct hypothesis. This is an explicit, temporary override of the older 1–2 exploration-items-per-week preference rule; it expires with the pilot unless renewed by owner decision.

### 6.3 Opportunity verdict and company action

The current vacancy receives one `system_verdict`:

- `Priority`;
- `Investigate`;
- `Save`;
- `Reject`.

The company may separately receive one `company_action`:

- `nominate`;
- `promote`;
- `retain`;
- `deprioritize`;
- `reject`;
- `expire`.

Absence of a company action is represented by no action, not by a lifecycle state named `None`.

This allows natural combinations such as `Priority + Exploration`, `Investigate + Exploration`, or `Reject vacancy + nominate company`.

## 7. Discovery portfolio

### 7.1 Open Market

**Purpose:** find relevant vacancies and companies outside any existing list.

**Pilot working range:** approximately 60–70% of unique delivered vacancies over a full week when qualifying supply exists.

Open Market searches combinations of:

- role family;
- mandate or problem pattern;
- geography and work format;
- company scale and stage;
- industry and business model.

A company does not need watchlist approval before a strong vacancy can be shown. Success is measured by qualified new companies, useful opportunities, and coverage gained—not raw job-board hits.

### 7.2 Strategic Watchlist

**Purpose:** monitor companies where scale, product complexity, trajectory, and organizational needs create a recurring fit thesis.

**Pilot working range:** approximately 30–40% of unique delivered vacancies over a full week when qualifying supply exists.

The watchlist is a prioritization instrument, never a market allowlist. It is diversified by region and business model; fintech may remain important but may not dominate the list or the delivered feed.

Companies enter or rise because of an evidence-backed thesis such as:

- product leadership change;
- rapid or multi-market expansion;
- product-organization restructuring;
- creation of a new business line;
- M&A or portfolio integration;
- marketplace or digital-business expansion;
- visible need for monetization, lifecycle, or operating-model change.

### 7.3 Portfolio denominator

The origin mix denominator is the number of unique vacancies delivered in daily digests during a full week. Each vacancy has one origin. Exploration does not create a third origin or a second count.

The 60–70/30–40 ranges are diagnostic targets, not fill requirements. An exceptional week may fall outside them. The weekly review must explain material deviations and whether they came from supply, source observability, or selection decisions.

## 8. Market coverage policy

### 8.1 Geographic coverage

The pilot must attempt to observe the following independent lanes:

| Geographic lane | Product intent |
|---|---|
| Europe, including the UK | Expand beyond London into continental Europe, DACH, Benelux, Nordics, and CEE |
| APAC excluding Australia/New Zealand | Observe ecosystem, telecom, marketplace, consumer, and regional-growth contexts |
| GCC | Observe executive relocation, digital transformation, regional platform, and consumer-services markets |
| Americas | Include Canada and Latin America; include US roles only when actual remote eligibility or sponsorship makes them feasible |
| Global remote | Capture genuinely location-independent roles and assess country/timezone eligibility explicitly |
| Australia and New Zealand | Retain as valid but difficult markets without allowing a few employers to dominate |
| Kazakhstan | Normal eligible market; no minimum share, no fallback status, no lowered bar |
| Other Central Asia | Each country observed and evaluated independently; no coupling to Kazakhstan |

No fixed geographic delivery allocation is imposed during the pilot. Executive supply, access, and feasibility differ too much for a forced percentage to be meaningful. Instead, every lane receives an explicit weekly observability state:

1. searched and qualified results found;
2. searched and no qualified results found;
3. attempted but source access was blocked or degraded;
4. not meaningfully observed.

Only states 1 and 2 count as `meaningfully observed`. State 2 is valid only when the complete, pre-agreed lane search contract was executed without critical source degradation. The contract must name the covered role families, search window, active sources, and minimum source breadth before the pilot starts. States 3 and 4 mean that the lane was not sufficiently observable. A single unsuccessful query can never establish state 2.

This distinction prevents “there are no roles” from being inferred when the market was not actually observable.

For geographic reporting, the denominator is unique hard-gate-eligible vacancies at funnel stage 4. Each candidate has one primary geography. A country-specific remote role retains its country as primary and receives a separate remote-eligibility marker; only genuinely location-independent mandates use `Global remote` as primary. Portfolio-reviewed and delivered geography are reported as separate later-stage cuts.

Guardrails:

- the US, UK, and Australia may not dominate merely because current sources overproduce them;
- at least four geographic lanes should be meaningfully observed in a full week and all lanes over a rolling two-week cycle, unless a coverage failure is reported;
- absence of Kazakhstan or other Central Asian vacancies from a digest is acceptable when no qualifying supply exists;
- no geography target may cause a weak vacancy to be delivered.

### 8.2 Industry and business-model coverage

Industry, business model, and selection mode are separate classifications. They must never share one primary-family field.

Each eligible candidate receives exactly one `industry_family`; `unknown` is allowed when evidence is insufficient:

- `financial_services`;
- `telecommunications`;
- `retail_and_ecommerce`;
- `mobility_travel_and_logistics`;
- `media_entertainment_and_creator`;
- `healthcare`;
- `education`;
- `energy_utilities_and_climate`;
- `enterprise_software_and_technology`;
- `consumer_services`;
- `other`;
- `unknown`.

Industry is classified from the employing business unit or mandate when that evidence is explicit. Otherwise the employer's primary industry is used; unresolved ambiguity becomes `unknown` rather than an arbitrary choice.

Each eligible candidate separately receives exactly one `business_model_primary`; `unknown` is allowed:

- `b2c_subscription`;
- `marketplace`;
- `platform_as_business`;
- `transaction_or_financial_intermediation`;
- `advertising_or_attention`;
- `commerce_margin`;
- `b2b_software_or_enterprise_service`;
- `regulated_service`;
- `hybrid`;
- `other`;
- `unknown`.

The primary business model describes how the relevant business unit creates and captures value. Supplementary `business_model_tags` may include `b2b2c`, `mass_smb`, `multi_sided`, `superapp`, `ecosystem`, or other approved tags. Tags never enter a primary coverage denominator.

Gaming, climate, Big Tech, early-stage companies, and unfamiliar roles receive ordinary industry and business-model classifications. Their experimental status is represented only by `selection_mode=Exploration`.

Coverage is reported independently for geography, industry, primary business model, and selection mode. For industry and business-model coverage, the denominator is unique hard-gate-eligible vacancies. Portfolio-reviewed and delivered distributions are reported as separate later-stage cuts of the same taxonomy.

`unknown` remains visible as a data-quality category but never counts toward an industry- or business-model-breadth target. For the pilot concentration guardrail, fintech is measured conservatively as all delivered vacancies with `industry_family=financial_services`; this prevents narrower labeling from hiding financial-sector concentration.

Guardrails:

- fintech should normally be no more than 25% of unique weekly digest vacancies;
- at least five industry families and four primary business models should be meaningfully observed in a full pilot week when source access allows;
- no employer should normally contribute more than two ordinary cards per week;
- several roles from one employer should be compressed into a company cluster where appropriate;
- industry feedback must identify the mandate or transferability reason before creating an industry hypothesis.

### 8.3 Canonical search funnel

The following stages and order are normative:

1. **Raw observed vacancy** — a source produced a possible vacancy record.
2. **Canonical current vacancy** — identity has been resolved, duplicates consolidated, and freshness confirmed.
3. **Minimum evidence sufficient** — evidence is sufficient to identify the role, company, location/work format, material responsibilities, and known feasibility constraints well enough to test the hard gates.
4. **Hard-gate eligible** — no known hard eligibility gate is violated; unknowns remain explicit.
5. **Portfolio reviewed** — an attempt has been made to evaluate all six Decision dimensions; unavailable facts remain `unknown` rather than blocking the stage.
6. **Selected** — the vacancy passed the delivery policy, including verdict eligibility, novelty, portfolio balance, and attention budget.
7. **Delivered** — the vacancy actually appeared in Denis's digest or a valid urgent exception.
8. **User decision recorded** — Denis provided a named user decision.
9. **Concrete action completed** — a research, feasibility, networking, outreach, referral, or application step was actually completed.

`Eligible candidate` means a vacancy at stage 4: canonical, current, minimum evidence sufficient, and not in violation of a known hard gate. Title and location alone are insufficient.

`Portfolio reviewed` means stage 5, not merely collected or eligible. `Selected` and `Delivered` are distinct: a selected item may fail to deliver, and only actual delivery consumes a user-facing slot.

Observability denominators use hard-gate-eligible vacancies at stage 4. Portfolio and delivery metrics must name their later stage explicitly and may not be presented as acquisition coverage.

## 9. Opportunity Decision Contract

### 9.1 Required first-class dimensions

Every user-facing opportunity contains separate conclusions for:

| Dimension | Core question |
|---|---|
| Feasibility | Can Denis realistically take this role given work format, location, sponsorship, language, and explicit barriers? |
| Mandate fit | Does the role own a business line, region, portfolio, platform-as-business, growth system, organization, or P&L? |
| Company fit | Does the company offer useful scale, trajectory, context, and a credible need for Denis's strengths? |
| Transferability | Which strengths transfer directly, what bridge is credible, and which gaps remain unsupported? |
| Career value | Would this improve international credibility, executive scope, brand, and the path toward CPO/VP/GM leadership? |
| Evidence confidence | Which conclusions are explicit, inferred, externally supported, or unknown? |

Transferability and Career Value must become first-class outputs of Decision Contract v2 before production rollout of this product policy. They may not be left as unconstrained free text.

### 9.2 Opportunity verdicts

These values are `system_verdicts`; they must not be conflated with the similarly named `user_decision` values in §11.

| Verdict | Meaning | Expected action |
|---|---|---|
| **Priority** | Strong mandate and career value; feasibility is credible or quickly confirmable | Review promptly and decide whether to pursue |
| **Investigate** | Potentially strong, but one material and resolvable fact is unknown | Complete a named research, feasibility, or outreach step |
| **Save** | Valid opportunity, but not currently strong or urgent enough for action | Retain without presenting it as an actionable recommendation |
| **Reject** | Infeasible, below scope, wrong function, non-transferable, stale, or strategically weak | Suppress with a concise evidence-backed reason |

If an internal numeric score is retained for ordering, it must not replace these dimensions or appear as the primary decision.

Daily-digest eligibility is deterministic:

- `Priority` is eligible for the daily digest;
- `Investigate` is eligible only when it names at least one material, bounded, and realistically resolvable question;
- `Save` normally remains outside the daily digest and may consume a slot only in Exploration mode when its information value and named hypothesis are explicit;
- `Reject` is never delivered as an opportunity card.

### 9.3 Compatibility mapping from the current evaluator

| Existing recommendation | Product verdict mapping |
|---|---|
| `exceptional`, `strong` | `Priority` |
| `promising` | `Investigate` when a concrete blocker can be resolved; otherwise `Save` |
| `unclear` | `Investigate` only when the missing fact is material and realistically resolvable; otherwise `Save` or `Reject` |
| `not_recommended` | `Reject` |
| `exploration_axis` | Preserved as `selection_mode=Exploration`, never converted into a verdict |

This table is product intent, not permission for an unreviewed technical migration.

### 9.4 Decision order

1. Verify vacancy identity, freshness, and minimum evidence.
2. Apply hard feasibility and function/scope gates.
3. Evaluate mandate.
4. Evaluate company context.
5. Evaluate transferability and career value.
6. Assign opportunity verdict and evidence confidence.
7. Assign selection mode and company action independently.
8. Decide whether the vacancy belongs in the digest, urgent exception, saved set, weekly company section, or rejection ledger.

### 9.5 Decision non-regression invariants

The redesign must preserve these established distinctions:

1. **Narrow monetization exception.** Pricing, acquiring, or monetization can be attractive at domain scope when the revenue authority and strategic leverage are material.
2. **B2B is not independently negative.** Enterprise-sales-only scope is negative; platform-as-business can be a strong fit.
3. **Remote US does not inherit the onsite US sponsorship gate.** Actual country eligibility and work format govern feasibility.
4. **Crypto employer is a company concern, not an automatic role veto.** Company risk and role fit remain separate.
5. **Platform engineering is not platform-as-business.** Technical infrastructure is excluded unless meaningful business ownership is explicit.
6. **GM, CCO, CDO, CGO, and COO-adjacent roles may pass the function gate** when digital product, business, lifecycle, growth, portfolio, or P&L ownership is real.
7. **Timezone and compensation are not hard gates** until explicit limits are approved.
8. **Unknown is not negative.** Unknown sponsorship, reporting line, P&L, team size, or compensation can produce `Investigate`; it cannot silently become either a strong fit or a rejection.

## 10. User experience

### 10.1 Daily digest

The standard active search days are Monday–Friday in the Asia/Almaty timezone. On each active day, Job Intel sends one digest with a working range of 5–7 unique vacancies when qualifying supply exists and fewer when it does not. The weekly cap of 35 delivered vacancies is authoritative and cannot be exceeded by retries, holiday shifts, or additional runs.

Weekends have no ordinary digest. They may accumulate qualifying material for Monday or produce a valid urgent exception under §10.3.

Weekly working composition:

- approximately 60–70% Open Market origin;
- approximately 30–40% Strategic Watchlist origin;
- 5–7 Exploration-mode items across both origins, normally one per day;
- no more than two ordinary items from one company;
- no duplicate or materially unchanged vacancy already reviewed.

These are ceilings and working ranges, not minimum fill obligations. A short coverage note explains under-filled days and distinguishes insufficient qualifying supply from weak observability.

### 10.2 Progressive disclosure

The digest summary shows only what is needed for a decision:

- role, company, location, and work format;
- opportunity verdict plus an Exploration marker when applicable;
- two specific reasons it deserves attention;
- one main risk or unknown;
- recommended action.

Expandable or threaded detail contains:

- mandate and scope evidence;
- transferability bridge and unsupported gaps;
- feasibility evidence;
- company rationale;
- career value;
- evidence confidence and detailed unknowns;
- discovery origin and exploration hypothesis.

This preserves decision quality without requiring ten visible blocks per card.

### 10.3 Urgent exception

Urgent delivery defaults to zero and is capped at one item per day. A `Priority` verdict alone is insufficient.

An urgent item requires a strong opportunity plus an externally evidenced time-sensitive fact showing that it should not wait for the next digest:

- an explicit closing date within 48 hours;
- a confirmed short referral or outreach window;
- a recruiter deadline, limited intake, or confirmed rapidly closing shortlist that became known after the daily digest.

Role strength, model confidence, or an agent-authored rationale is never urgency by itself.

### 10.4 Weekly market and company review

The weekly review summarizes:

- geography, industry, and business-model observability states;
- origin and selection-mode mix with denominators;
- useful sources and qualified new companies;
- concentration, duplication, and source-degradation warnings;
- exploration hypotheses and user outcomes;
- positive user decisions and activated opportunities;
- unresolved feasibility questions;
- proposed search adjustments;
- company watchlist changes and their rationale;
- any profile change requiring explicit approval.

Watchlist signals do not consume the 5–7 daily vacancy slots.

### 10.5 Monthly strategy review

The monthly review asks:

- Which opportunities led to research, outreach, application work, or networking?
- Which mandate and company patterns converted into action?
- Which geographies were attractive but infeasible, genuinely low-supply, weakly observed, or inaccessible?
- Is the attention budget sustainable?
- Which hypotheses were supported, disproved, or remain unknown?
- Should search volume, portfolio attention, or Career Profile policy change?

## 11. Feedback and learning model

### 11.1 Primary user decisions

These values are `user_decision`; they are analytically distinct from `system_verdict` even where the display labels are similar.

- **Pursue**;
- **Investigate**;
- **Save for later**;
- **Not interesting**;
- **Not feasible**;
- **Wrong or stale data**.

`Pursue` expresses intent. It does not mean an application was submitted and may not mutate CRM lifecycle state automatically.

### 11.2 Negative reason taxonomy

When needed, capture one primary reason and an optional note:

- mandate too narrow;
- insufficient business or P&L ownership;
- wrong function;
- scope too low;
- company unattractive;
- company scale or trajectory weak;
- transferability gap too large;
- non-transferable required domain;
- required language unavailable;
- location or visa infeasible;
- work format unacceptable;
- timezone, working-hours, or travel concern;
- compensation concern;
- interesting but not a current priority;
- duplicate or already reviewed;
- factual error;
- other.

### 11.3 Positive reason taxonomy

For `Pursue` and `Investigate`, an optional primary positive reason is captured:

- breadth of mandate;
- P&L or business ownership;
- growth or monetization;
- company or brand;
- geography or relocation;
- transformation challenge;
- industry or problem interest;
- other.

### 11.4 Learning invariants

- no reaction means no signal;
- one primary reason plus an optional note is sufficient;
- factual errors trigger data repair and are excluded from preference learning;
- feasibility, factual correction, and genuine preference remain separate signals;
- an Exploration reaction updates only the named hypothesis, not the entire industry or region;
- an Exploration reaction without a reason is recorded as interaction but does not count as an interpretable hypothesis outcome;
- positive reactions are interpreted through the stated positive reason rather than through source, title, or industry alone;
- a material profile change requires a named rule, supporting examples, counterexamples, expected coverage impact, and explicit owner approval.

## 12. Company intelligence and watchlist lifecycle

Company-event intelligence is included in the pilot only as a weekly product. It can recommend networking or watchlist attention before a suitable vacancy appears, but it remains separate from the daily vacancy digest and has separate metrics.

A company signal is useful only when it contains evidence, a fit thesis, and a proposed company action. Events such as leadership change, expansion, restructuring, M&A, or a new business line are inputs to the thesis, not opportunities by themselves.

Watchlist status, review freshness, and transition action are separate fields.

`watchlist_status` has exactly one value:

- `candidate` — nominated, but the thesis is not approved for active monitoring;
- `active` — approved monitoring priority and eligible for Strategic Watchlist origin;
- `deprioritized` — retained as history but not actively prioritized;
- `rejected` — explicitly excluded from monitoring for a recorded reason;
- `expired` — the thesis was not renewed after review became due.

`review_state` is independent:

- `current`;
- `review_due`.

Normative company actions and transitions are:

- `nominate` → `candidate`;
- `promote` → `active`;
- `retain` → remains `active` and refreshes the thesis;
- `deprioritize` → `deprioritized`;
- `reject` → `rejected`;
- `expire` → `expired` when a review-due thesis is not renewed.

`candidate` companies are not part of the Strategic Watchlist discovery origin. `review_due` can coexist with `candidate`, `active`, or `deprioritized` and does not itself change origin eligibility. `rejected` and `expired` are terminal for the current thesis; renewed consideration starts a new nominated thesis rather than reusing stale freshness state.

The weekly review reports nominations, promotions, retention decisions, removals, and overdue theses. A candidate list may not grow indefinitely without decisions.

Company intelligence is measured separately through:

- company signals reviewed;
- accepted company actions;
- networking actions initiated from a company signal;
- share of active theses in `review_due` state.

## 13. Six-week product pilot

### 13.1 Pilot objective

Determine whether a diversified search portfolio produces more activated opportunities per review hour, with wider observable market coverage and without exceeding Denis's attention budget.

### 13.2 Phase 0 — contract, baseline, and counterfactual

Before the first pilot digest:

- confirm the effective Product Search SoT v1.0.0 and Career Profile v2; any amendment follows the versioned change rule rather than silently altering the pilot;
- lock an immutable 4–6 week supply- and policy-level pre-pilot baseline using the metric definitions in this document;
- choose and lock the attention-measurement method;
- establish a behavioral baseline from delivered historical evidence using the same stable attention proxy, or run a short prospective baseline before the new delivery policy starts if no comparable historical evidence exists;
- record baseline concentration, duplicates, factual errors, feasibility unknowns, user actions, and observability gaps;
- preserve the old selection policy as a counterfactual and, where practical, continue it in shadow without user delivery; shadow results are authoritative only for supply- and policy-level metrics, never for unobserved user behavior or attention;
- perform a named impact analysis across Career Preference, Semantic, Decision, CRM, and search policy contracts;
- prevent production rollout of the old end-to-end selection policy.

### 13.3 Weeks 1–2 — breadth and observation

Focus:

- establish both discovery origins and both selection modes;
- attempt meaningful observation across the agreed regional and business-model lanes;
- maximize qualified new-company discovery inside the attention budget;
- label every Exploration item and its hypothesis;
- collect clean positive and negative reasons;
- avoid overreacting to early precision.

### 13.4 Weeks 3–4 — calibration

Focus:

- identify mandate patterns producing real user action;
- remove repeated non-product noise;
- rebalance attention where markets are not meaningfully observed;
- promote or reject newly discovered companies;
- resolve recurring feasibility unknowns;
- formulate—but do not silently apply—profile-change proposals.

### 13.5 Weeks 5–6 — stabilization

Focus:

- compare positive decisions and activated opportunities with the appropriate locked baseline;
- verify that breadth does not depend on quota-fill;
- reduce exploration in disproved areas;
- preserve unresolved hypotheses explicitly;
- decide steady-state volume, origin mix, and exploration budget.

### 13.6 Exit decision

At the end of week six, choose:

- **Adopt** — must-pass guardrails hold and the target outcomes materially improve over baseline;
- **Adjust** — the strategy creates value but a bounded part of profile, coverage, UX, or volume needs another test;
- **Stop** — wider search does not improve user outcomes or cannot remain inside the attention budget; document the evidence and choose another hypothesis.

## 14. Metrics, counterfactual, and guardrails

### 14.1 Metric dictionary

| Metric | Definition |
|---|---|
| Positive decision | User chose `Pursue` or `Investigate`, regardless of whether a next step was completed |
| Activated opportunity | User made a positive decision and completed a concrete research, feasibility, outreach, referral, networking, or application step |
| Attention | Actual review minutes or a stable pre-agreed proxy, locked before the pilot |
| North star | Activated opportunities per 60 minutes of attention |
| Positive-decision rate | Unique delivered vacancies receiving a positive decision divided by all unique delivered vacancies |
| Activation rate | Unique activated opportunities divided by unique delivered vacancies |
| Qualified previously untracked company | Company absent from every watchlist status at pilot start, supported by sufficient company evidence and either at least one vacancy with `system_verdict=Priority`, `Investigate`, or `Save`, or a standalone company thesis accepted through an owner-approved `nominate` or `promote` action |
| Origin mix | Unique delivered vacancies by exactly one discovery origin divided by all unique delivered vacancies |
| Selection-mode mix | Unique delivered vacancies by exactly one selection mode divided by all unique delivered vacancies |
| Geography coverage | Unique hard-gate-eligible vacancies at funnel stage 4 by one primary geography; portfolio-reviewed and delivered cuts reported separately |
| Industry coverage | Unique hard-gate-eligible vacancies at funnel stage 4 by exactly one `industry_family`; portfolio-reviewed and delivered cuts reported separately |
| Business-model coverage | Unique hard-gate-eligible vacancies at funnel stage 4 by exactly one `business_model_primary`; tags excluded from the denominator |
| Duplicate rate | Materially unchanged previously reviewed opportunities divided by all delivered vacancies |
| Non-product false-positive rate | Delivered vacancies that fail the approved function/scope contract divided by all delivered vacancies |
| Unresolved-feasibility rate | Feasibility-led `Investigate` vacancies whose named question remains unresolved seven calendar days after the user decision divided by all feasibility-led `Investigate` vacancies old enough to complete that window |
| Material factual-error rate | Delivered vacancies with at least one adjudicated material factual error divided by all delivered vacancies |

A material factual error is either a user-confirmed correction or an audit-confirmed mismatch between a factual claim and authoritative evidence that could change verdict, feasibility, recommended action, or user trust. Editorial style, an explicitly labeled inference, or a reasonable interpretation dispute is not counted unless presented incorrectly as fact. Confirmed errors trigger repair and are excluded from preference learning.

### 14.2 Must-pass guardrails

- zero silent profile or hard-filter changes;
- zero materially unchanged duplicate deliveries;
- median daily review attention at or below 15 minutes;
- zero known hard-gate violations delivered;
- every Exploration item has one named hypothesis;
- every Exploration item changes one material axis unless an explicit multi-axis exception is recorded;
- factual corrections are excluded from preference learning;
- no-fill is used instead of lowering the quality bar;
- Kazakhstan and other low-supply markets have no delivery minimum.

### 14.3 Target outcomes

| Dimension | Six-week product target |
|---|---|
| User-facing volume | Up to 35 unique vacancies per full week; 25–35 is the expected working range only when qualifying supply exists |
| Exploration | 5–7 items per full week when qualifying hypotheses and vacancies exist; normally one per day |
| Origin balance | Approximately 60–70% Open Market and 30–40% Watchlist across delivered vacancies, interpreted diagnostically |
| New companies | 6–10 qualified previously untracked companies per week in weeks 1–4 when market observability is adequate |
| Geographic observability | At least four lanes meaningfully observed each full week and all agreed lanes over a rolling two-week period, or an explicit blocked/not-observed report |
| Industry observability | At least five industry families meaningfully observed per full week when access allows |
| Business-model observability | At least four primary business models meaningfully observed per full week when access allows |
| Fintech concentration | Normally no more than 25% of unique delivered vacancies |
| Employer concentration | Normally no more than two ordinary cards from one employer per week; top-three employer share reported |
| Data quality | Material factual-error rate and non-product false-positive rate each below 5% by weeks 5–6 |
| User value | Positive decisions per 100 delivered and activated opportunities per review hour are reported separately; adoption requires at least four activated opportunities across weeks 5–6, at least 25% improvement in one comparable rate, and no regression in the other |

The user-action funnel is reported consistently:

1. delivered;
2. positive user decision: `Pursue` or `Investigate`;
3. concrete action completed;
4. outreach or application actually initiated;
5. employer or network response.

Machine `Priority` and `Investigate` verdicts are prediction-quality inputs and never count in the outcome numerator.

### 14.4 Counterfactual comparison

The pilot compares the new policy with the locked pre-pilot baseline and, where practical, a shadow continuation of the old selection policy.

Shadow comparison is authoritative only for supply- and policy-level metrics because an undelivered vacancy cannot produce a user decision, attention measure, or activation. User-action and attention outcomes require delivered historical evidence with the same measurement method or the prospective baseline established in Phase 0.

Comparable metrics use the same definitions for:

- positive decisions per 100 delivered, only where delivery evidence exists;
- activated opportunities per review hour, only where comparable attention and action evidence exists;
- unique qualified companies;
- employer, region, industry, and business-model concentration;
- duplicate and non-product false-positive rates;
- unresolved-feasibility rate;
- geographic, industry, and business-model observability;
- policy disagreement between the old and new selection approaches.

Every exit report shows absolute numerators and denominators alongside relative change. A large percentage improvement from a very small base is not sufficient evidence for adoption by itself.

Seasonality, source outage, or a major market event must be recorded as a confounder rather than silently attributed to product policy.

### 14.5 Pause and stop triggers

Pause and investigate if any of the following occurs:

- median daily review time exceeds 15 minutes for two consecutive weeks;
- two consecutive weeks require lower-quality cards to approach the volume range;
- material factual-error rate exceeds 10% in a full week;
- known hard-gate violations are repeatedly delivered;
- source degradation makes at least half of agreed geographic lanes not meaningfully observed;
- Exploration produces no interpretable hypothesis outcomes for two consecutive weeks.

## 15. Product governance and authorities

### 15.1 Parallel authorities

**Candidate authority**

- confirmed structured-resume facts;
- explicit corrections from Denis.

**Opportunity evidence authority**

1. current official vacancy evidence;
2. official company evidence;
3. reliable external evidence;
4. clearly labeled inference;
5. unknown.

**Policy authority**

- approved Career Profile and feasibility rules;
- this Product Search SoT;
- approved Decision Contract.

**Learning authority**

- clean user feedback with a known reason;
- named hypotheses;
- owner-approved profile changes.

One authority may interpret another but may not rewrite its facts. For example, career policy interprets official vacancy evidence; it cannot replace the evidence.

### 15.2 Ownership

Denis owns:

- career objectives and hard constraints;
- approval of profile and Product SoT changes;
- final decisions to pursue opportunities;
- resolution of product-policy conflicts.

Job Intel owns:

- broad and balanced market observation;
- evidence-backed opportunity interpretation;
- attention-budget enforcement;
- explicit reporting of uncertainty, low supply, and coverage gaps;
- bounded proposals for search and profile changes.

The product does not apply to vacancies, contact recruiters, or mutate application state without a separate explicit workflow.

### 15.3 Review cadence

- weekly: coverage, user actions, company intelligence, and exploration review;
- end of week three: interim portfolio calibration;
- end of week six: pilot exit decision;
- after the pilot: profile review only after a meaningful body of clean feedback or a material career-goal change.

## 16. Transition from the current semantic/scoring roadmap

The transition decision is:

1. The already bounded current benchmark step may be closed if closure requires no scope expansion.
2. Semantic, Decision, or recommendation policy must not be tuned merely to improve that benchmark.
3. The old end-to-end selection policy must not receive a new production rollout.
4. After Product SoT approval, a versioned impact analysis must identify what is reusable, what requires Decision Contract v2, and what belongs to the new search-portfolio layer.
5. A named checkpoint must record where the former product roadmap becomes superseded.

This is a product sequencing decision, not a technical implementation plan.

## 17. Non-goals

This document does not define:

- source-specific acquisition technology;
- browser automation or job-board integration;
- database schema;
- ranking algorithms, prompts, or model providers;
- semantic extraction implementation;
- Slack implementation details;
- infrastructure, scheduling, retries, or alerting;
- a technical migration plan;
- automated applications;
- CV or cover-letter generation;
- a universal compensation threshold;
- a guaranteed interview or offer rate.

Technical designs may be created only after the product contract is approved and may not redefine it implicitly.

## 18. Owner-approved decisions

Decision record `PS-SOT-2026-08-10-v1` approves the following product decisions:

1. Insufficient market coverage, not scoring precision, is the primary product problem.
2. Career Profile v2 positions Denis around product and digital-business leadership, growth, monetization, and transformation rather than fintech specialization.
3. Open Market and Strategic Watchlist are the only discovery origins; Watchlist is never an allowlist.
4. Core and Exploration are cross-cutting selection modes; Exploration is not a verdict or source.
5. During the pilot, Exploration has a 5–7 item weekly working range and temporarily overrides the older 1–2 item rule.
6. Kazakhstan is a normal eligible market with no minimum volume and no fallback status.
7. Other Central Asian countries are independent normal markets and do not inherit Kazakhstan policy.
8. Geography, industry, and business model govern observable coverage and do not score vacancy quality.
9. The product uses five ordinary digest days, Monday–Friday in Asia/Almaty, a 5–7 vacancy daily working range when qualifying supply exists, and an authoritative weekly cap of 35; no-fill beats quota-fill.
10. Urgent delivery defaults to zero, is capped at one per day, and requires objective time sensitivity.
11. Discovery origin, selection mode, system verdict, and company action are separate fields with deterministic origin attribution and normalized watchlist transitions.
12. Evaluation separates feasibility, mandate, company, transferability, career value, and evidence confidence.
13. Transferability and Career Value become first-class Decision Contract v2 outputs.
14. Positive user decisions and activated opportunities—not machine verdicts—define product value; activated opportunities per review hour are the north star.
15. Profile changes require explicit owner approval.
16. Company-event intelligence is a separate weekly pilot product and does not consume vacancy slots.
17. The pilot uses an immutable baseline and comparable definitions; a shadow old-policy counterfactual is authoritative only for supply- and policy-level metrics.
18. Current semantic/scoring expansion pauses except for bounded closure or an active user-visible blocker.
19. The nine-stage search funnel and the exact meaning of meaningful observability are normative for every coverage denominator.
20. Industry, primary business model, business-model tags, and selection mode are classified and measured separately.
21. System verdicts and user decisions are separate vocabularies; only user decisions followed by completed actions create activated opportunities.
22. Relative improvement is reported with absolute numerators and denominators and cannot justify adoption from a trivial base by itself.

## 19. Product acceptance record

Product re-review confirmed the strategy and the limited contract changes in this version. This Product SoT governs implementation planning because:

- normative scope and the compatibility map are approved;
- Career Profile v2 is bounded by confirmed Candidate Facts;
- discovery origin, selection mode, system verdict, and company action are independent and deterministic;
- Kazakhstan and Central Asia policy is explicitly approved;
- active-day cadence, digest eligibility, Exploration rules, progressive disclosure, and urgency rules are normative;
- geography, industry, and business-model observability have separate taxonomies, stages, and denominators;
- positive decisions, activations, immutable baselines, counterfactual limits, guardrails, and stop triggers are defined;
- Decision Contract v2 changes are acknowledged as a separate versioned migration;
- both review rounds are resolved in Appendix C;
- technical implementation is explicitly prohibited from redefining these product decisions.

## 20. Current-to-proposed product delta

| Current behavior | Proposed behavior |
|---|---|
| Small company universe bounds the market | Watchlist is one discovery origin and never an allowlist |
| Fintech-heavy source portfolio | Broad industry and business-model observation with a fintech concentration guardrail |
| US, London, and Australia dominate | Every region receives an auditable observability state; no country is filled by quota |
| Kazakhstan is treated as fallback | Kazakhstan is a normal eligible, low-supply market with no minimum volume |
| Central Asia is grouped with Kazakhstan | Each Central Asian country is evaluated independently under global rules |
| Exploration is treated as a third stream or verdict | Exploration is a cross-cutting selection mode with a named hypothesis |
| Industry and title act as fit proxies | Mandate, feasibility, business ownership, transferability, and career value determine fit |
| Repeated collection counts resemble coverage | Unique candidates, qualified new companies, and observability states define coverage |
| Machine verdicts can resemble success | Only user decisions plus concrete actions count as product outcomes |
| Dense cards are the main experience | Compact digest summary with expandable evidence detail |
| Strong score can trigger interruption | Urgent delivery requires objective time sensitivity and defaults to zero |
| Feedback may be interpreted causally without a reason | Positive, negative, feasibility, and factual signals remain explicit and separate |
| Company discovery can grow an unbounded list | Watchlist has a lifecycle, review dates, promotion, rejection, and expiry |
| Technical scoring roadmap drives product priorities | Market observability and activated opportunities per review hour drive priorities |

## Appendix A. Informative production baseline snapshot

This appendix is evidence, not normative product policy. It reflects the production state inspected on 2026-08-10 and may become stale.

Evidence boundary:

- latest inspected daily run ID: `463`;
- source-yield view: latest 14-day period ending 2026-08-10;
- notification view: latest 30-day period ending 2026-08-10;
- supporting source-coverage audit: `docs/reports/2026-07-04-source-coverage-yield-audit.md`.

Observed baseline:

- the stable ATS flow covered approximately 30 predefined company tenants;
- the portfolio was concentrated in fintech, payments, digital banking, and crypto-adjacent companies;
- LinkedIn was not part of the production acquisition flow;
- HeadHunter produced 16 collected rows in the inspected 14-day period and no delivered vacancy cards;
- 51 vacancy-card messages represented 40 unique URLs from 16 companies over 30 days;
- Affirm, Coinbase, and Brex accounted for 25 of the 51 sends;
- approximately half of sends were US roles, followed by Canada, London, and Australia;
- continental Europe, GCC, most of APAC, Kazakhstan, and the rest of Central Asia were weakly represented;
- company-universe discovery did not expand the production search universe;
- the normalized career preference model was not the product logic governing the delivered feed.

Raw collected-vacancy counts are not treated as proof of market coverage. Re-reading open jobs from the same companies increases volume without expanding the observed market.

## Appendix B. Contract compatibility and supersession map

| Contract or artifact | Authority under v1.0.0 | Relationship to this Product Search SoT |
|---|---|---|
| Structured resume / Candidate Facts | Canonical facts about Denis's experience | Remains authoritative; this document may not invent or broaden facts |
| Career Preference Model | Approved career preferences and feasibility constraints | Remains authoritative except for explicit versioned overrides in this document: Exploration pilot volume and Kazakhstan's non-fallback status |
| Semantic Contract | Meaning and evidence extracted from vacancies | Remains authoritative until a versioned migration; must support product-required facts without redefining product policy |
| Decision Contract | How evidence becomes a recommendation | Remains authoritative until Decision Contract v2; must migrate to separate verdict/mode/company action and first-class Transferability/Career Value |
| Product Search SoT | Where the product searches, what it selects and shows, how the portfolio and pilot are governed | This approved document is canonical |
| CRM lifecycle contract | Actual application, outreach, and relationship state | Remains authoritative; `Pursue` is intent and cannot imply `application_submitted` |
| `docs/job-intel-improvements/jul19/job-intel-career-preference-system-development-sot.md` | Completed artifacts and benchmark evidence remain valid historical and technical inputs | Remaining product sequencing is superseded at decision record `PS-SOT-2026-08-10-v1`; bounded benchmark closure remains governed by §16 |
| `opportunity-thesis.md` | Historical opportunity hypothesis | Deprecated as policy wherever it conflicts with mandate-first evaluation or constrains the observable market |
| `opportunity_scoring.yaml` | Historical scoring policy | Deprecated as product authority; any reusable scoring is subordinate to Decision Contract v2 and this Product SoT |
| `docs/company-registry-seed.yaml` and `job_intel/seed/target_companies.yaml` | Company evidence and prioritization inputs | May seed Strategic Watchlist work but never define the market boundary or act as an allowlist |
| Other legacy company-discovery policy documents | Historical evidence and deprecated policy | Superseded wherever company membership, industry, geography, or title independently defines fit or market scope |

Conflict rule: candidate facts cannot be overridden by preference or product policy; vacancy facts cannot be overridden by evaluator inference; product policy cannot be changed by technical convenience. Any unresolved conflict blocks the affected behavior until a named owner decision and versioned migration exist.

## Appendix C. Review resolution record

Version 0.2.0 resolved the first review verdict `APPROVE WITH PRODUCT CHANGES` by:

- defining normative authority, versioning, conflict rules, and the supersession map;
- moving the dated production state into an informative evidence appendix;
- separating discovery origin, selection mode, opportunity verdict, and company action;
- normalizing Exploration volume and declaring the temporary pilot override;
- adding current-evaluator compatibility mapping and Decision Contract v2 requirements;
- restoring non-regression decision invariants;
- defining user-action metrics, denominators, observability states, counterfactual, and stop triggers;
- correcting Career Profile claims and expanding discovery vocabulary;
- introducing progressive disclosure and objective urgency rules;
- adding positive feedback causality and watchlist lifecycle;
- making company-event intelligence a separate weekly product.

The final re-review verdict `APPROVE WITH LIMITED CONTRACT CHANGES` is resolved in v1.0.0 by:

- separating industry, primary business model, business-model tags, and selection mode;
- defining the canonical nine-stage search funnel and the exact meaning of eligible, portfolio reviewed, selected, delivered, and meaningfully observed;
- preventing weak rejected roles from inflating qualified-new-company metrics;
- fixing the Monday–Friday digest cadence, weekly cap, and verdict eligibility for daily slots;
- separating positive decisions from activated opportunities and making activation per review hour the north star;
- limiting shadow-policy authority to metrics it can actually observe and requiring a comparable behavioral baseline;
- normalizing watchlist status, review freshness, company actions, transitions, and primary-origin precedence;
- restoring the one-axis Exploration invariant and requiring a reason for interpretable learning;
- tightening the minimum executive-scope gate;
- narrowing the Candidate Facts wording to facts supported by the structured resume;
- naming the prior process roadmap and legacy opportunity, scoring, and company-list artifacts in the supersession map;
- tightening urgency, feasibility-review timing, factual-error adjudication, and feedback reasons.

**Owner-directed decision retained across both reviews:** Kazakhstan is a normal eligible market with no minimum volume, and other Central Asian countries are independent markets under the global policy. This intentionally supersedes the prior fallback rule and must be reflected in subsequent Career Preference and Decision contract migrations.
