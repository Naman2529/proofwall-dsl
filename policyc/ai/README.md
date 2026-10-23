# policyc/ai: hand-off notes for the AI part

Status at Review 2: **the compiler side is done, the LLM side is not started.**
This folder is where the AI work goes. Everything here talks to the compiler
only through its public API (`compile_source`, `verify`, `Diagnostic.to_dict()`),
so nothing in the compiler should need to change.

| File | What is already here | What you need to do |
|---|---|---|
| `llm_client.py` | The `LLMClient` type (a function: prompt in, text out) and `default_client()` | **Implement `default_client()`** for whatever model we pick. Keep the signature. |
| `generator.py` | The English to DSL correction loop: ask, compile, send JSON diagnostics back, retry. Works today with a fake LLM (see `tests/test_cli_ai.py`). | **Write the real prompts** in `build_prompt()` and `build_fix_prompt()`. Tune `max_attempts`. |
| `llm_optimizer.py` | The propose, verify, retry-with-counterexample loop. The verifier decides, never the model. | **Write the prompt** in `build_optimize_prompt()`. |
| `evaluation.py` | The compiler-only baseline numbers (`baseline_metrics`). | **Write `run_evaluation()`**: the AI vs baseline table (validity, rule reduction, tokens, latency). |

Rules for this folder (please keep them):

1. The model never decides anything. Every model output goes through
   `compile_source()` (is it valid?) and, for the optimizer, `verify()`
   (does it mean the same thing?). Do not add a path that skips either.
2. Do not import a specific provider SDK anywhere except `llm_client.py`, so the
   model stays swappable and the tests can keep using a fake.
3. If you decide one of these files is not needed, delete it and its test,
   and update the "How much is done" table in the top-level README.
