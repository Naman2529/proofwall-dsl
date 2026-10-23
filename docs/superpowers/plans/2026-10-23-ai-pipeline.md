# AI Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire a real LLM (Gemini first) into the English-to-DSL generator and the IR optimizer, and build the evaluation that produces every Review 1 metric.

**Architecture:** A provider-neutral `LLMClient` returns `Completion` objects (text, tokens, latency). The generator and optimizer loops treat every completion as an untrusted candidate that must pass `compile_source()` and, for rewrites, `policyc.verifier.verify()`. An evaluation package runs three suites over data in `evaluation/`, appends raw JSONL (resumable), and renders text, CSV, Markdown and optional PNG reports.

**Tech Stack:** Python 3.8+ standard library (`urllib.request`, `json`, `http.server` for test stubs, `unittest`); optional `z3-solver`, optional `matplotlib`.

**Spec:** `docs/superpowers/specs/2026-10-23-ai-pipeline-design.md` (read it first; section numbers below refer to it).

## Global Constraints

- Python 3.8+: no `match`, no `X | Y` types at runtime, no `dict |`; keep `from __future__ import annotations` like existing modules.
- No new required dependency. Runtime: stdlib only. `z3-solver` and `matplotlib` stay optional.
- Every model output goes through `compile_source()`; every proposed rewrite also through `policyc.verifier.verify()`. Nothing skips either.
- Provider-specific code lives only in `policyc/ai/llm_client.py`.
- The API key (`GEMINI_API_KEY`) is never printed, logged, stored in results, or included in exception messages.
- Env vars: `GEMINI_API_KEY`, `POLICYC_LLM` (`gemini` default, `fake`), `POLICYC_MODEL` (default `gemini-flash-lite-latest`).
- Optimizer default `mode="equivalent"`, `samples=1`, `max_attempts=3`; generator default `max_attempts=3`; Gemini `temperature` 0.2.
- Tests run with `python -m unittest discover -s tests` (from repo root), must pass with and without z3, and never touch the network.
- Diagnostic codes and `Diagnostic.to_dict()` shape unchanged.
- Fake-LLM outputs carry the header line `FAKE - not real results`.
- Match surrounding style: module docstrings explaining the phase, `%`-formatting, dataclasses.

## Review Focus

1. **Model reply that is empty, prose-only, or prose around a fenced block** → treated as an invalid attempt (L001/P001 fed back), never a crash. Tests in Task 4 and Task 5.
2. **Gemini response with no `candidates` or a candidate with no `content.parts` (safety block)** → no candidates: `LLMError("bad_response")`; candidate without parts: `Completion(text="")`. Test in Task 1.
3. **Non-ASCII in model output (smart quotes, `’`, `é`)** → lexer errors fed back; files written UTF-8; no `UnicodeEncodeError`. Tests in Task 4 and Task 8.
4. **Raw JSONL with a truncated last line (process killed mid-write)** → bad line ignored, that task re-run on resume. Test in Task 6.
5. **Quota hit mid-suite** → report still renders from partial raw data, `not_run` counted separately and excluded from rates. Test in Task 7.

---

### Task 1: LLM client

**Files:**
- Modify: `policyc/ai/llm_client.py` (rewrite; keep `strip_code_fence`)
- Test: `tests/test_llm_client.py`

**Interfaces:**
- Produces:
  - `Completion(text: str, prompt_tokens: Optional[int] = None, output_tokens: Optional[int] = None, latency_s: float = 0.0)` (dataclass)
  - `LLMError(kind: str, message: str)`; `.kind` in `{"quota", "auth", "network", "bad_response"}`
  - `as_client(llm) -> client`: returns `llm` unchanged if it has `.complete`, else wraps a `str -> str` callable; wrapper `.name == "function"`, sets `latency_s`, tokens `None`
  - `FakeClient(replies, name="fake")`: `replies` is a list (consumed in order, last one repeats) or a callable `prompt -> str`; `.prompts: List[str]` records every prompt; `.complete(prompt, n=1)` returns n completions
  - `GeminiClient(api_key: Optional[str] = None, model: Optional[str] = None, temperature: float = 0.2, base_url: str = "https://generativelanguage.googleapis.com/v1beta", max_retries: int = 3, backoff_s: float = 2.0, timeout_s: float = 60.0)`; `.name == "gemini:<model>"`; `.complete(prompt, n=1) -> List[Completion]`
  - `default_client(name: Optional[str] = None) -> client` (reads `POLICYC_LLM`)
  - `total_tokens(completions) -> Optional[int]`
  - `strip_code_fence(text) -> str` (unchanged behaviour)

