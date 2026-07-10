# hermes-lawyer-core

Core package for the built-in `lawyer` role: source-grounded Kazakhstan legal
analysis over adilet.zan.kz.

## Behavior contract

- Answers only from retrieved acts (legal_research toolset); never from memory.
- Mandatory answer format (Краткий вывод … Ограничения ответа), Russian output.
- Answers with conclusions MUST pass the `legal_answer_review` gate before
  delivery: deterministic citation verification + adversarial second-model
  verdict (tier `legal_review`, gpt-5.6-terra). Max 2 rework rounds, then the
  answer ships with unresolved findings disclosed under «⚠️ Замечания ревьюера».
- Review reports are persisted to `~/.hermes/cache/legal_qa/` for audit.

## MVP limitations

- Routing is done by the built-in LLM router + keyword cascade; package
  triggers are NOT active.
- Tool category boundaries are observe-only (platform-wide tool exposure).
- Russian-language acts only (adilet /rus/ mirror).
