import json

from hermes_cli.run_evidence import (
    observed_sandbox_commands,
    observed_write_refusals,
    render_execution_locus_block,
)


def _call(name, arguments):
    return {"role": "assistant", "tool_calls": [{"function": {"name": name, "arguments": arguments}}]}


def test_terminal_calls_are_observed_as_sandbox_commands():
    messages = [
        {"role": "user", "content": "посмотри, в чем проблема"},
        _call("terminal", json.dumps({"command": "timeout 30s python3 -m job_intel doctor"})),
        _call("execute_code", json.dumps({"code": "import job_intel\nprint(1)"})),
    ]

    assert observed_sandbox_commands(messages) == [
        "timeout 30s python3 -m job_intel doctor",
        "import job_intel",
    ]


def test_non_executing_tools_are_not_counted():
    messages = [_call("read_file", json.dumps({"path": "job_intel/cli.py"}))]

    assert observed_sandbox_commands(messages) == []


def test_malformed_arguments_never_raise():
    messages = [
        _call("terminal", "{not json"),
        _call("terminal", None),
        _call("terminal", json.dumps({})),
        {"role": "assistant", "tool_calls": [{"function": "not a dict"}]},
        {"role": "assistant", "tool_calls": "not a list"},
        "not a dict at all",
    ]

    assert observed_sandbox_commands(messages) == []


def test_none_messages_are_tolerated():
    assert observed_sandbox_commands(None) == []


def test_block_names_the_sandbox_and_warns_it_is_not_the_host():
    block = render_execution_locus_block(["python3 -m job_intel doctor"])

    assert "В песочнице" in block
    assert "python3 -m job_intel doctor" in block
    assert "не описывают состояние хоста" in block


def test_block_truncates_a_long_list_but_says_how_many_were_hidden():
    block = render_execution_locus_block([f"cmd{i}" for i in range(9)])

    assert "9" in block
    assert "и ещё 4" in block


def test_a_turn_with_no_commands_renders_nothing():
    # Приписка "ничего не выполнялось" к ходу, который и не собирался ничего
    # выполнять, -- шум, а шум пролистывают вместе с сигналом.
    assert render_execution_locus_block([]) == ""


def _tool_result(content):
    return {"role": "tool", "content": content}


def test_a_read_only_refusal_in_a_tool_result_is_counted():
    messages = [
        _tool_result('{"output": "touch: cannot touch \'x\': Read-only file system"}'),
    ]

    assert observed_write_refusals(messages) == 1


def test_several_refusals_in_one_result_are_all_counted():
    messages = [
        _tool_result("sed: couldn't open: Read-only file system\ncp: Read-only file system"),
    ]

    assert observed_write_refusals(messages) == 2


def test_the_marker_is_matched_regardless_of_case():
    assert observed_write_refusals([_tool_result("READ-ONLY FILE SYSTEM")]) == 1


def test_only_tool_results_are_scanned():
    # Собственный текст агента про read-only -- это его рассуждение, а не факт
    # отказа. Считаем только то, что вернула система.
    messages = [
        {"role": "assistant", "content": "репозиторий read-only file system, так что не пишу"},
        {"role": "user", "content": "read-only file system"},
    ]

    assert observed_write_refusals(messages) == 0


def test_malformed_messages_never_raise():
    assert observed_write_refusals([None, "x", {"role": "tool"}, {"role": "tool", "content": 5}]) == 0
    assert observed_write_refusals(None) == 0


def test_block_reports_refusals_next_to_the_commands():
    block = render_execution_locus_block(["touch /workspace/live-hermes/x"], write_refusals=3)

    assert "Отклонено попыток записи в репозиторий: 3" in block
    assert ":ro" in block


def test_no_refusals_adds_no_line():
    block = render_execution_locus_block(["ls"], write_refusals=0)

    assert "Отклонено" not in block


def test_refusals_alone_still_render_when_commands_were_seen():
    # Отказ без единой замеченной команды рендерить нечего: блок висит на
    # командах, а их не было.
    assert render_execution_locus_block([], write_refusals=5) == ""