- [ ] **Step 1: Write failing tests** in `tests/test_llm_client.py`, with a `http.server` stub on `127.0.0.1:0` in a thread whose handler serves a queue of `(status, headers, body)` and records request path, headers and JSON body. `GeminiClient(api_key="TESTKEY", base_url=stub_url, backoff_s=0)`:
  - `test_success_returns_text_tokens_and_latency`: body `{"candidates":[{"content":{"parts":[{"text":"ALLOW a READ r"}]}}],"usageMetadata":{"promptTokenCount":10,"candidatesTokenCount":4}}` → `[Completion("ALLOW a READ r", 10, 4, >0)]`; request path ends `/models/gemini-flash-lite-latest:generateContent`; header `x-goog-api-key == "TESTKEY"`; body has `generationConfig.temperature == 0.2`.
  - `test_n_candidates_in_one_request`: n=2, two candidates, `candidatesTokenCount` 8 → two completions with output_tokens 4 each, prompt tokens only on the first; exactly 1 request.
  - `test_candidate_count_rejected_falls_back_to_sequential`: first response 400 mentioning `candidateCount`, then two 200s → 2 completions, 3 requests total.
  - `test_429_then_success` (2 requests) and `test_429_exhausted_raises_quota` (`max_retries=1` → `LLMError` kind `quota`).
  - `test_401_raises_auth_without_retry` (1 request).
  - `test_missing_key_raises_auth_before_request` (env var cleared via `mock.patch.dict(os.environ, {}, clear=True)`; 0 requests).
  - `test_no_candidates_is_bad_response`; `test_candidate_without_parts_is_empty_text` (Review Focus 2).
  - `test_malformed_json_is_bad_response`.
  - `test_key_never_in_error_messages`: for every error case above, `"TESTKEY" not in str(err)`.
  - `test_as_client_wraps_plain_function`, `test_fake_client_list_and_callable`, `test_default_client_env` (`POLICYC_LLM=fake` → `FakeClient`; unknown name → `ValueError`).

- [ ] **Step 2: Run** `python -m unittest tests.test_llm_client -v` from `tests/`-aware root (`python -m unittest discover -s tests -p "test_llm_client.py" -v`). Expected: FAIL (ImportError on `Completion`).

- [ ] **Step 3: Implement** in `policyc/ai/llm_client.py`. Request body: `{"contents":[{"role":"user","parts":[{"text": prompt}]}],"generationConfig":{"temperature":t,"candidateCount":n}}` (omit `candidateCount` when n == 1). Use `urllib.request.Request` with `Content-Type: application/json`. Map `HTTPError` 429/503 → retry with `Retry-After` seconds if present else `backoff_s * 2**i`; 401/403 → `auth`; 400 whose body mentions `candidateCount` → sequential fallback; other HTTP / `URLError` → `network`. Messages built from status code and `error.message` of the JSON body only.

- [ ] **Step 4: Run** the same command. Expected: all pass. Then `python -m unittest discover -s tests` → existing tests still pass (generator and optimizer still call `llm(prompt)` until Tasks 4–5; `as_client` is additive).

- [ ] **Step 5: Commit** `git add policyc/ai/llm_client.py tests/test_llm_client.py && git commit -m "Add provider-neutral LLM client with Gemini REST backend"`

---

### Task 2: Prompts

**Files:**
- Create: `policyc/ai/prompts.py`
- Test: `tests/test_prompts.py`

