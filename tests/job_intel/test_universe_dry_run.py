from types import SimpleNamespace

from job_intel.universe.dry_run import dry_run_candidate
from job_intel.universe.models import CandidateCompany


def _fake_fetcher(vacancies):
    def f(queries, *, companies, max_jobs_per_company):
        assert companies == ["nium"] and max_jobs_per_company == 25
        return SimpleNamespace(vacancies=vacancies, errors=[],
                               discovered_companies=1, pages_fetched=1)
    return f


def test_dry_run_counts_and_samples():
    c = CandidateCompany(name="Nium", ats_type="greenhouse")
    vacs = [SimpleNamespace(title=f"Role {i}") for i in range(5)]
    dry_run_candidate(c, fetchers={"greenhouse": _fake_fetcher(vacs)})
    assert c.dry_run_vacancies == 5
    assert c.dry_run_sample_titles == ["Role 0", "Role 1", "Role 2"]


def test_dry_run_skips_without_ats():
    c = CandidateCompany(name="Ghost")
    dry_run_candidate(c, fetchers={})
    assert c.dry_run_vacancies == -1


def test_dry_run_fetcher_error_is_zero():
    def boom(queries, *, companies, max_jobs_per_company):
        raise RuntimeError("http 500")
    c = CandidateCompany(name="Nium", ats_type="greenhouse")
    dry_run_candidate(c, fetchers={"greenhouse": boom})
    assert c.dry_run_vacancies == 0
