"""Regression tests: injected cron data must not drive role selection.

Incident 2026-07-06: the morning-diagnostics-report cron job was routed to
``artist`` because the pre-run script's diagnostics digest (injected into the
prompt as a ``## Script Output`` fenced block) contained ``image_gen ...`` log
signatures, and the keyword cascade in ``_select_role`` scanned the whole
assembled prompt. These tests pin the fix: routing/classification text
extraction strips injected data sections, and an explicit ``[ROLE PIN: ...]``
directive deterministically wins over the keyword cascade.
"""

from hermes_cli.profile_execution import _select_role
from hermes_cli.profile_request_context import (
    classification_request_text,
    extract_role_pin,
    routing_request_text,
)


CRON_BANNER = (
    "[IMPORTANT: You are running as a scheduled cron job. "
    "DELIVERY: Your final response will be automatically delivered "
    "to the user — do NOT use send_message or try to deliver "
    "the output yourself. Just produce your report/output as your "
    "final response and the system handles the rest. "
    "SILENT: If there is genuinely nothing new to report, respond "
    'with exactly "[SILENT]" (nothing else) to suppress delivery. '
    "Never combine [SILENT] with content — either report your "
    "findings normally, or say [SILENT] and nothing more.]\n\n"
)

POISONED_DIGEST = (
    "## Script Output\n"
    "The following data was collected by a pre-run script. "
    "Use it as context for your analysis.\n\n"
    "```\n"
    "2026-07-06 05:01:12 ERROR image_gen: сгенерируй изображение failed\n"
    "2026-07-06 05:02:44 INFO git push origin main rejected (git_remote_mutation)\n"
    "2026-07-06 05:03:01 WARNING deploy code fix commit merge\n"
    "```\n\n"
)

USER_INSTRUCTION = "Составь утренний отчёт о ночных событиях и отправь его."


def _assembled_prompt(extra_sections: str = POISONED_DIGEST) -> str:
    return CRON_BANNER + "## Scheduled Task Metadata\nTitle: morning-diagnostics-report\n\n" + extra_sections + USER_INSTRUCTION


class TestInjectedDataStripping:
    def test_routing_text_drops_script_output_section(self):
        cleaned = routing_request_text(_assembled_prompt())
        assert "image_gen" not in cleaned
        assert "git push" not in cleaned
        assert USER_INSTRUCTION in cleaned

    def test_classification_text_drops_script_output_section(self):
        cleaned = classification_request_text(_assembled_prompt())
        assert "image_gen" not in cleaned
        assert "git push" not in cleaned
        assert USER_INSTRUCTION in cleaned

    def test_script_error_section_is_stripped(self):
        section = (
            "## Script Error\n"
            "The data-collection script failed. Report this to the user.\n\n"
            "```\nTraceback: сгенерируй изображение image generate\n```\n\n"
        )
        cleaned = routing_request_text(_assembled_prompt(section))
        assert "Traceback" not in cleaned
        assert USER_INSTRUCTION in cleaned

    def test_context_from_section_is_stripped(self):
        section = (
            "## Output from job 'abc123def456'\n"
            "The following is the most recent output from a preceding "
            "cron job. Use it as context for your analysis.\n\n"
            "```\nsecurity audit vulnerability pentest image_gen\n```\n\n"
        )
        cleaned = routing_request_text(_assembled_prompt(section))
        assert "pentest" not in cleaned
        assert USER_INSTRUCTION in cleaned

    def test_select_role_ignores_poisoned_digest(self):
        role, _, _ = _select_role(_assembled_prompt(), None)
        assert role != "artist"

    def test_plain_prompt_without_sections_unchanged(self):
        task = "сгенерируй изображение кота"
        assert routing_request_text(task) == task
        role, _, _ = _select_role(task, None)
        assert role == "artist"


class TestRolePin:
    def test_extract_role_pin_returns_role(self):
        assert extract_role_pin("[ROLE PIN: scribe]\nDo the report.") == "scribe"

    def test_extract_role_pin_case_insensitive_and_mid_text(self):
        task = CRON_BANNER + "[role pin: engineer]\n\n" + USER_INSTRUCTION
        assert extract_role_pin(task) == "engineer"

    def test_extract_role_pin_absent(self):
        assert extract_role_pin(USER_INSTRUCTION) is None

    def test_extract_role_pin_rejects_malformed(self):
        assert extract_role_pin("[ROLE PIN: rm -rf /]") is None
        assert extract_role_pin("[ROLE PIN: ]") is None

    def test_pin_line_stripped_from_request_text(self):
        task = "[ROLE PIN: scribe]\n" + USER_INSTRUCTION
        assert "role pin" not in routing_request_text(task).lower()
        assert "role pin" not in classification_request_text(task).lower()
        assert USER_INSTRUCTION in routing_request_text(task)

    def test_select_role_honors_pin_over_keyword_cascade(self):
        task = "[ROLE PIN: scribe]\nсгенерируй изображение кота"
        role, fallback, reason = _select_role(task, None)
        assert role == "scribe"
        assert fallback is False
        assert "pin" in reason.lower()

    def test_select_role_pin_wins_over_poisoned_digest(self):
        task = CRON_BANNER + "[ROLE PIN: scribe]\n\n" + POISONED_DIGEST + USER_INSTRUCTION
        role, _, _ = _select_role(task, None)
        assert role == "scribe"


class TestRoleContextPinBypassesLlmRouter:
    def test_pin_skips_llm_router(self, monkeypatch):
        import hermes_cli.profile_context as pc

        def _boom(*args, **kwargs):
            raise AssertionError("LLM router must not be called for pinned tasks")

        monkeypatch.setattr(pc, "select_role_via_llm", _boom)
        result = pc.build_role_context_for_task("[ROLE PIN: scribe]\nЗапиши итоги дня.")
        assert result.selected_role == "scribe"