**Interfaces:**
- Consumes: `Diagnostic.to_dict()`; `Vocabulary` is defined here (moved from spec 6.1 so prompts and generator share it).
- Produces:
  - `Vocabulary(subjects: List[str], resources: Dict[str, List[str]], attributes: List[str])` with `.declarations() -> str` (the `SUBJECT`/`RESOURCE ... SUPPORTS` lines)
  - `generation_prompt(english: str, vocabulary: Optional[Vocabulary]) -> str`
  - `generation_fix_prompt(english: str, vocabulary: Optional[Vocabulary], dsl: str, errors: List[dict]) -> str`
  - `optimization_prompt(source: str, ir_dump: str, warnings: List[dict], feedback: Optional[str]) -> str`
  - `PROMPT_VERSION: str` (first 12 hex chars of sha256 over all template strings)

- [ ] **Step 1: Write failing tests:** generation prompt contains the EBNF line `policy        -> effect IDENT action IDENT ( WHEN or_expr )?`, "deny", the vocabulary's declaration lines and attribute names, two `Example` blocks, the English text, and "Output only"; with `vocabulary=None` it still builds and says names are free to choose. Fix prompt numbers the previous attempt (`  1| ...`) and contains `"code": "P001"` from a sample error dict. Optimization prompt contains the source, IR dump, each warning code, the words "ACCESS" and "OR" in the allowed-rewrites list, "keep the SUBJECT and RESOURCE lines unchanged", "keep every DENY rule", and the feedback text when given. `PROMPT_VERSION` is 12 lowercase hex chars and changes when a template constant is patched (`mock.patch.object(prompts, "GRAMMAR", "x")` then recompute via `prompts.fingerprint()`).

- [ ] **Step 2: Run** `python -m unittest discover -s tests -p "test_prompts.py" -v`. Expected: FAIL (module missing).

- [ ] **Step 3: Implement** templates as module constants (`GRAMMAR`, `SEMANTICS`, `RULES`, `EXAMPLES`, `GEN_TEMPLATE`, `FIX_TEMPLATE`, `OPT_TEMPLATE`), `fingerprint() -> str`, `PROMPT_VERSION = fingerprint()`. Grammar text copied from `policyc/parser.py` docstring. Examples: one simple (two rules, no condition), one with `ACCESS`, a DENY exception and `NOT`; both must compile (test it).

- [ ] **Step 4: Run** same command → PASS.

- [ ] **Step 5: Commit** `git commit -m "Add LLM prompt templates with version fingerprint"`

---

### Task 3: Metrics

**Files:**
- Create: `policyc/ai/metrics.py`
- Test: `tests/test_metrics.py`

**Interfaces:**
- Consumes: `CompileResult`, `IRProgram`, `VerificationResult`.
- Produces:
  - `Size = Tuple[int, int, int]`; `size(res: CompileResult) -> Size` = (IR rules, policy statements in AST, condition nodes summed over policies)
  - `TIERS = ("invalid", "syntactically_valid", "semantically_valid", "verified", "improving")`
  - `tier(res: CompileResult, verified: Optional[bool] = None, smaller: Optional[bool] = None) -> str`
  - `same_declarations(a: IRProgram, b: IRProgram) -> Optional[str]` (None if equal, else a one-line description of the difference)
  - `drops_deny_guard(original: IRProgram, candidate: IRProgram) -> bool` (some original DENY `(subject, action, resource, cond_key)` absent from candidate DENYs)

- [ ] **Step 1: Write failing tests:** `size` of `examples/optimizable.pol` is `(9, 7, 3)` (9 IR rules, 7 policy statements, 3 `Attr` nodes); `tier` returns `invalid` for source with `;`, `syntactically_valid` for undefined subject, `semantically_valid` for valid with `verified=None` or `False`, `verified` with `verified=True, smaller=False/None`, `improving` with both True; `same_declarations` is None for `optimizable` vs `candidate_good` and names the missing subject when one is dropped; `drops_deny_guard` True for `optimizable` vs `candidate_drops_deny`, False vs `candidate_good`.

- [ ] **Step 2: Run** `python -m unittest discover -s tests -p "test_metrics.py" -v` → FAIL.

- [ ] **Step 3: Implement.** Condition nodes: count `Attr`, `Not`, `And`, `Or` nodes recursively.

- [ ] **Step 4: Run** → PASS.

