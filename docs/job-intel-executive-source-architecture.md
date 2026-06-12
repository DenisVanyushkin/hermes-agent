# Executive Job-Intel Source Architecture

## Executive Summary

The current job-intelligence system is no longer blocked by runtime stability. Its limiting factors are source quality, executive-role density, and weak company discovery. The present stack over-relies on a small number of broad search queries, low-yield remote-job boards, and a static target-company list that is too shallow to surface meaningful executive product opportunities consistently.

The recommended redesign is:

- Keep **LinkedIn** and **HeadHunter** as Tier 1 acquisition sources.
- Remove **RemoteOK** and **Remotive** from the core production path.
- Build Tier 2 around **structured ATS acquisition**, prioritizing platforms that expose public job feeds or stable job APIs.
- Replace the static target-company list with a **dynamic company discovery and watchlist system** driven by company signals and sector fit.
- Add a **hiring-signal layer** that ranks companies before vacancies appear.
- Split scoring into **company score** and **vacancy score**, with source trust and executive-density controls.

Recommended next implementation wave:

1. Greenhouse
2. Lever
3. Ashby
4. Teamtailor
5. SmartRecruiters
6. Personio

Secondary / conditional ATS sources:

- Recruitee
- Workday
- Comeet (Spark Hire Recruit)

Deprioritize or exclude for now:

- BambooHR
- Rippling
- RemoteOK
- Remotive

Expected impact:

- Higher executive-role density per run.
- Better vacancy metadata quality.
- Better company coverage in growth-stage SaaS, fintech, telecom-adjacent, marketplace, and AI product businesses.
- Less noise from generic remote boards.
- Better lead time by detecting companies before the executive role is formally posted.

## Diagnosis of the Current System

### What exists today

The current system uses:

- LinkedIn browser-native acquisition.
- HeadHunter browser-native acquisition.
- DuckDuckGo HTML search against a handful of broad discovery queries.
- RemoteOK API.
- Remotive API.
- A static target-company list from `job_intel/seed/target_companies.yaml`.
- A lightweight company monitor that crawls homepages and up to two career URLs.

Current role focus is seeded correctly at a high level. The system already aims at:

- VP Product
- Head of Product
- Chief Product Officer
- Director Product
- product transformation / digital product leadership
- monetization leadership

The scoring model also already values:

- monetization responsibility
- PnL ownership
- B2C / platform exposure
- fintech / telecom adjacency
- product transformation
- executive visibility

### Why the current system underperforms

#### 1. Too much of the system still behaves like a search scraper, not an executive-intelligence system

The current acquisition logic is vacancy-first and query-first. It tries to discover executive roles by probing public listings using a small rotating query set. That is acceptable for LinkedIn and HeadHunter, but it is structurally weak for the rest of the stack.

Executive roles are sparse, inconsistent in naming, and often hidden inside ATS systems or company-specific boards. A strategy built around generic search discovery misses the best companies and finds them too late.

#### 2. The non-browser sources are low-yield or noisy

Observed current behavior and code structure indicate:

- RemoteOK returns very few relevant executive product roles.
- Remotive is often empty for this target profile.
- DuckDuckGo relies on a tiny query budget and broad web search that is sensitive to ranking noise, network variability, and poor freshness control.

These sources may occasionally produce a relevant hit, but they do not appear structurally strong enough to justify core-system status for executive product discovery.

#### 3. Static target-company maintenance is not scalable

The current target-company layer is limited to a curated YAML list and shallow HTTP crawling.

Structural issues:

- The list must be maintained manually.
- It is biased toward known companies instead of discovering new opportunities.
- It does not adapt to funding, expansion, hiring spikes, or leadership change.
- It does not capture newly emerging companies in AI, fintech infrastructure, telecom-adjacent products, or fast-scaling B2C ecosystems.

#### 4. Company monitoring is too shallow for modern career infrastructure

The current company monitor reads homepages, discovers a small set of career URLs, and samples only a few links. That is low recall for modern JavaScript-heavy careers sites and ATS-backed pages.

