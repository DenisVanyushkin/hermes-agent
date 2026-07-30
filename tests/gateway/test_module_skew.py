"""Детект устаревших модулей: файлы, а не ревизия git (спека 2026-07-30)."""

import sys
import types


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _fake_module(name, path):
    """Модуль с __file__, как его видит sys.modules."""
    mod = types.ModuleType(name)
    mod.__file__ = str(path)
    return mod


def test_untouched_tree_has_no_skew(tmp_path, monkeypatch):
    from gateway import module_skew

    module_skew.reset_snapshot()
    src = _write(tmp_path / "pkg" / "mod_a.py", "x = 1\n")
    monkeypatch.setitem(sys.modules, "probe_mod_a", _fake_module("probe_mod_a", src))

    assert module_skew.take_snapshot(tmp_path) >= 1
    assert module_skew.detect_module_skew(tmp_path) == []


def test_changed_module_file_is_reported(tmp_path, monkeypatch):
    from gateway import module_skew

    module_skew.reset_snapshot()
    src = _write(tmp_path / "pkg" / "mod_b.py", "x = 1\n")
    monkeypatch.setitem(sys.modules, "probe_mod_b", _fake_module("probe_mod_b", src))
    module_skew.take_snapshot(tmp_path)

    src.write_text("x = 2\n", encoding="utf-8")

    assert module_skew.detect_module_skew(tmp_path) == ["pkg/mod_b.py"]


def test_touched_but_identical_file_is_not_skew(tmp_path, monkeypatch):
    """git pull переписывает файлы и двигает mtime, не меняя содержимого."""
    import os

    from gateway import module_skew

    module_skew.reset_snapshot()
    src = _write(tmp_path / "pkg" / "mod_c.py", "x = 1\n")
    monkeypatch.setitem(sys.modules, "probe_mod_c", _fake_module("probe_mod_c", src))
    module_skew.take_snapshot(tmp_path)

    st = src.stat()
    os.utime(src, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))

    assert module_skew.detect_module_skew(tmp_path) == []


def test_module_loaded_after_snapshot_is_not_skew(tmp_path, monkeypatch):
    from gateway import module_skew

    module_skew.reset_snapshot()
    first = _write(tmp_path / "pkg" / "mod_d.py", "x = 1\n")
    monkeypatch.setitem(sys.modules, "probe_mod_d", _fake_module("probe_mod_d", first))
    module_skew.take_snapshot(tmp_path)

    late = _write(tmp_path / "pkg" / "mod_late.py", "y = 1\n")
    monkeypatch.setitem(
        sys.modules, "probe_mod_late", _fake_module("probe_mod_late", late)
    )

    assert module_skew.detect_module_skew(tmp_path) == []


def test_watched_config_file_is_reported(tmp_path):
    """Подкласс Warning 2: старый валидатор в памяти против свежего YAML."""
    from gateway import module_skew

    module_skew.reset_snapshot()
    policy = _write(tmp_path / "config" / "policy.yaml", "role_policies: {}\n")
    module_skew.take_snapshot(tmp_path, watch_files=["config/policy.yaml"])

    policy.write_text("role_policies: {a: 1}\n", encoding="utf-8")

    assert module_skew.detect_module_skew(tmp_path) == ["config/policy.yaml"]


def test_missing_watch_file_is_ignored(tmp_path):
    from gateway import module_skew

    module_skew.reset_snapshot()
    module_skew.take_snapshot(tmp_path, watch_files=["config/does-not-exist.yaml"])

    assert module_skew.detect_module_skew(tmp_path) == []


def test_detect_without_snapshot_is_a_noop(tmp_path):
    from gateway import module_skew

    module_skew.reset_snapshot()

    assert module_skew.detect_module_skew(tmp_path) == []


def test_deleted_file_does_not_raise(tmp_path, monkeypatch):
    from gateway import module_skew

    module_skew.reset_snapshot()
    src = _write(tmp_path / "pkg" / "mod_e.py", "x = 1\n")
    monkeypatch.setitem(sys.modules, "probe_mod_e", _fake_module("probe_mod_e", src))
    module_skew.take_snapshot(tmp_path)

    src.unlink()

    assert module_skew.detect_module_skew(tmp_path) == ["pkg/mod_e.py"]


def test_venv_files_are_never_fingerprinted(tmp_path, monkeypatch):
    """I8: на проде venv лежит ВНУТРИ корня; pip install -e . — штатный шаг."""
    from gateway import module_skew

    module_skew.reset_snapshot()
    own = _write(tmp_path / "gateway" / "mod_own.py", "x = 1\n")
    dep = _write(
        tmp_path / "venv" / "lib" / "python3.11" / "site-packages" / "dep" / "core.py",
        "y = 1\n",
    )
    monkeypatch.setitem(sys.modules, "probe_mod_own", _fake_module("probe_mod_own", own))
    monkeypatch.setitem(sys.modules, "probe_dep_core", _fake_module("probe_dep_core", dep))

    module_skew.take_snapshot(tmp_path)

    assert dep.resolve() not in module_skew._snapshot
    assert own.resolve() in module_skew._snapshot

    dep.write_text("y = 2\n", encoding="utf-8")
    assert module_skew.detect_module_skew(tmp_path) == []


def test_dot_venv_files_are_never_fingerprinted(tmp_path, monkeypatch):
    from gateway import module_skew

    module_skew.reset_snapshot()
    dep = _write(tmp_path / ".venv" / "lib" / "site-packages" / "dep2.py", "y = 1\n")
    monkeypatch.setitem(sys.modules, "probe_dep2", _fake_module("probe_dep2", dep))

    module_skew.take_snapshot(tmp_path)

    assert dep.resolve() not in module_skew._snapshot


def test_module_imported_after_snapshot_is_folded_in(tmp_path, monkeypatch):
    """M2: ленивый импорт (как run_agent) обязан попасть в снимок при виде."""
    from gateway import module_skew

    module_skew.reset_snapshot()
    first = _write(tmp_path / "pkg" / "mod_f.py", "x = 1\n")
    monkeypatch.setitem(sys.modules, "probe_mod_f", _fake_module("probe_mod_f", first))
    module_skew.take_snapshot(tmp_path)

    late = _write(tmp_path / "pkg" / "mod_lazy.py", "y = 1\n")
    monkeypatch.setitem(
        sys.modules, "probe_mod_lazy", _fake_module("probe_mod_lazy", late)
    )

    assert module_skew.detect_module_skew(tmp_path) == []  # только что прочитан
    assert late.resolve() in module_skew._snapshot

    late.write_text("y = 2\n", encoding="utf-8")
    assert module_skew.detect_module_skew(tmp_path) == ["pkg/mod_lazy.py"]


def test_unresolvable_module_path_does_not_kill_the_snapshot(tmp_path, monkeypatch):
    """M4: RuntimeError/ValueError из resolve() не имеет права снести снимок."""
    from gateway import module_skew

    module_skew.reset_snapshot()
    good = _write(tmp_path / "pkg" / "mod_g.py", "x = 1\n")
    monkeypatch.setitem(sys.modules, "probe_mod_g", _fake_module("probe_mod_g", good))
    monkeypatch.setitem(
        sys.modules, "probe_mod_nul", _fake_module("probe_mod_nul", "/tmp/bad\x00name.py")
    )

    assert module_skew.take_snapshot(tmp_path) >= 1
    assert good.resolve() in module_skew._snapshot