- [ ] **Step 5: Commit** `git commit -m "Add policy size, output tier and declaration/DENY-guard checks"`

---

### Task 4: Generator

**Files:**
- Modify: `policyc/ai/generator.py` (rewrite)
- Modify: `tests/test_cli_ai.py` only if an assertion depends on the old placeholder prompt (the existing `generate_policy("...", llm=fake_llm)` call and its asserts must keep passing unchanged)
- Test: `tests/test_generator.py`

**Interfaces:**
- Consumes: `as_client`, `default_client`, `strip_code_fence`, `Completion`, `LLMError` (Task 1); `Vocabulary`, `generation_prompt`, `generation_fix_prompt` (Task 2); `compile_source`.
- Produces:
  - `Attempt(dsl: str, result: CompileResult, completion: Completion)`
  - `GenerationResult(english: str, attempts: List[Attempt])` with properties `ok`, `dsl`, `retries`, `tokens -> Optional[int]`, `latency_s -> float`
  - `generate_policy(english: str, vocabulary: Optional[Vocabulary] = None, llm=None, max_attempts: int = 3) -> GenerationResult`
  - re-export `Vocabulary` from `policyc.ai.generator`

- [ ] **Step 1: Write failing tests** with `FakeClient`: first reply has `FLY`, second fixed → `ok`, `retries == 1`, `fake.prompts[1]` contains `"code": "P001"` and `  3| ALLOW admin FLY db`; vocabulary lines appear in `fake.prompts[0]`; `max_attempts=2` with two bad replies → `not ok`, 2 attempts, `dsl is None`; a reply that compiles with warnings stops after 1 attempt; tokens summed across attempts (`None` if any attempt lacks counts); Review Focus 1: replies `""`, `"Sure! Here is the policy:"`, and `"Here you go:\n```\nSUBJECT a\n...\n```\nHope this helps"` → first two are failed attempts (not exceptions), third compiles; Review Focus 3: a reply using `’` → failed attempt with `L001` in the fix prompt; `LLMError` from the client propagates.

- [ ] **Step 2: Run** `python -m unittest discover -s tests -p "test_generator.py" -v` → FAIL.

- [ ] **Step 3: Implement.** `strip_code_fence` must also extract the first fenced block when prose surrounds it (extend it in `llm_client.py`, keep old behaviour for fence-only text). Only `result.errors` trigger a retry.

- [ ] **Step 4: Run** the generator tests and `python -m unittest discover -s tests` → all PASS.

- [ ] **Step 5: Commit** `git commit -m "Implement English-to-DSL generator with compiler-guided correction"`

---

### Task 5: Optimizer

**Files:**
- Modify: `policyc/ai/llm_optimizer.py` (rewrite)
- Modify: `tests/test_integration.py` (`TestLlmOptimizerLoop` asserts updated to the new stage names only where needed)
- Test: `tests/test_llm_optimizer.py`

**Interfaces:**
- Consumes: Task 1 client, Task 2 `optimization_prompt`, Task 3 `size`, `tier`, `same_declarations`, `drops_deny_guard`; `compile_source`; `policyc.verifier.verify`.
- Produces:
  - `STAGES = ("compile", "declarations", "verifier", "not_smaller", "accepted")`
  - `Proposal(dsl, attempt: int, stage: str, compiled: CompileResult, verification: Optional[VerificationResult], size: Optional[Size], drops_deny_guard: bool, completion: Completion)`; properties `accepted`, `tier`
  - `OptimizeResult(original: CompileResult, proposals: List[Proposal], mode: str)`; properties `accepted -> Optional[Proposal]` (the chosen one), `rules_before`, `rules_after`, `statements_before`, `statements_after`, `stage_counts -> Dict[str, int]`, `tokens`, `latency_s`, `final_source -> str`
  - `optimize_with_llm(source: str, llm=None, max_attempts: int = 3, samples: int = 1, mode: str = "equivalent") -> OptimizeResult`; raises `ValueError` if `source` does not compile

