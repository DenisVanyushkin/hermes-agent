import job_intel.dedup as dedup
from job_intel.dedup import canonical_vacancy_key, is_duplicate
from job_intel.models import Vacancy


def test_canonical_key_normalizes_company_title_and_location() -> None:
    vacancy = Vacancy(
        source="linkedin",
        source_id="abc",
        company="Revolut",
        title="VP Product",
        location="London, UK",
        url="https://example.com/1",
        description="Lead monetization and product strategy.",
    )

    assert canonical_vacancy_key(vacancy) == canonical_vacancy_key(
        vacancy.model_copy(update={"title": "Vice President Product"})
    )


def test_duplicate_detection_uses_similarity_and_repost_window() -> None:
    canonical = Vacancy(
        source="greenhouse",
        source_id="1",
        company="Wise",
        title="Head of Product Monetization",
        location="Remote, Europe",
        url="https://boards.example/jobs/1",
        description="Own product strategy, monetization, and growth for a B2C platform.",
        posted_at="2026-05-01T00:00:00Z",
    )
    repost = canonical.model_copy(update={"source_id": "2", "url": "https://boards.example/jobs/2"})

    assert is_duplicate(repost, canonical, similarity_threshold=0.7, repost_window_days=45)


def _pair(first_description: str, second_description: str) -> tuple[Vacancy, Vacancy]:
    common = dict(
        source="linkedin",
        company="Different Company",
        title="Different Title",
        location="Remote",
        posted_at=None,
    )
    return (
        Vacancy(
            **common,
            source_id="candidate",
            url="https://www.linkedin.com/jobs/view/100",
            description=first_description,
        ),
        Vacancy(
            **common,
            source_id="existing",
            url="https://www.linkedin.com/jobs/view/200",
            description=second_description,
        ),
    )


def test_duplicate_short_circuit_uses_real_quick_ratio_before_ratio(monkeypatch) -> None:
    candidate, existing = _pair("x" * 10, "y" * 1_000)
    calls: list[str] = []
    original = dedup.SequenceMatcher

    class Spy:
        def __init__(self, *args, **kwargs):
            self.inner = original(*args, **kwargs)

        def real_quick_ratio(self):
            calls.append("real_quick_ratio")
            return self.inner.real_quick_ratio()

        def quick_ratio(self):
            calls.append("quick_ratio")
            return self.inner.quick_ratio()

        def ratio(self):
            calls.append("ratio")
            return self.inner.ratio()

    monkeypatch.setattr(dedup, "SequenceMatcher", Spy)

    assert not is_duplicate(candidate, existing, similarity_threshold=0.82)
    assert calls == ["real_quick_ratio"]


def test_duplicate_short_circuit_uses_quick_ratio_before_ratio(monkeypatch) -> None:
    candidate, existing = _pair("a b c " * 100, "x y z " * 100)
    calls: list[str] = []
    original = dedup.SequenceMatcher

    class Spy:
        def __init__(self, *args, **kwargs):
            self.inner = original(*args, **kwargs)

        def real_quick_ratio(self):
            calls.append("real_quick_ratio")
            return self.inner.real_quick_ratio()

        def quick_ratio(self):
            calls.append("quick_ratio")
            return self.inner.quick_ratio()

        def ratio(self):
            calls.append("ratio")
            return self.inner.ratio()

    monkeypatch.setattr(dedup, "SequenceMatcher", Spy)

    assert not is_duplicate(candidate, existing, similarity_threshold=0.82)
    assert calls == ["real_quick_ratio", "quick_ratio"]
