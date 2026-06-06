from gateway.telegram_reactions import strip_telegram_reaction_only_response


def test_strip_telegram_reaction_only_response():
    assert strip_telegram_reaction_only_response("👍") == ""
    assert strip_telegram_reaction_only_response("  **👍**  ") == ""
    assert strip_telegram_reaction_only_response("Thanks 👍") == "Thanks 👍"
    assert strip_telegram_reaction_only_response("done") == "done"