- [ ] **Step 1: Write failing tests** on `examples/optimizable.pol` with `FakeClient`:
  - bad then good (`candidate_bad.pol`, `candidate_good.pol`) → stages `["verifier", "accepted"]`, `fake.prompts[1]` contains `authenticated=False`, rules 9→6.
  - non-compiling reply → stage `compile`, error line in next prompt.
  - reply with a SUBJECT removed → stage `declarations`.
  - reply equal to the original → stage `not_smaller`, next prompt contains "not smaller".
  - `samples=3` with replies [bad, original, good] in one attempt → one call to `complete(n=3)`, chosen proposal is the good one, loop stops after attempt 1.
  - two accepted proposals of different size in one attempt → the smaller is chosen.
  - all attempts fail → `accepted is None`, `final_source == source`, `rules_after == rules_before`.
  - `candidate_stricter.pol` rejected by default, accepted with `mode="non-weakening"`.
  - a proven proposal that removes a redundant DENY sets `drops_deny_guard=True` and is still accepted.
  - Review Focus 1: empty reply → stage `compile`, no exception.

- [ ] **Step 2: Run** `python -m unittest discover -s tests -p "test_llm_optimizer.py" -v` → FAIL.

- [ ] **Step 3: Implement** per spec 6.2 table. Original's warnings passed to the prompt as `[d.to_dict() for d in original.warnings]`. Feedback for the next attempt concatenates one line block per failed proposal of the current attempt (cap 3 counterexamples via `result.feedback(limit=3)`).

- [ ] **Step 4: Run** optimizer tests and full suite → PASS (with z3 and with system Python without z3).

- [ ] **Step 5: Commit** `git commit -m "Implement LLM optimizer with staged verification and best-of-N"`

---

### Task 6: Evaluation data and loader

**Files:**
- Create: `evaluation/generation/tasks.json`, `evaluation/generation/g01.pol` … `g20.pol`
- Create: `evaluation/optimization/o1_*.pol` … `o8_*.pol` (descriptive suffixes)
- Create: `evaluation/semantic/s01_*.pol` … `s10_*.pol`
- Create: `policyc/ai/evaluation/__init__.py`, `policyc/ai/evaluation/tasks.py`
- Delete: `policyc/ai/evaluation.py` (its `baseline_metrics` moves to `policyc/ai/evaluation/__init__.py`, same signature and keys)
- Test: `tests/test_evaluation_data.py`

**Interfaces:**
- Consumes: `Vocabulary` (Task 2), `compile_file`, `compile_source`.
- Produces:
  - `EVAL_ROOT` (repo `evaluation/` path)
  - `GenTask(id, difficulty, english, vocabulary: Vocabulary, reference_path)`; `load_generation_tasks(root=EVAL_ROOT) -> List[GenTask]`
  - `load_optimization_policies(root=EVAL_ROOT) -> List[Tuple[str, str]]` (name, path), sorted
  - `Expectation(file, line, code)`; `load_semantic_cases(root=EVAL_ROOT) -> List[Tuple[str, List[Expectation]]]` parsing `# expect: CODE[, CODE]`
  - `baseline_metrics(path) -> dict` (unchanged)

- [ ] **Step 1: Write failing integrity tests:** 20 tasks, ids `g01`–`g20` unique, difficulty counts `{"easy": 6, "medium": 8, "hard": 6}`; every reference compiles with no errors; the reference's declared subjects/resources/supports equal the task vocabulary and every attribute in the reference is in `vocabulary.attributes`; 8 optimization files compile; for o1–o3 the compiler-only optimizer reduces IR rules, for o4–o6 it does not, for o8 (control) it does not; 10 semantic files; every marker code is a known code; each code in S001–S006, W101–W104, W106, W107 appears in at least one marker; at least one file has no markers on some policy lines (clean lines) — assert total policy lines > total markers. `baseline_metrics("examples/optimizable.pol")` returns the same keys as before.

- [ ] **Step 2: Run** `python -m unittest discover -s tests -p "test_evaluation_data.py" -v` → FAIL.

- [ ] **Step 3: Write the data** per spec 6.3 (difficulty definitions, the o-file categories, semantic coverage including at least two documented known-miss cases marked with a trailing `# known miss: <reason>` comment alongside `# expect:`). English is phrased as requirements. Then implement the loaders.

- [ ] **Step 4: Run** → PASS; then `python -m policyc compile` on three random references to eyeball clean output.

