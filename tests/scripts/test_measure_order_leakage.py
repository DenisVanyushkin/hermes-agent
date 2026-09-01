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


def test_classify_nodes_keeps_four_traits_independent():
    nodes = [
        "tests/a.py::test_order_and_standalone",
        "tests/b.py::test_host_sensitive",
    ]

    classified = measure.classify_nodes(
        nodes,
        red_standalone={nodes[0]},
        intra_file_order={nodes[0]},
        host_sensitive={nodes[1]},
    )

    assert classified == [
        {
            "nodeid": nodes[0],
            "traits": {
                "red_standalone": "yes",
                "intra_file_order": "yes",
                "needs_neighbour": "not_checked",
                "cross_process_or_host_state_sensitive": "not_checked",
            },
        },
        {
            "nodeid": nodes[1],
            "traits": {
                "red_standalone": "not_checked",
                "intra_file_order": "no",
                "needs_neighbour": "not_checked",
                "cross_process_or_host_state_sensitive": "yes",
            },
        },
    ]


def test_parse_node_statuses_preserves_parameterized_nodeids_with_spaces():
    log = "PASSED tests/a.py::test_value[hello world] - detail\n"

    assert measure.parse_node_statuses(log) == {
        "tests/a.py::test_value[hello world]": "PASSED"
    }


def test_parse_node_statuses_preserves_two_parameterized_nodeids_with_dashes():
    log = (
        "FAILED tests/a.py::test_value[alpha - old] - setup boom\n"
        "FAILED tests/a.py::test_value[alpha - new] - setup boom\n"
    )

    assert measure.parse_node_statuses(log) == {
        "tests/a.py::test_value[alpha - old]": "FAILED",
        "tests/a.py::test_value[alpha - new]": "FAILED",
    }


def test_parse_node_statuses_keeps_tests_path_policy_at_call_site():
    log = (
        "FAILED src/a.py::test_outside - setup boom\n"
        "FAILED tests/a.py::test_inside - setup boom\n"
    )

    assert measure.parse_node_statuses(log) == {
        "tests/a.py::test_inside": "FAILED"
    }


def test_standalone_results_mark_each_node_yes_or_no():
    results = {
        "tests/a.py::test_red": {"returncode": 1},
        "tests/b.py::test_green": {"returncode": 0},
    }

    assert measure.classify_standalone_results(results) == {
        "tests/a.py::test_red": "yes",
        "tests/b.py::test_green": "no",
    }