This makes the “target company” source structurally underpowered even before scoring begins.

#### 5. Current scoring is vacancy-centric, not company-opportunity-centric

The current scoring logic is good at evaluating a single vacancy once found, but it does not yet turn company-level signals into acquisition priorities.

That means the system still waits for the vacancy instead of predicting where executive hiring is likely to happen next.

## Proposed Source Architecture

### Design principle

The redesigned stack should optimize for:

- executive role density
- structured metadata
- company discovery breadth
- signal lead time
- source reliability
- explainable prioritization

It should prefer fewer high-quality acquisition families over many low-trust low-yield feeds.

### Tier 1: Core vacancy acquisition

#### 1. LinkedIn

Role in the stack:

- Primary cross-market executive vacancy source.
- Best broad source for VP / Head / Director product roles across growth-stage and scaled companies.
- Strongest current source for cross-sector executive product openings.

Why keep:

- Highest expected executive-role density among broad public professional platforms.
- Strong coverage across SaaS, fintech, AI, product transformation, platform, and growth roles.
- Good signal for both global and regional product leadership hiring.

Limitations:

- Anti-bot pressure and session fragility.
- Search-ranking opacity.
- Public surface is not a structured open jobs API.

Recommendation:

- Keep as core Tier 1.
- Treat it as the highest-value discovery surface, but not as the only one.

#### 2. HeadHunter

Role in the stack:

- Primary source for CIS / Kazakhstan / nearby regional executive product roles.
- Strong local-market complement to LinkedIn.

Why keep:

- Better regional coverage where LinkedIn is weaker.
- Stronger visibility into companies hiring in Kazakhstan and nearby markets.
- Produces structured vacancy listings once runtime is stable.

Limitations:

- Weaker global executive coverage than LinkedIn.
- More regionally concentrated.

Recommendation:

- Keep as core Tier 1.
- Position it as regional depth, not global breadth.

### Tier 2: ATS platform layer

This layer should become the main replacement for weak generic search and remote-job boards.

ATS sources matter because executive product roles are often posted directly to the company ATS before or instead of broad aggregation.

Recommended Tier 2 priority order:

1. Greenhouse
2. Lever
3. Ashby
4. Teamtailor
5. SmartRecruiters
6. Personio
7. Recruitee
8. Workday
9. Comeet
10. BambooHR
11. Rippling

### Tier 3: Company and hiring-signal intelligence

This is not a vacancy source in the narrow sense. It is a prioritization engine that tells the acquisition layer where to look.

Functions:

- discover companies before they become obvious
- identify likely executive hiring conditions
- steer ATS crawling budget and LinkedIn / HeadHunter query budget
- decide which companies deserve intensive monitoring

This layer replaces the static target-company list.

### Tier 4: Experimental / opportunistic discovery

#### DuckDuckGo

Role:

- opportunistic web discovery only
- not a core source

Recommendation:

- downgrade to experimental only
- keep only if later evidence shows it helps discover ATS career surfaces or newly emerging companies faster than the signal layer

#### RemoteOK

Recommendation:

- remove from core production path

Why:

- too little executive product density
- too much remote-board noise
- better treated as an occasional watch source than a primary acquisition input

#### Remotive

Recommendation:

- remove from core production path

Why:

- weak executive-role yield
- usually too junior or too generic for the target profile
- low return for acquisition budget

## ATS Evaluation Matrix

### Greenhouse

- Executive-role coverage: High
- Accessibility: High
- API/feed availability: Strong
- Crawlability: High
- Anti-bot/auth friction: Low for public job-board reads
- Metadata richness: High
- Company-type fit: Strong for startups, scale-ups, SaaS, fintech, AI, and modern product companies
- Signal quality: High
- Expected vacancy yield: High
- Implementation complexity: Low to medium
- Recommendation: Include in first implementation wave

Why:

Greenhouse exposes a mature Job Board API and is deeply embedded in venture-backed tech hiring. It is one of the best structured acquisition targets for executive product roles outside LinkedIn.

### Lever

