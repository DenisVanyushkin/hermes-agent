---
name: two-gis-site-scraping
description: "Use when you need 2GIS public business data from 2gis.kz before you have an official API key; search city pages, extract firm pages, and return contacts/hours/coordinates with clear scraping limitations."
version: 1.0.0
author: Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [2gis, scraping, local-business, kazakhstan, contacts, hours, coordinates]
    related_skills: [maps, google-workspace]
---

# 2GIS Site Scraping

## Overview

Practical fallback workflow for pulling public business data from `2gis.kz` when
an official 2GIS API key is not available yet. This skill does **not** depend
on undocumented authenticated endpoints. It works by fetching normal public HTML
pages that any browser can open, then extracting data already embedded in those
pages.

This makes it useful for:
- Kazakhstan local-business discovery
- grabbing public contact info from a firm card
- collecting hours, coordinates, and websites fast
- evaluating whether 2GIS data coverage is good enough before paying for API

It is **not** a stable contract. Public markup and embedded JSON can change at
any time. Treat it as a fallback workflow, not a replacement for the official
API.

## When to Use

- User wants public 2GIS data now, but no paid/demo API key is available yet.
- Need to find businesses in Kazakhstan and return name, address, hours,
  website, WhatsApp, phones, and coordinates.
- Need a quick practical workflow from Hermes terminal tools, not browser-only
  manual clicking.
- Need an extraction path that survives the current container because the files
  live in the Hermes git repo and can be committed on the local customizations
  branch.

## Do Not Use This For

- High-volume production ingestion
- compliance-sensitive or contract-sensitive datasets
- guaranteed complete phone numbers or guaranteed unchanged schemas
- aggressive crawling across many cities/pages

## Script

Primary script:

```bash
python ~/.hermes/hermes-agent/skills/productivity/two-gis-site-scraping/scripts/extract_2gis_public.py
```

The script is stdlib-only and exposes three commands:
- `search` — parse public search results into candidate firm URLs
- `firm` — extract one firm page into structured JSON
- `lookup` — search first, then enrich top firm pages

## Practical Workflow

### 1) Search for candidate firms

```bash
TGIS=~/.hermes/hermes-agent/skills/productivity/two-gis-site-scraping/scripts/extract_2gis_public.py
python "$TGIS" search "coffee" --city almaty --limit 5
python "$TGIS" search "стоматология" --city almaty --limit 8
python "$TGIS" search "визовый центр" --city almaty --limit 5
```

Returns JSON like:
- `firm_id`
- `name`
- `url`

### 2) Extract one firm page

```bash
python "$TGIS" firm https://2gis.kz/almaty/firm/70000001106087900
python "$TGIS" firm 70000001106087900 --city almaty
```

Returns structured JSON fields such as:
- `name`
- `address`
- `coordinates.lat` / `coordinates.lon`
- `rubrics`
- `schedule`
- `contacts.phones`
- `contacts.whatsapp`
- `contacts.websites`
- `contacts.instagram`
- `updated_at`
- `distances` (when `--origin label:lat,lon` is provided)

Distance example:

```bash
python "$TGIS" firm 70000001106087900 --city almaty \
  --origin home:43.238,76.945 \
  --origin office:43.256,76.928
```

This adds straight-line distance blocks for each supplied origin.

### 3) One-shot lookup workflow

```bash
python "$TGIS" lookup "coffee" --city almaty --search-limit 10 --firm-limit 3
python "$TGIS" lookup "барбершоп" --city almaty --search-limit 12 --firm-limit 5
python "$TGIS" lookup "визовый центр" --city almaty --search-limit 8 --firm-limit 3
python "$TGIS" lookup "автозвук" --city almaty --firm-limit 3 --origin home:43.238,76.945
```

This is the default practical path when the user asks:
- "найди компании в 2GIS"
- "собери контакты заведений"
- "верни телефоны / WhatsApp / часы работы"

## Recommended Agent Workflow

1. Determine the city alias (`almaty`, `astana`, etc.).
2. Run `lookup` first for broad discovery.
3. If results are noisy, run `search` with a narrower query.
4. Re-run `firm` on the most promising URLs for cleaner per-business output.
5. Present a concise list to the user rather than dumping raw JSON unless they
   explicitly ask for machine-readable output.
6. If the user needs durable or large-scale integration, switch to the official
   2GIS API as soon as pricing/access is available.

## Output Interpretation Notes

### Contacts

2GIS public pages may contain several contact channels:
- phone
- WhatsApp (`wa.me`)
- websites
- Instagram
- Telegram
- email

The script merges both:
- embedded public JSON contact groups
- visible HTML links like `tel:` and `wa.me`

### Phones can be masked

A known 2GIS quirk:
- `tel:` hrefs may contain masked numbers like `+777****4752`
- embedded contact JSON may still expose a fuller `print_text`

Because of that, always prefer the script's normalized `contacts.phones` output
instead of trusting only the raw `tel:` href.

### Schedule format

The script normalizes the public `schedule` object into:
- `is_24x7`
- `description`
- `days.Mon` … `days.Sun`

Example:

```json
{
  "is_24x7": false,
  "description": null,
  "days": {
    "Mon": [{"from": "09:00", "to": "18:00"}],
    "Tue": [{"from": "09:00", "to": "18:00"}]
  }
}
```

## Safe Usage Limits

Use this like a careful fallback scraper, not a crawler.

Recommended limits:
- keep `firm-limit` small (3-10)
- keep `search-limit` reasonable (5-20)
- do not parallelize aggressively
- keep short delays between repeated firm fetches
- prefer targeted business searches over broad category sweeps

## Common Pitfalls

1. **Treating this as a stable API.**
   It is not. HTML and embedded data shape can change without notice.

2. **Assuming all phone numbers are complete.**
   Public pages can partially mask phones.

3. **Using raw search pages as final truth.**
   Search results are good for discovery; final extraction should come from the
   firm page.

4. **Trying to do bulk ingestion.**
   This skill is for narrow, practical lookups.

5. **Forgetting city aliases in URLs.**
   The script needs aliases like `almaty` rather than arbitrary free-form city
   names in the URL path.

6. **Assuming every firm has every channel.**
   Some pages have no website, no WhatsApp, or no visible schedule.

## Verification Checklist

- [ ] `python "$TGIS" search "coffee" --city almaty --limit 3` returns firm URLs
- [ ] `python "$TGIS" firm <url>` returns address + coordinates + contacts JSON
- [ ] `python "$TGIS" lookup "coffee" --city almaty --firm-limit 2` returns enriched firms
- [ ] Output is clearly labeled as public-page scraping fallback
- [ ] User-facing summary distinguishes reliable fields from missing/masked ones

## Escalation Path

If the user later receives acceptable pricing/access from 2GIS:
1. keep this skill as fallback only
2. build an API-first workflow using official catalog endpoints
3. prefer API responses for structured, scalable, durable integrations