- [ ] **Step 5: Commit** `git commit -m "Add evaluation benchmark data and loaders"`

---

### Task 7: Evaluation runner

**Files:**
- Create: `policyc/ai/evaluation/run.py`
- Test: `tests/test_evaluation_run.py`

**Interfaces:**
- Consumes: Tasks 1–6.
- Produces:
  - `RunConfig(llm_name: str, model: Optional[str], samples: int = 1, attempts: int = 3, mode: str = "equivalent", limit: Optional[int] = None)`; `.key_fields() -> dict` (adds `PROMPT_VERSION`)
  - `run_semantic(root) -> List[dict]` (one record per file: expected, reported, matched, missed, false_alarms as lists of `[line, code]`)
  - `run_generation(client, cfg, raw_path, root) -> List[dict]`
  - `run_optimization(client, cfg, raw_path, root) -> List[dict]` (each record holds `baseline` and `llm` sections)
  - `fake_client_for(root) -> FakeClient` (generation prompts answered with the matching reference, the first easy task answered wrongly once to exercise correction; optimization prompts answered with the baseline-optimized source rendered from IR, or the original when the baseline removes nothing)
  - `read_raw(path) -> List[dict]` (skips undecodable lines), `append_raw(path, record)`
  - Record fields: `suite, task, status ("ok"|"not_run"), error_kind, config, attempts: [{dsl, tier, errors: [codes], tokens, latency_s, stage?}], final_tier, verified_vs_reference (generation), sizes (optimization), tokens, latency_s, fake: bool`

- [ ] **Step 1: Write failing tests** in a temp dir: generation with `fake_client_for` writes 20 records, all `fake: true`; rerun with the same config appends nothing; rerun with `samples=2` appends new records; a truncated final line in the JSONL is skipped and that task reruns (Review Focus 4); a client raising `LLMError("quota")` on task 3 yields `status "not_run"`, `error_kind "quota"` for that task and the run continues; `limit=5` → 5 records; generation correctness uses `verify(reference, generated, mode="equivalent")`; optimization records contain baseline IR before/after for all 8 files; semantic run matches expectations by `(line, code)`.

- [ ] **Step 2: Run** `python -m unittest discover -s tests -p "test_evaluation_run.py" -v` → FAIL.

- [ ] **Step 3: Implement.** Records are written immediately after each task (`append_raw` with `\n` and flush). Resume key = `(task, json.dumps(cfg.key_fields(), sort_keys=True))`. IR-to-DSL rendering for the fake optimizer lives in `run.py` as `render_source(ir: IRProgram) -> str` (declarations then one statement per IR rule using `logic.canonical`-compatible `ast_nodes.cond_to_str`).

- [ ] **Step 4: Run** → PASS.

- [ ] **Step 5: Commit** `git commit -m "Add resumable evaluation runner for all three suites"`

---

### Task 8: Reports and charts

**Files:**
- Create: `policyc/ai/evaluation/report.py`, `policyc/ai/evaluation/charts.py`
- Test: `tests/test_evaluation_report.py`

**Interfaces:**
- Consumes: records from Task 7.
- Produces:
  - `summarize(gen: List[dict], opt: List[dict], sem: List[dict]) -> dict` (all metrics of spec 7.1 and tier counts of 7.2; rates exclude `not_run`)
  - `write_reports(out_dir, summary, gen, opt, sem, run_info: dict) -> List[str]` writes `summary.txt`, `generation.csv`, `optimization.csv`, `proposals.csv`, `semantic.csv`, `results.md`, `run.json`; returns paths
  - `charts.write_charts(out_dir, summary) -> List[str]` (empty list when matplotlib missing)

- [ ] **Step 1: Write failing tests** on hand-built records: first-try compile rate, after-correction rate, mean cycles, equivalence rates per difficulty, tier counts, baseline vs LLM reduction, stage counts, semantic detection rate — each asserted with literal expected numbers; a `not_run` record is excluded from rates and counted in `not_run`; partial input (only semantic records) still writes every file (Review Focus 5); with `fake: true` records every text file's first line is `FAKE - not real results` (CSV as a `#` comment line, JSON as a `"fake": true` field and `"banner"`); `run.json` never contains the value of `GEMINI_API_KEY` (set it to a sentinel in the test env); files are UTF-8 and survive a record containing `’` (Review Focus 3); `write_charts` returns `[]` when `matplotlib` import is patched to fail, and PNG paths when it is available (skip if not installed).

