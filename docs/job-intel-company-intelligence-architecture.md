# Executive Job-Intel Company Intelligence Architecture

## Executive Summary

The next redesign step should make the system **company-first**, not vacancy-first.

The current ATS plan remains valid, but ATS ingestion should no longer be the primary discovery engine. It should become a **confirmation and capture layer** after the system has already identified promising companies and likely executive hiring conditions.

The revised architecture should prioritize:

1. **Company Discovery**
2. **Hiring Signals**
3. **LinkedIn Company Intelligence**
4. **Product Growth Signals**
5. **ATS Ingestion**
6. **Vacancy Scoring and Notification**

The goal is not to wait for a VP Product or Head of Product role to appear. The goal is to detect the companies that are likely to need such a role **months before the job is posted**.

The recommended result is a pipeline that:

- discovers companies before they become obvious,
- ranks them by strategic relevance and executive hiring likelihood,
- monitors organizational and growth signals continuously,
- and then uses ATS, LinkedIn, and HeadHunter as downstream evidence-collection layers.

## Why the System Must Become Company-First

Executive product roles are sparse and late-surface by nature.

Most VP Product, CPO, GM Product, and Product Strategy leadership openings are preceded by company-level changes such as:

- new funding,
- aggressive hiring growth,
- market expansion,
- product-line expansion,
- leadership turnover,
- reorganization,
- AI platform bets,
- or a shift from founder-led product management to scaled product leadership.

A vacancy-first architecture reacts too late.

A company-first architecture gives three advantages:

- better lead time,
- better company quality control,
- and better search efficiency because ATS and vacancy crawling are directed toward the highest-probability companies.

## Revised Intelligence Architecture

### Layer 1: Company Discovery

Purpose:

- find relevant companies before they post executive product roles,
- keep the watchlist fresh without manual maintenance,
- widen coverage beyond already-known target companies.

Outputs:

- candidate companies,
- company metadata,
- growth stage,
- sector fit,
- geographic footprint,
- ATS presence,
- confidence score.

### Layer 2: Hiring Signals

Purpose:

- determine which discovered companies are moving toward executive product hiring conditions.

Outputs:

- signal events,
- signal freshness,
- signal confidence,
- company-priority uplift,
- predicted hiring-likelihood uplift.

### Layer 3: LinkedIn Company Intelligence

Purpose:

- convert company candidates into operational evidence around hiring growth, leadership change, and organization shape.

Outputs:

- headcount-growth direction,
- hiring-growth direction,
- leadership movement,
- location expansion,
- product-org change indicators.

### Layer 4: Product Growth Signals

Purpose:

- detect real product traction, category momentum, app adoption, traffic growth, and technical platform expansion.

Outputs:

- product momentum score,
- market-expansion evidence,
- category breakout indicators,
- growth acceleration flags.

### Layer 5: ATS Ingestion

Purpose:

- confirm and capture actual openings from already-prioritized companies.

Outputs:

- structured vacancies,
- role metadata,
- recency,
- level and location data.

### Layer 6: Vacancy Scoring and Notification

Purpose:

- decide which roles deserve alerting.

Outputs:

- ranked opportunities,
- confidence labels,
- notification payloads.

## Company Discovery Subsystem

### Evaluation Criteria

Each company-discovery source is evaluated on:

- accessibility,
- freshness,
- signal quality,
- implementation complexity,
- expected yield.

Expected yield here means expected usefulness for surfacing companies that are likely to generate executive product opportunities, not raw article count.

## Company Discovery Source Matrix

### 1. Crunchbase

- Accessibility: Medium
- Freshness: High
- Signal quality: High
- Implementation complexity: Medium
- Expected yield: Very high

Why:

Crunchbase is one of the strongest structured sources for company-first discovery because it already organizes firms around funding, firmographics, investors, industry tags, acquisitions, and recent news. Its data products explicitly cover funding data, company filters, recent news, and predictive company intelligence. That makes it highly suitable for identifying companies moving into the scale band where executive product hiring appears.

Best use:

- funding-based watchlist seeding,
- investor-backed company discovery,
- new category entrants,
- geography and stage filtering,
- leadership- and news-trigger enrichment.

Recommendation:

- Include in first company-intelligence wave.
- Treat as a primary backbone source.

### 2. Dealroom

- Accessibility: Medium
- Freshness: High
- Signal quality: Very high
- Implementation complexity: Medium
- Expected yield: Very high

Why:

Dealroom is structurally one of the best sources for this problem, especially for Europe, MENA-adjacent ecosystems, and growth companies. It explicitly emphasizes startups, growth companies, ecosystem mapping, headcount and growth signals, web traffic, app downloads, and business-model context. That is stronger than a vacancy board because it supports upstream company ranking.

Best use:

- growth-stage company discovery,
- European scale-up coverage,
- traffic and app-growth enrichment,
- sector clustering,
- ecosystem and geographic expansion tracking.

Recommendation:

- Include in first company-intelligence wave.
- Rank alongside Crunchbase as a foundational source.

### 3. Wellfound

- Accessibility: High
- Freshness: High
- Signal quality: Medium
- Implementation complexity: Low to medium
- Expected yield: Medium

Why:

Wellfound is valuable less for direct executive-role capture and more for startup visibility, hiring posture, and company metadata in venture-backed and startup-heavy ecosystems. It covers a large universe of startups and jobs, but its role density is skewed more toward startup operating roles than true executive product leadership.

Best use:

- startup discovery,
- company hiring-activity signal,
- startup-company enrichment,
- triangulating whether a company is actively building out product, engineering, growth, or monetization teams.

Recommendation:

- Include as a secondary company-discovery source.
- Use more for startup detection and hiring posture than for direct executive search.

### 4. TechCrunch

- Accessibility: High
- Freshness: High
- Signal quality: Medium to high
- Implementation complexity: Low
- Expected yield: Medium to high

Why:

TechCrunch is not a structured database, but it is a strong high-freshness signal layer for funding announcements, product launches, acquisitions, leadership shifts, and expansion moves. It is useful because many likely executive-product opportunities are preceded by exactly the kind of events TechCrunch covers.

Best use:

- event-driven company discovery,
- funding and acquisition signal capture,
- product launch and strategy change monitoring,
- watchlist priority boosts.

Recommendation:

- Include in first signal-oriented discovery wave.
- Use as event intelligence, not as a canonical company system of record.

### 5. Sifted

- Accessibility: High
- Freshness: High
- Signal quality: Medium to high
- Implementation complexity: Low
- Expected yield: Medium

Why:

Sifted is valuable for European startup and scale-up intelligence. It is editorial rather than structured, but it is focused on the right company population: fast-moving tech companies, funding, scaling, and European market expansion.

Best use:

- European company discovery,
- editorial enrichment,
- leadership- and expansion-signal monitoring,
- supplementing Dealroom and LinkedIn for EU-first growth companies.

Recommendation:

- Include as a secondary Europe-focused signal source.

### 6. EU-Startups

- Accessibility: High
- Freshness: Medium
- Signal quality: Medium
- Implementation complexity: Low
- Expected yield: Medium-low

Why:

EU-Startups is useful as a niche ecosystem feed for emerging European companies, but weaker than Dealroom and Sifted for robust company-intelligence coverage. It is better treated as a lightweight discovery supplement.

Best use:

- early European company spotting,
- smaller ecosystem monitoring,
- supplementing Europe-focused watchlist refreshes.

Recommendation:

- Include only as a tertiary EU discovery source.

### 7. Product Hunt

- Accessibility: High
- Freshness: Very high
- Signal quality: Medium-low
- Implementation complexity: Low
- Expected yield: Medium-low

Why:

Product Hunt is useful for identifying product launches, AI-first startups, tooling startups, and newly emerging SaaS products. It is strong for freshness but noisy as a company-quality signal. Product Hunt popularity does not reliably imply durable company quality or executive-hiring intent.

Best use:

- new product-line detection,
- AI product launch discovery,
- early-stage company spotting,
- identifying categories that may later require formal product leadership.

Recommendation:

- Include as a niche early-signal source, not as a ranking backbone.
- Weight it lightly unless reinforced by funding, hiring, or traction signals.

### 8. G2

- Accessibility: Medium
- Freshness: Medium
- Signal quality: High for B2B software intent, lower for general company discovery
- Implementation complexity: Medium
- Expected yield: Medium

Why:

G2 is not a general startup-discovery source. It is useful when the target company set is B2B SaaS and the system wants evidence of category traction, buyer interest, market presence, and product-level momentum. Its buyer-intent and marketplace activity are more useful as growth/traction signals than as company discovery inputs.

Best use:

- B2B SaaS prioritization,
- validating product-market traction,
- identifying categories where a company is becoming commercially significant,
- detecting strong product-led or GTM-led scaling context.

Recommendation:

- Include, but under Product Growth / traction intelligence rather than core company discovery.

### 9. CB Insights

- Accessibility: Medium to low
- Freshness: High
- Signal quality: Very high
- Implementation complexity: Medium to high
- Expected yield: High

Why:

CB Insights is structurally strong for private-company intelligence, growth signals, market mapping, and early strategic movement. It offers predictive intelligence, headcount history, market and company analysis, and API/data delivery. The downside is likely access cost and implementation friction.

Best use:

- premium company prioritization,
- headcount and growth pattern detection,
- sector mapping,
- identifying companies entering product-scaling mode.

Recommendation:

- Include if access is available.
- Rank as a premium high-value source, but not as the first integration if access is operationally constrained.

### 10. YC Companies

- Accessibility: High
- Freshness: Medium
- Signal quality: Medium to high
- Implementation complexity: Low
- Expected yield: Medium

Why:

YC's directory is a strong curated universe for startup discovery, especially in SaaS, AI, fintech, devtools, and infrastructure. It is not enough by itself because it is cohort-biased and startup-biased, but it is very useful as a seed layer for company watchlists and for finding companies before broad mainstream visibility.

Best use:

- startup watchlist seeding,
- AI and SaaS company sourcing,
- matching portfolio-stage companies to growth and hiring signals later.

Recommendation:

- Include as a seed-source, not as a standalone intelligence backbone.

## Company Discovery Source Ranking

### Tier A: Core company discovery

1. Crunchbase
2. Dealroom
3. CB Insights (if access is available)

### Tier B: High-value secondary discovery

4. Wellfound
5. TechCrunch
6. Sifted
7. YC Companies

### Tier C: Opportunistic / niche

8. Product Hunt
9. G2
10. EU-Startups

## Executive Search Source Layer

### Research question

Can meaningful executive-product opportunities be extracted from retained executive search firms such as:

- Spencer Stuart
- Russell Reynolds
- Korn Ferry
- Heidrick & Struggles
- Egon Zehnder

### Structural conclusion

These firms are **not good direct acquisition sources** for executive product opportunities.

Why:

- retained executive searches are usually confidential,
- public websites are built around firm capabilities and thought leadership, not open searchable assignments,
- most real executive-product mandates are not posted as publicly extractable vacancies,
- published content is heavily advisory and brand-oriented.

That makes them weak as direct vacancy sources even though they are highly relevant players in executive hiring.

## Executive Search Firm Evaluation

### Spencer Stuart

- Meaningful public opportunity extraction: Low
- Signal quality if something is published: High but rare
- Implementation complexity: Medium
- Expected yield: Low

Notes:

Spencer Stuart clearly operates C-level and CPO executive search practices, including a dedicated Chief Product Officer practice. That confirms market relevance, but it does not create a reliable open-opportunity source.

Recommendation:

- Do not use as a direct vacancy source.
- Use only as indirect market evidence that the firm actively covers CPO and product leadership mandates.

### Russell Reynolds

- Meaningful public opportunity extraction: Low
- Signal quality if published: High but rare
- Implementation complexity: Medium
- Expected yield: Low

Notes:

Russell Reynolds explicitly covers executive search and leadership advisory, including technology, data, digital, and growth functions. The public surface is still mostly advisory rather than assignment-driven.

Recommendation:

- Exclude as a primary source.
- Possible niche use: monitor public leadership-transition content or sector insights, not open roles.

### Korn Ferry

- Meaningful public opportunity extraction: Low
- Signal quality if published: Medium
- Implementation complexity: Medium
- Expected yield: Low

Notes:

Korn Ferry is highly relevant institutionally, but its public web presence is dominated by consulting and talent solutions rather than extractable executive product assignments.

Recommendation:

- Exclude from core opportunity acquisition.

### Heidrick & Struggles

- Meaningful public opportunity extraction: Low
- Signal quality if published: High but rare
- Implementation complexity: Medium
- Expected yield: Low

Notes:

Heidrick is clearly a top retained executive search and leadership advisory firm. That matters for market context, but not for systematic opportunity extraction.

Recommendation:

- Exclude from direct acquisition.

### Egon Zehnder

- Meaningful public opportunity extraction: Low
- Signal quality if published: High but rare
- Implementation complexity: Medium
- Expected yield: Low

Notes:

Egon Zehnder explicitly covers chief product officers, technology officers, and leadership advisory. That makes it useful as evidence that executive product hiring is a mature search category, but still not an efficient extraction target.

Recommendation:

- Exclude from direct acquisition.

## Executive Search Layer Recommendation

Do not treat retained executive search firms as a meaningful vacancy source layer.

Use them only for:

- market taxonomy validation,
- function naming validation,
- occasional high-signal press releases or public leadership-transition announcements.

They are not worth implementation priority compared with company discovery, LinkedIn company intelligence, or ATS.

## LinkedIn Company Intelligence Layer

This layer should become one of the strongest sources of downstream company evidence after a company is discovered elsewhere.

### Objectives

Track:

- hiring growth,
- headcount growth,
- leadership changes,
- organizational changes,
- geographic expansion.

### Why LinkedIn matters here

LinkedIn is not only a vacancy surface. It is also the richest publicly visible professional graph for:

- job openings,
- team composition,
- talent movement,
- leadership mobility,
- company footprint changes,
- and role clustering by geography and function.

LinkedIn Talent Insights and the broader LinkedIn data model explicitly support workforce trends, talent movement, hiring demand, and company comparison logic. Even without formal enterprise product access, the public company and jobs surfaces are still strong evidence layers.

## LinkedIn Company Intelligence Design

### 1. Hiring Growth

Signals:

- increasing number of product, growth, analytics, platform, monetization, data, or design openings,
- multi-location hiring for product-adjacent roles,
- sudden rise in senior product openings relative to baseline,
- new hiring clusters around a business unit or geography.

Value:

This is one of the strongest near-term precursors to VP Product or Head of Product hiring.

### 2. Headcount Growth

Signals:

- visible expansion in employee count ranges,
- rising role density in product, engineering, data, monetization, or market-expansion functions,
- workforce growth in specific regions.

Value:

Headcount growth without product leadership expansion often precedes formalizing product leadership.

### 3. Leadership Changes

Signals:

- departure of CPO, VP Product, GM Product, Chief Digital Officer, Chief Growth Officer,
- arrival of new CEO or regional GM,
- arrival of transformation-oriented CTO or COO,
- executive reshuffling across strategy, product, growth, revenue, or regional leadership.

Value:

Very high. This is often the single strongest precursor to executive product search.

### 4. Organizational Changes

Signals:

- company begins hiring in platform, growth, monetization, lifecycle, or AI-product pods,
- function titles shift from PM-only hiring toward director/lead/GM/VP structures,
- company starts adding cross-functional roles that indicate scaling complexity.

Value:

Strong. It indicates product organization formalization, not just tactical hiring.

### 5. Geographic Expansion

Signals:

- new product or growth hiring in UAE, Saudi Arabia, Kazakhstan, Eastern Europe, Germany, UK, or remote hubs,
- multiple new office footprints,
- regional GM or go-to-market buildout.