- Executive-role coverage: High
- Accessibility: High
- API/feed availability: Strong
- Crawlability: High
- Anti-bot/auth friction: Low for postings
- Metadata richness: High
- Company-type fit: Strong for startups, scale-ups, SaaS, fintech, growth companies
- Signal quality: High
- Expected vacancy yield: High
- Implementation complexity: Low to medium
- Recommendation: Include in first implementation wave

Why:

Lever has a well-known public job-posting surface and strong adoption in growth companies that hire product leaders.

### Ashby

- Executive-role coverage: High
- Accessibility: High
- API/feed availability: Strong public job postings API
- Crawlability: High
- Anti-bot/auth friction: Low
- Metadata richness: High
- Company-type fit: Excellent for modern venture-backed startups and scale-ups
- Signal quality: High
- Expected vacancy yield: Medium to high
- Implementation complexity: Low to medium
- Recommendation: Include in first implementation wave

Why:

Ashby is especially strong in newer tech companies, where many executive product roles appear early and are often not fully captured by older board-centric strategies.

### Teamtailor

- Executive-role coverage: Medium
- Accessibility: Medium to high
- API/feed availability: Strong API for jobs, requires API access
- Crawlability: High on hosted sites, medium for API-first integrations
- Anti-bot/auth friction: Medium
- Metadata richness: Medium to high
- Company-type fit: Good for EU-first growth companies and employer-brand-heavy organizations
- Signal quality: Medium
- Expected vacancy yield: Medium
- Implementation complexity: Medium
- Recommendation: Include in second part of first wave

Why:

Teamtailor is worth including because it has a real jobs API and good coverage in European growth companies, though executive density is lower than Greenhouse / Lever / Ashby.

### SmartRecruiters

- Executive-role coverage: Medium to high
- Accessibility: Medium
- API/feed availability: Strong customer APIs, public-access patterns vary by customer
- Crawlability: Medium
- Anti-bot/auth friction: Medium
- Metadata richness: High where accessible
- Company-type fit: Strong for larger mid-market and enterprise companies
- Signal quality: Medium to high
- Expected vacancy yield: Medium
- Implementation complexity: Medium to high
- Recommendation: Include after the first four ATS

Why:

SmartRecruiters broadens enterprise and larger-scale coverage, which matters for Director / GM Product / transformation roles.

### Personio

- Executive-role coverage: Medium
- Accessibility: Medium
- API/feed availability: Recruiting API plus XML feed
- Crawlability: Medium to high
- Anti-bot/auth friction: Medium
- Metadata richness: Medium
- Company-type fit: Strong in European SMB / mid-market hiring, weaker for global executive density
- Signal quality: Medium
- Expected vacancy yield: Medium-low for executive product roles, but useful in EU markets
- Implementation complexity: Medium
- Recommendation: Include after SmartRecruiters

Why:

Personio is structurally viable and better than generic search, but expected executive density is lower than Greenhouse / Lever / Ashby.

### Recruitee

- Executive-role coverage: Medium
- Accessibility: Medium
- API/feed availability: Careers Site API available
- Crawlability: Medium to high
- Anti-bot/auth friction: Medium
- Metadata richness: Medium
- Company-type fit: Mid-market and startup-friendly
- Signal quality: Medium
- Expected vacancy yield: Medium-low
- Implementation complexity: Medium
- Recommendation: Deprioritize but keep on roadmap

Why:

Recruitee is structurally usable, but not a top-yield executive source compared with the leading ATS platforms.

### Workday

- Executive-role coverage: Medium to high
- Accessibility: Low for structured public extraction
- API/feed availability: Enterprise APIs exist, but public job-feed acquisition is inconsistent and customer-specific
- Crawlability: Medium
- Anti-bot/auth friction: Medium to high
- Metadata richness: Medium
- Company-type fit: Strong for enterprise, large corporates, transformation hiring
- Signal quality: Medium
- Expected vacancy yield: Medium
- Implementation complexity: High
- Recommendation: Include as a specialized later-phase source

Why:

Workday matters for enterprise-scale executive hiring, but its public acquisition surface is inconsistent enough that it should not be early implementation priority.

