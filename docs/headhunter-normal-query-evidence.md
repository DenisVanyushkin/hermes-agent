# HeadHunter normal-query evidence (offline T6)

Date: 2026-09-30  
Source base: `e587f9e5766beb99cb1191841abc94f6624363d0` (`hermes-agent`)

## Request contract

The daily HH path sends role names in `text` and geographic constraints as
separate `area` IDs. The existing four geography families are retained. The
remote variant uses `work_format=REMOTE`; the legacy `schedule` parameter is
deprecated and the current API documentation says it was replaced by
`work_schedule_by_days`, `work_format`, and `employment_form`.

Official references:

- [Vacancy search](https://api.hh.ru/openapi/redoc#tag/Poisk-vakansij/operation/get-vacancies)
- [Region directory](https://api.hh.ru/openapi/redoc#tag/Obshie-spravochniki/operation/get-areas)
- [Field dictionaries](https://api.hh.ru/openapi/redoc#tag/Obshie-spravochniki/operation/get-dictionaries)

The search documentation defines `text` as the vacancy text query, `area` as
one or more IDs from `/areas`, and `work_format` as one or more IDs from the
`work_format` dictionary. `REMOTE` is the remote work-format ID in the current
dictionary.

The HH-specific role groups retain the eight existing family IDs but use
literal role titles in English and Russian. Geography stays out of `text`, and
generic discovery placeholders such as “COO-adjacent digital role” are excluded
from the HH vocabulary.

## Geography snapshot

A read-only `GET https://api.hh.ru/areas` returned HTTP 200 on 2026-09-30. The
response was 2,430,723 bytes with SHA-256
`25895305b48cf9ca02e86aaa0e758b75b099e607273e6347eb7c6f6126d5df65`. These are
the exact area IDs found for named places in the existing groups:

| Existing group | HH area IDs used | Labels kept as unknown |
|---|---|---|
| `remote_europe` | United Kingdom `21`, Germany `27` | Europe-wide |
| `eu_gcc` | Netherlands `65`, Poland `74`, UAE `208` | Saudi Arabia, GCC-wide |
| `apac_core` | Indonesia `2108`, Malaysia `238`, Singapore `233`, Thailand `300` | APAC-wide |
| `apac_plus` | Australia `6`, Kazakhstan `40` | — |

The root `Другие регионы` (`1001`) contains many countries and is not used as a
shortcut for the broad labels; doing so would collapse the existing region
families into one much wider area. The HH documentation warns that directory
values can change, so this mapping must be refreshed against `/areas` before a
future area-ID change is accepted.

## Response fixture

`tests/fixtures/job_intel/hh_api_search_item_query_url.json` uses the same
search-item shape as the existing saved HH fixture, including separate API
`url` and user-facing `alternate_url`. Its query-string suffix is a synthetic
representative value, added to prove the mapper preserves the URL exactly; it
is not presented as a newly captured vacancy response.

No vacancy-search request, production database access, message delivery, or
deployment was performed for this offline slice. The live old-versus-new
comparison and rollout remain separately sanctioned by T6.

## Offline verification

The focused HH/API/query and LinkedIn-regression command passed 93 tests across
nine files. Ruff and `git diff --check` passed. A broader
`scripts/run_tests.sh -j 4 tests/job_intel` run reported failures in 18 unrelated
test modules. Representative causes were confirmed in the unchanged base:
`job_intel/cli.py` does not contain the CRM command expected by the failing CRM
tests, the expected metrics-exporter systemd unit is absent, the profile-lock
script uses Bash 4 descriptor syntax while this host provides Bash 3.2, and
several tests require `/usr/bin/python3.12`, which is absent. No CRM, deployment,
profile-lock, browser, or Python-version files are part of this T6 patch.

## Independent review

The first peer review found that adding empty filter fields to every
`query_id` changed LinkedIn and legacy HH query identities as well as new HH
identities. The implementation now appends filter components only when a plan
item actually has area/work-format/unsupported-geography fields. Regression
tests pin the prior LinkedIn and legacy HH IDs and verify that a remote-format
filter does distinguish a structured HH query. The 93-test focused run above
was repeated after this fix; the broad suite was not rerun after this narrow
identity-serialization change.