Value:

Strong for product-strategy, growth, platform, and GM Product opportunities.

## LinkedIn Company Intelligence Recommendation

Treat LinkedIn as two separate layers:

1. **LinkedIn Jobs** for direct vacancy capture.
2. **LinkedIn Company Intelligence** for company-priority scoring.

The second one should become more strategically important than the first.

## Product Growth Signals

This layer should estimate whether a company’s products are accelerating strongly enough to require more senior product leadership.

### Evaluation targets

Research focus:

- app growth,
- traffic growth,
- revenue growth,
- market expansion.

## Product Growth Source Evaluation

### Similarweb

- Accessibility: Medium
- Freshness: High
- Signal quality: High for web traffic and engagement direction
- Implementation complexity: Medium
- Expected yield: High

Why:

Similarweb is strong for website traffic, engagement, category standing, and digital growth direction. Its API and methodology also explicitly support app and web intelligence. It is excellent for identifying companies whose product adoption or market share is moving materially.

Best use:

- web traffic growth,
- competitive traffic comparison,
- category breakout detection,
- geographic traffic mix,
- early commercial traction validation.

Recommendation:

- Include in the first product-growth intelligence wave.

### Product Hunt

- Accessibility: High
- Freshness: Very high
- Signal quality: Medium-low
- Implementation complexity: Low
- Expected yield: Medium-low

Why:

Product Hunt is useful as an early launch detector, especially for AI and SaaS products, but weak as a durable growth indicator.

Recommendation:

- Use only as an early-stage momentum signal.
- Never treat it as evidence of sustained scale.

### App Store

- Accessibility: High for public charts; low for internal analytics beyond own apps
- Freshness: High
- Signal quality: Medium to high for app-category momentum
- Implementation complexity: Medium
- Expected yield: Medium

Why:

Public App Store charts and metadata help identify mobile-app momentum, category movement, and app-market expansion. They are more useful for consumer, fintech, subscription, and marketplace apps than for pure B2B SaaS.

Recommendation:

- Include where mobile products matter.
- Use as a product-momentum signal, not a general company-discovery backbone.

### Google Play

- Accessibility: High for public surfaces
- Freshness: High
- Signal quality: Medium to high for mobile traction direction
- Implementation complexity: Medium
- Expected yield: Medium

Why:

Google Play provides strong directional mobile adoption evidence in Android-heavy regions and for consumer, fintech, telecom, and marketplace products.

Recommendation:

- Include alongside App Store for mobile-heavy sectors.

### Sensor Tower

- Accessibility: Medium
- Freshness: High
- Signal quality: High
- Implementation complexity: Medium to high
- Expected yield: High

Why:

Sensor Tower provides app intelligence and, increasingly, cross-channel behavior insight. It is valuable for app-download growth, category position, and mobile-market expansion.

Recommendation:

- Include if access is available.
- Rank high for consumer, fintech, marketplace, telecom, and subscription-app contexts.

### data.ai

- Accessibility: Medium to low
- Freshness: High
- Signal quality: High
- Implementation complexity: Medium to high
- Expected yield: High

Why:

data.ai historically has been one of the strongest app-market intelligence providers. It is now a Sensor Tower company, but as a signal family it remains strong for market size, downloads, usage, and app performance benchmarking.

Recommendation:

- Treat as premium app-growth intelligence if commercial access exists.
- Avoid duplicative implementation if Sensor Tower already covers the same need.

### BuiltWith

- Accessibility: High
- Freshness: Medium to high
- Signal quality: Medium
- Implementation complexity: Low to medium
- Expected yield: Medium

Why:

BuiltWith is not a growth metric source in the strict sense, but it is a powerful technographic indicator. It helps identify platform sophistication, ecommerce stack changes, AI adoption, payments tooling, data infrastructure, and modern product architecture. That can be a good proxy for product and platform scaling.

Recommendation:

- Include as a supporting enrichment layer.
- Use it for company sophistication and stack-change evidence, not revenue estimation.

