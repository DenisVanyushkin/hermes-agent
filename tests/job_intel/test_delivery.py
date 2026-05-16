from job_intel.cli import _deliver_to_slack


def test_deliver_to_slack_uses_webhook(monkeypatch) -> None:
    calls = {}

    class Response:
        def raise_for_status(self):
            return None

    def fake_post(url, json, timeout):
        calls["url"] = url
        calls["json"] = json
        calls["timeout"] = timeout
        return Response()

    monkeypatch.setenv("JOB_INTEL_SLACK_WEBHOOK_URL", "https://hooks.slack.test/example")
    monkeypatch.setattr("job_intel.cli.requests.post", fake_post)

    assert _deliver_to_slack("hello", "C123") is True
    assert calls["url"] == "https://hooks.slack.test/example"
    assert calls["json"]["text"] == "hello"
    assert calls["json"]["channel"] == "C123"