### Comeet / Spark Hire Recruit

- Executive-role coverage: Low to medium
- Accessibility: Medium
- API/feed availability: Careers API and recruiting APIs exist
- Crawlability: Medium
- Anti-bot/auth friction: Medium
- Metadata richness: Medium
- Company-type fit: Niche / uneven
- Signal quality: Medium-low
- Expected vacancy yield: Low to medium
- Implementation complexity: Medium
- Recommendation: Deprioritize

Why:

It is technically viable, but likely lower-yield than the main ATS set.

### BambooHR

- Executive-role coverage: Low
- Accessibility: Medium
- API/feed availability: General API exists, public recruiting extraction story is weak for this use case
- Crawlability: Medium-low
- Anti-bot/auth friction: Medium
- Metadata richness: Medium-low
- Company-type fit: Better for SMB operational hiring than executive product leadership hiring
- Signal quality: Low
- Expected vacancy yield: Low
- Implementation complexity: Medium
- Recommendation: Exclude from near-term implementation

Why:

BambooHR is not where the next meaningful executive product hiring gains are likely to come from.

### Rippling

- Executive-role coverage: Unknown to low-medium for this use case
- Accessibility: Low to medium
- API/feed availability: General developer APIs exist, but public recruiting acquisition value is unclear
- Crawlability: Medium-low
- Anti-bot/auth friction: Medium
- Metadata richness: Unknown / inconsistent for public job extraction
- Company-type fit: Interesting long-term, weak immediate acquisition case
- Signal quality: Low to medium
- Expected vacancy yield: Low for now
- Implementation complexity: High relative to evidence
- Recommendation: Exclude from near-term implementation

Why:

Rippling Recruiting may become useful later, but there is not enough evidence that it will outperform easier ATS integrations for executive product discovery.

## Dynamic Company Discovery Architecture

### Objective

Replace the static watchlist with a system that continuously discovers promising companies and ranks them before vacancies are posted.

### Discovery inputs

The discovery layer should ingest company candidates from:

- funding data providers and deal databases
- startup / scale-up ecosystems
- company press releases and newsroom feeds
- leadership-change signals
- ATS job-volume changes
- sector-specific watchlists
- LinkedIn company growth cues
- app ecosystem and product-launch intelligence

### Candidate company entry logic

A company should enter the watchlist when it satisfies a weighted combination of:

- sector fit
- geography fit
- scale trajectory
- evidence of product or organizational change
- evidence of active hiring or internal restructuring
- presence on one or more high-value ATS surfaces

### Required enrichment layers

Each discovered company should be enriched with:

- canonical company identity
- domain and careers URLs
- ATS platform type
- sector classification
- growth stage classification
- geography footprint
- business model tags
- signal history
- current hiring intensity
- leadership-change history
- last refreshed timestamp

### Watchlist exit logic

A company should leave or downgrade when:

- no relevant signals recur for a fixed period
- no executive or strategic-product openings appear over repeated cycles
- the company drifts outside sector or geography fit
- signals are repeatedly false-positive or low quality
- hiring activity collapses relative to peer set

### Refresh cadence

Recommended cadence:

- company signal refresh: daily
- ATS job-surface refresh for active watchlist: daily or twice daily
- full watchlist reprioritization: weekly
- low-priority candidate revalidation: biweekly or monthly

### False-positive controls

Use layered controls:

- require at least two independent signals before promotion to high-priority watchlist
- downgrade sources that repeatedly produce low-fit or non-executive roles
- separate company-interest score from immediate vacancy score
- track whether prior signals actually converted into relevant roles

## Hiring-Signal Architecture

### Signal taxonomy

#### 1. Funding and capital events

Examples:

- Seed to Series D+ rounds
- debt or strategic financing for expansion
- investor-led scaling announcements

Predictive value: High for growth-stage product-org expansion
Observability: High
Freshness: High
Likely sources: funding databases, press releases, company newsrooms
False-positive risk: Medium
Use: Boost company priority, especially for SaaS, fintech, AI, platform, and marketplace firms