### G2

- Accessibility: Medium
- Freshness: Medium
- Signal quality: High for B2B category traction
- Implementation complexity: Medium
- Expected yield: Medium

Why:

G2 is a strong B2B SaaS traction indicator because it reflects buyer research, category presence, comparisons, and marketplace engagement. It is much more useful for B2B software than for consumer apps or telecom.

Recommendation:

- Include as a B2B product-traction signal source.

## Product Growth Source Ranking

### Tier A

1. Similarweb
2. Sensor Tower
3. data.ai

### Tier B

4. App Store
5. Google Play
6. G2
7. BuiltWith

### Tier C

8. Product Hunt

## Revised Priority Model

The architecture should no longer treat ATS as the central discovery surface.

### Recommended priority order

1. Company Discovery
2. Hiring Signals
3. LinkedIn Company Intelligence
4. Product Growth Signals
5. ATS Ingestion
6. Vacancy Ranking

This means a company should first become interesting because:

- it raised funding,
- it is scaling headcount,
- it is entering new markets,
- it is launching new products,
- it shows product traction,
- or it has undergone a leadership or organizational shift.

Only then should ATS crawling and vacancy capture spend meaningful budget on it.

## Company-First Scoring Model

### Company Discovery Score

Dimensions:

- sector fit,
- company stage,
- investor / funding quality,
- hiring activity,
- ATS visibility,
- geography fit,
- product sophistication,
- growth momentum.

### Hiring-Likelihood Score

Dimensions:

- executive departure signal,
- senior-product hiring cluster,
- headcount acceleration,
- expansion signal,
- new product-line signal,
- reorganization signal,
- market momentum signal.

### Product Momentum Score

Dimensions:

- traffic growth,
- app ranking growth,
- category visibility,
- buyer-intent evidence,
- technology-stack expansion,
- product-launch recency.

### Vacancy Score

This becomes the final layer, not the first layer.

Dimensions:

- executive seniority confidence,
- role relevance,
- company score carryover,
- hiring-likelihood carryover,
- source trust,
- recency.

## Expected Yield by Layer

### Highest strategic yield

1. Company Discovery + Hiring Signals together
2. LinkedIn Company Intelligence
3. ATS ingestion

### Highest lead-time advantage

1. Crunchbase
2. Dealroom
3. LinkedIn Company Intelligence
4. TechCrunch / Sifted
5. Similarweb / app-growth intelligence

### Highest direct vacancy yield

1. LinkedIn Jobs
2. ATS ingestion
3. HeadHunter

### Lowest-priority surfaces

- retained executive search firms as public sources,
- generic remote job boards,
- broad undirected web search.

## Implementation Roadmap

This is a design roadmap only, not an implementation instruction.

### Phase 1: Company backbone

- Add Crunchbase-backed company discovery.
- Add Dealroom-backed company discovery.
- Build canonical company identity records.
- Replace static target-company YAML as the primary discovery mechanism.

### Phase 2: Signal backbone

- Add event ingestion from TechCrunch and Sifted.
- Add funding, expansion, leadership-change, and hiring-activity signal models.
- Introduce company-priority and hiring-likelihood scoring.

### Phase 3: LinkedIn Company Intelligence

- Add company-level monitoring for hiring growth, headcount growth, geographic expansion, and leadership shifts.
- Separate LinkedIn company-intelligence outputs from LinkedIn vacancy outputs.

### Phase 4: Product Growth Intelligence

- Add Similarweb as the first traction signal source.
- Add mobile-intelligence sources where relevant sectors justify them.
- Add BuiltWith and G2 as enrichment layers.

### Phase 5: ATS becomes downstream

- Only after company and signal ranking are working, expand ATS ingestion from the previously approved ATS architecture.
- Use company priority to allocate ATS crawl budget.

### Phase 6: Continuous validation

- Measure which company signals actually precede executive product openings.
- Decay or remove sources that do not improve lead time or opportunity quality.

## Final Recommendations

### Build first

