"""
A registered tool is only reachable if its toolset survives the platform gate.

The gateway computes ``enabled_toolsets = sorted(_get_platform_tools(...))``
(gateway/run.py) and the agent's schema is built from those toolset keys. The
platform reverse-mapping resolves toolsets statically (``include_registry=False``,
see toolsets.resolve_toolset), so a toolset that exists only because a module
called ``registry.register(toolset=...)`` never appears — the tool is silently
dropped even though its name is listed in the platform's composite toolset.
``tts``/``text_to_speech`` avoids this by also having a static TOOLSETS entry.
"""

import pytest

from hermes_cli.tools_config import _get_platform_tools
from toolsets import resolve_toolset


def _tools_offered_to(platform: str) -> set:
    names = set()
    for toolset in _get_platform_tools({}, platform):
        names.update(resolve_toolset(toolset))
    return names


@pytest.mark.parametrize("platform", ["whatsapp", "telegram"])
def test_text_to_speech_is_offered(platform):
    """Control: proves this harness measures what the agent actually gets."""
    assert "text_to_speech" in _tools_offered_to(platform)


@pytest.mark.parametrize("platform", ["whatsapp", "telegram"])
def test_transcribe_audio_is_offered(platform):
    """Without this, the agent's only route to a transcript is the terminal."""
    assert "transcribe_audio" in _tools_offered_to(platform)
