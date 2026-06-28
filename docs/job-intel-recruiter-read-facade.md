# Job-Intel Recruiter Read Facade

Module path: `job_intel/recruiter_read_facade.py`

Supported read methods:
- `get_vacancy_by_id(...)`
- `get_vacancy_by_url(...)`
- `get_opportunity_by_id(...)`
- `get_opportunity_for_vacancy(...)`
- `get_company_context(...)`
- `get_application_history(...)`
- `get_recent_relevant_vacancies(...)`

Non-goals:
- no CRM writes;
- no reconcile/apply paths;
- no outbound Slack/Gmail/Telegram/LinkedIn sends;
- no browser/source acquisition;
- no scheduled job execution;
- no provider/model calls.

Read-only proof:
- all facade queries use `JobIntelStore.connect(read_only=True)`;
- the facade does not import `crm_service`, `crm_reconciler`, or `OpportunityRepository`;
- focused tests assert direct writes through the facade read-only connection fail.

Future Recruiter skills should consume only the facade JSON payloads and treat warnings and provenance as part of the contract.

Approval-gated write paths remain outside this module: CRM status changes, artifact creation, reconcile/apply flows, and any outbound delivery.

Known follow-up gaps:
- existing `job_intel` read helpers in other modules are not uniformly read-only yet;
- CRM tables are treated as optional, so future slices should decide whether their schema becomes guaranteed host baseline.