- Crunchbase
- Dealroom
- LinkedIn Company Intelligence
- hiring-signal taxonomy
- Similarweb

### Add next

- TechCrunch
- Sifted
- Wellfound
- YC Companies
- G2
- BuiltWith

### Conditional / premium

- CB Insights
- Sensor Tower
- data.ai

### Deprioritize

- Product Hunt as a primary signal
- EU-Startups as a primary signal
- executive search firms as direct opportunity sources

### Exclude from direct executive opportunity extraction

- Spencer Stuart
- Russell Reynolds
- Korn Ferry
- Heidrick & Struggles
- Egon Zehnder

## Reference Links

- Crunchbase Data: https://data.crunchbase.com/
- Crunchbase company/funding filters: https://support.crunchbase.com/hc/en-us/articles/30493799544211-Companies-Filters-Definitions
- Dealroom platform: https://dealroom.co/
- Dealroom data overview: https://knowledge.dealroom.co/knowledge/what-is-dealroom.co
- Wellfound jobs: https://wellfound.com/jobs/
- Wellfound company/job seeker coverage: https://help.wellfound.com/article/1132-what-kind-of-job-seekers-and-companies-will-i-find-on-wellfound
- TechCrunch startups: https://techcrunch.com/category/startups/
- TechCrunch funding: https://techcrunch.com/tag/funding/
- Sifted about: https://sifted.eu/about
- EU-Startups about: https://www.eu-startups.com/about/
- Product Hunt about: https://www.producthunt.com/about
- Product Hunt launch guide: https://www.producthunt.com/launch
- G2 buyer intent data: https://sell.g2.com/data
- G2 buyer intent docs: https://documentation.g2.com/docs/buyer-intent
- CB Insights: https://www.cbinsights.com/
- CB Insights data: https://www.cbinsights.com/what-we-offer/data/
- YC directory: https://www.ycombinator.com/blog/the-yc-directory/
- YC companies directory: https://www.ycombinator.com/companies/industry/search
- Spencer Stuart executive search: https://www.spencerstuart.com/home
- Spencer Stuart Chief Product Officer practice: https://www.spencerstuart.com/what-we-do/functional-roles/technology-officer/chief-product-officer
- Russell Reynolds executive search: https://www.russellreynolds.co/en/capabilities/how-do-i-find-the-best-leaders/executive-search
- Heidrick & Struggles home: https://www.heidrick.com/en/Home/
- Heidrick about: https://www.heidrick.com/en/about-us
- Korn Ferry: https://www.kornferry.com/
- Korn Ferry story: https://www.kornferry.com/about-us/our-story
- Egon Zehnder technology officers / chief product officers: https://www.egonzehnder.com/functions/technology-officers
- LinkedIn Talent Insights launch: https://www.linkedin.com/business/talent/blog/product-tips/linkedin-talent-insights-now-available
- LinkedIn Talent Pool reports: https://www.linkedin.com/help/recruiter/answer/a188042/create-talent-pool-reports-in-talent-insights?lang=en
- LinkedIn workforce insights: https://economicgraph.linkedin.com/insights
- Similarweb: https://www.similarweb.com/?locale=en
- Similarweb data methodology: https://support.similarweb.com/hc/en-us/articles/360001631538-SimilarWeb-Data-Methodology
- Similarweb API: https://docs.similarweb.com/api-v5/similarweb-api/search-analysis-api/landing-pages-1
- BuiltWith: https://builtwith.com/
- Sensor Tower app intelligence: https://go.sensortower.com/game-intelligence.html
- Sensor Tower web insights release: https://www.prnewswire.com/news-releases/sensor-tower-releases-enhanced-web-insights-to-track-web-app-and-ai-driven-consumer-behavior-302770292.html
- data.ai: https://www.data.ai/
- App Store Connect analytics: https://developer.apple.com/help/app-store-connect-analytics/
- App Store analytics overview: https://developer.apple.com/app-store/measuring-app-performance/
- App Store top charts: https://apps.apple.com/us/genre/mobile-software-applications/id6011
