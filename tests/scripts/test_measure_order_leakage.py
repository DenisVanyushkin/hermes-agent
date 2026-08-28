import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import measure_order_leakage as measure  # noqa: E402


def test_parse_measurement_report_splits_survivors_vanished_and_isolation_only():
    full_log = (
        "FAILED tests/a.py::test_survives - AssertionError\n"
        "FAILED tests/a.py::test_vanishes - AssertionError\n"
    )
    isolated_logs = {
        "tests/a.py": "FAILED tests/a.py::test_survives - AssertionError\n",
        "tests/b.py": "FAILED tests/b.py::test_isolation_only - AssertionError\n",
    }

    report = measure.parse_measurement_report(full_log, isolated_logs)

    assert report["full"] == 2
    assert report["isolated"] == 2
    assert report["survive"] == 1
    assert report["vanish"] == 1
    assert report["isolation_only"] == 1
    assert report["by_file"] == {
        "tests/a.py": {
            "full": ["tests/a.py::test_survives", "tests/a.py::test_vanishes"],
            "isolated": ["tests/a.py::test_survives"],
            "survive": ["tests/a.py::test_survives"],
            "vanish": ["tests/a.py::test_vanishes"],
            "isolation_only": [],
        },
        "tests/b.py": {
            "full": [],
            "isolated": ["tests/b.py::test_isolation_only"],
            "survive": [],
            "vanish": [],
            "isolation_only": ["tests/b.py::test_isolation_only"],
        },
    }


def test_bidirectional_section_is_derived_for_any_file():
    full_log = (
        "FAILED tests/other.py::test_full - AssertionError\n"
        "FAILED tests/plain.py::test_same - AssertionError\n"
    )
    isolated_logs = {
        "tests/other.py": "FAILED tests/other.py::test_isolated - AssertionError\n",
        "tests/plain.py": "FAILED tests/plain.py::test_same - AssertionError\n",
    }

    report = measure.parse_measurement_report(full_log, isolated_logs)

    assert set(report["bidirectional"]) == {"tests/other.py"}
    assert report["bidirectional"]["tests/other.py"] == report["by_file"]["tests/other.py"]