#### 2. Executive departures or leadership changes

Examples:

- CPO departure
- VP Product departure
- new CEO / COO / GM with product-led mandate
- new CTO / Chief Digital Officer in transformation cases

Predictive value: Very high
Observability: Medium
Freshness: Medium to high
Likely sources: LinkedIn, press releases, leadership pages, company announcements
False-positive risk: Medium
Use: Strong promotion signal for executive-role monitoring

#### 3. Expansion into new markets

Examples:

- launch into GCC, Europe, CIS, APAC
- cross-border product or payments expansion

Predictive value: High
Observability: Medium
Freshness: Medium
Likely sources: PR, company blogs, investor updates, hiring footprint changes
False-positive risk: Medium
Use: Strong boost for GM Product, regional VP Product, platform and monetization leadership roles

#### 4. New product lines or platform bets

Examples:

- launch of new subscription line
- launch of AI product suite
- new ecosystem or marketplace initiative
- major product-platform reorg

Predictive value: High
Observability: Medium
Freshness: Medium
Likely sources: product launches, blogs, newsroom, app stores, earnings commentary
False-positive risk: Medium
Use: Boost both company score and future vacancy expectations

#### 5. M&A and reorganizations

Examples:

- acquisition integration
- spinout or merger
- centralization of product leadership

Predictive value: Medium to high
Observability: High
Freshness: Medium
Likely sources: PR, regulatory filings, earnings and investor updates
False-positive risk: Medium-high
Use: Good signal for transformation and GM / Director / VP roles

#### 6. Hiring-activity spikes

Examples:

- sudden growth in PM / design / analytics / growth / platform hiring
- multiple product-adjacent openings across one company

Predictive value: Medium to high
Observability: High
Freshness: High
Likely sources: ATS inventories, LinkedIn job surfaces, job-volume deltas
False-positive risk: Low to medium
Use: Promote watchlist priority and increase crawl budget

## Scoring Model

### Company score

Purpose:

Rank where the system should spend discovery attention.

Suggested dimensions:

- Sector fit: 20
- Business-model fit: 15
- Growth / hiring signals: 20
- Leadership-change signals: 15
- Expansion / transformation signals: 10
- Source trustworthiness: 10
- ATS accessibility / observability: 5
- Freshness of signals: 5

Interpretation:

- 80-100: aggressively monitor
- 60-79: active watchlist
- 40-59: low-priority watchlist
- below 40: archive / monitor passively

### Vacancy score

Purpose:

Rank whether an opening is worth notifying and prioritizing.

Suggested dimensions:

- Executive seniority confidence: 25
- Role relevance to product leadership target: 20
- Company score carryover: 15
- Business-model / sector fit: 10
- Geography fit: 10
- Monetization / PnL / growth responsibility: 10
- Recency / freshness: 5
- Source trustworthiness and metadata quality: 5

### Confidence handling

Each score should be accompanied by confidence bands:

- High confidence: structured ATS or high-quality LinkedIn / HH evidence with clear metadata
- Medium confidence: partial structured evidence or inferred fit
- Low confidence: broad search inference with limited metadata

Confidence must influence downstream notification thresholds.

### Missing-data behavior

- Missing salary should not destroy score.
- Missing company metadata should lower confidence, not automatically reject.
- Missing location should reduce confidence and geography fitness.
- Missing company context should trigger enrichment before alerting, not immediate promotion.

### Preventing noisy sources from dominating

The system must not let high-volume low-quality sources outrank structured high-trust sources.

Controls:

- cap raw contribution from noisy sources
- add source-trust multiplier
- separate role-title relevance from company quality
- require stronger score for noisy-source promotions
- decay sources that repeatedly yield rejects or generic PM roles

## Expected Signal Quality and Vacancy Yield

### Highest expected yield

1. LinkedIn
2. Greenhouse
3. Lever
4. Ashby
5. HeadHunter (regional strength)

### High-value but lower-volume sources

- Teamtailor
- SmartRecruiters
- Personio
- Recruitee

### High-complexity specialized source

- Workday