- [ ] **Step 2: Run** `python -m unittest discover -s tests -p "test_evaluation_report.py" -v` → FAIL.

- [ ] **Step 3: Implement.** `results.md`: run info, four tables (generation by difficulty, classification tiers, optimization per policy, semantic per code) and 3–6 factual bullet findings computed from the numbers (no adjectives the numbers do not support). Charts: matplotlib `Agg` backend, one PNG per chart in spec 7.3.

- [ ] **Step 4: Run** → PASS.

- [ ] **Step 5: Commit** `git commit -m "Add evaluation reports (txt, csv, md, json) and optional charts"`

---

### Task 9: CLI

**Files:**
- Modify: `policyc/__main__.py`
- Test: `tests/test_cli_llm.py`

**Interfaces:**
- Consumes: everything above. `--llm` maps to `default_client(name)` or `fake_client_for(root)` for `evaluate`; for `generate`/`optimize`, `--llm fake` uses a `FakeClient` that echoes a valid policy (generate: the task's reference when `--task` is given, else a policy built from the vocabulary allowing nothing; optimize: the input unchanged) and output is prefixed with the FAKE banner.
- Produces: subcommands `generate`, `optimize`, `evaluate` per spec section 8 and exit codes 0/1/2.

- [ ] **Step 1: Write failing tests** (capture stdout/stderr like `tests/test_cli_ai.py`): `generate --task g01 --llm fake -o out.pol` exits 0 and writes a compiling UTF-8 file; `generate "x" --subjects a --resource db=READ --attributes y --llm fake` exits 0; `generate` with no `GEMINI_API_KEY` and default llm exits 2 with a message mentioning `GEMINI_API_KEY` and no traceback; `optimize examples/optimizable.pol --llm fake` exits 1 (nothing accepted) and prints the stage of each proposal; `optimize` on `syntax_errors.pol` exits 1 with diagnostics; `evaluate --llm fake --out tmp` exits 0 and creates every report file; `evaluate --suite semantic --out tmp` with no key exits 0; `evaluate --out tmp` with no key runs semantic + baseline and prints that AI suites were skipped; bad `--resource` syntax exits 2.

- [ ] **Step 2: Run** `python -m unittest discover -s tests -p "test_cli_llm.py" -v` → FAIL.

- [ ] **Step 3: Implement** the three subcommands; `--resource NAME=ACT[,ACT]` repeatable.

- [ ] **Step 4: Run** → PASS, then full suite.

- [ ] **Step 5: Commit** `git commit -m "Add generate, optimize and evaluate CLI commands"`

---

### Task 10: Docs, final verification, push

**Files:**
- Create: `docs/EVALUATION.md`
- Modify: `README.md` (sections 1, 2 table, 4 phase list, 5 demo, 7, 8 layout), `policyc/ai/README.md`, `policyc/ai/__init__.py` docstring, `.gitignore` (add `evaluation/results/`; `docs/EVALUATION.md` tells the team to commit a real run with `git add -f evaluation/results`, so fake runs never land in git)

- [ ] **Step 1: Write docs** per spec section 11; every command in them is copy-pasteable and runs with `--llm fake`.
- [ ] **Step 2: Verify** on a fresh clone of the branch: `python -m unittest discover -s tests` with z3 venv and with system Python (expect all pass / only `test_verification` skipped); run every command in README and `docs/EVALUATION.md` with `--llm fake`; run `python -m policyc evaluate` with no key and confirm the skip message; grep the repo for `TODO(AI part)` and `NotImplementedError` (expect none in `policyc/ai/`).
- [ ] **Step 3: Commit** `git commit -m "Document the AI pipeline and evaluation"`, push `ai-pipeline`, open a PR into `add-core-compiler` (retarget to `main` after PR #2 merges) with a description listing what needs the real key.
