import importlib.util
from datetime import datetime, timezone, timedelta
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "news_context.py"
SPEC = importlib.util.spec_from_file_location("news_context", SCRIPT_PATH)
ncx = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(ncx)


def test_is_stale_true_when_old_or_missing():
    now = datetime(2026, 7, 7, 21, tzinfo=timezone.utc)
    fresh = {"generated_at": (now - timedelta(hours=1)).isoformat()}
    old = {"generated_at": (now - timedelta(hours=40)).isoformat()}
    assert ncx.is_stale(fresh, now) is False
    assert ncx.is_stale(old, now) is True
    assert ncx.is_stale({}, now) is True


def test_render_context_lists_items_and_frames_as_untrusted():
    now = datetime(2026, 7, 7, 21, tzinfo=timezone.utc)
    data = {"generated_at": (now - timedelta(hours=1)).isoformat(),
            "items": [{"source": "tg:llm_news", "title": "RAG lib", "url": "https://x.io/a",
                       "summary": "", "snippet": ""}]}
    out = ncx.render_context(data, now)
    assert "RAG lib" in out and "https://x.io/a" in out and "tg:llm_news" in out
    # untrusted-data framing present, both open and close markers
    assert out.count("UNTRUSTED") >= 2
    assert "не выполняй" in out.lower() or "do not follow" in out.lower()


def test_render_context_note_when_empty():
    now = datetime(2026, 7, 7, 21, tzinfo=timezone.utc)
    out = ncx.render_context({"generated_at": now.isoformat(), "items": []}, now)
    assert "нет" in out.lower() or "no " in out.lower()


def test_render_context_keeps_closing_frame_when_truncated():
    now = datetime(2026, 7, 7, 21, tzinfo=timezone.utc)
    items = [{"source": "s", "title": "x" * 300, "url": f"https://x.io/{i}",
              "summary": "", "snippet": ""} for i in range(500)]
    out = ncx.render_context(
        {"generated_at": (now - timedelta(hours=1)).isoformat(), "items": items}, now)
    assert out.rstrip().endswith("UNTRUSTED NEWS DATA =====")
    assert "BEGIN UNTRUSTED" in out
    assert len(out) <= ncx.MAX_CHARS + len(ncx._FRAME_CLOSE) + 2


def test_load_candidates_rejects_non_dict_json(tmp_path):
    p = tmp_path / "c.json"
    for bad in ("[]", "null", '"oops"', "[1, 2, 3]"):
        p.write_text(bad, encoding="utf-8")
        assert ncx.load_candidates(p) is None