Useful for enterprise transformation roles, but not an early efficiency play.

### Low-value or low-priority sources

- RemoteOK
- Remotive
- BambooHR
- Rippling
- Comeet / Spark Hire Recruit

### Best marginal gains

The best near-term gains are expected from:

1. Greenhouse and Lever ingestion
2. Ashby ingestion
3. dynamic company discovery
4. hiring-signal layer
5. Teamtailor and SmartRecruiters expansion

This sequence improves both vacancy capture and company prioritization without overinvesting in weak feeds.

## Implementation Roadmap

### Phase 1: Source rationalization

- Remove RemoteOK and Remotive from core production acquisition.
- Downgrade DuckDuckGo to experimental discovery only.
- Keep LinkedIn and HeadHunter as the only Tier 1 production sources.

### Phase 2: ATS foundation

- Implement Greenhouse ingestion first.
- Add Lever second.
- Add Ashby third.
- Normalize ATS metadata into a common vacancy model.
- Add per-source trust and executive-density tracking.

### Phase 3: ATS expansion

- Add Teamtailor and SmartRecruiters.
- Add Personio.
- Add Recruitee if ATS yield metrics justify it.

### Phase 4: Dynamic company discovery

- Replace static target-company YAML as the primary system of discovery.
- Build company candidate ingestion and enrichment.
- Introduce watchlist lifecycle management.

### Phase 5: Hiring-signal layer

- Add funding, leadership, expansion, and hiring-intensity signals.
- Feed those signals into company prioritization.
- Use company score to allocate crawl budget.

### Phase 6: Enterprise-specialized expansion

- Evaluate Workday with a narrower enterprise-playbook approach.
- Only add Comeet, BambooHR, or Rippling if live evidence shows meaningful executive yield.

## Final Recommendations

### Keep

- LinkedIn
- HeadHunter

### Add first

- Greenhouse
- Lever
- Ashby

### Add second

- Teamtailor
- SmartRecruiters
- Personio

### Conditional / later

- Recruitee
- Workday
- Comeet

### Remove or exclude from core path

- RemoteOK
- Remotive
- BambooHR
- Rippling

### Downgrade

- DuckDuckGo to experimental discovery only
- static target-company list to temporary seed input only

## Reference Links

- Greenhouse Job Board API: https://developers.greenhouse.io/job-board
- Greenhouse API overview: https://support.greenhouse.io/hc/en-us/articles/10568627186203-Greenhouse-API-overview
- Lever developer docs: https://hire.lever.co/developer/documentation
- Lever developer overview: https://hire.lever.co/developer
- Ashby Job Postings API: https://developers.ashbyhq.com/docs/public-job-posting-api
- Ashby custom careers page via API: https://docs.ashbyhq.com/build-an-entirely-custom-careers-page-using-the-ashby-api
- Teamtailor API: https://docs.teamtailor.com/
- SmartRecruiters customer API overview: https://developers.smartrecruiters.com/docs/customer-overview
- SmartRecruiters API reference: https://developers.smartrecruiters.com/docs/api-reference
- Personio Recruiting API overview: https://support.personio.de/hc/en-us/articles/360000314338-The-Personio-Recruiting-API
- Personio developer getting started: https://developer.personio.de/docs/getting-started-with-the-personio-api
- Recruitee API documentation: https://support.recruitee.com/articles/1066282-api-documentation/
- Workday Recruiting datasheet: https://www.workday.com/content/dam/web/en-us/documents/datasheets/datasheet-workday-recruiting.pdf
- Workday marketplace posting integration example: https://marketplace.workday.com/en-US/apps/414131/equest-job-board-posting-for-workday-recruiting/overview
- Comeet / Spark Hire Recruit developer portal: https://developers.comeet.com/
- BambooHR API docs: https://documentation.bamboohr.com/docs
- BambooHR ATS product page: https://www.bamboohr.com/applicant-tracking
- Rippling developer API access: https://developer.rippling.com/documentation/developer-portal/getting-started/api
- Rippling Recruiting product page: https://www.rippling.com/talent
