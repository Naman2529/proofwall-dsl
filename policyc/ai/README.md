# policyc/ai: the AI stages

The two untrusted stages from the Review 1 "division of responsibility" table,
plus the evaluation that measures them. Everything here talks to the compiler
only through its public API (`compile_source`, `verifier.verify`,
`Diagnostic.to_dict()`).

| File | What it does |
|---|---|
| `llm_client.py` | The one place a model is called. `GeminiClient` (REST, standard library, retries on 429/503), `FakeClient` (scripted), `as_client()` for plain functions. Returns `Completion` objects with tokens and latency. |
| `prompts.py` | Every prompt template, and `PROMPT_VERSION`, a fingerprint of all of them. |
| `generator.py` | Objective 4. `generate_policy(english, vocabulary, llm)`: ask, compile, feed the JSON diagnostics back, retry. |
| `llm_optimizer.py` | Objective 5. `optimize_with_llm(source, llm, samples, mode)`: each proposal must compile, keep the declarations, be proven by the verifier, and be smaller. Counterexamples go back to the model. |
| `metrics.py` | Policy size, the five-tier classification, the declaration and DENY-guard checks. |
| `cli.py` | `python -m policyc generate / optimize / evaluate`. |
| `evaluation/` | Benchmark loaders, the resumable runner, reports and charts. See `docs/EVALUATION.md`. |

Configuration: `GEMINI_API_KEY`, `POLICYC_LLM` (`gemini` or `fake`),
`POLICYC_MODEL` (default `gemini-flash-lite-latest`).

Rules for this folder (please keep them):

1. The model never decides anything. Every model output goes through
   `compile_source()`, and every rewrite through `verify()`. Do not add a path
   that skips either.
2. Do not import a provider SDK or call a provider anywhere except
   `llm_client.py`, so the model stays swappable and tests keep using a fake.
   Adding a provider means one class with `name` and `complete(prompt, n)`.
3. Never write the API key anywhere: not in files, logs, results or errors.
4. Prompt changes are fine; `PROMPT_VERSION` changes with them and the
   evaluation keeps their results apart.
