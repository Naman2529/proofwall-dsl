# AI pipeline design: LLM generator, LLM optimizer, evaluation

Date: 2026-10-23. Branch: `ai-pipeline` (stacked on `add-core-compiler`, PR #2).
Status: approved in conversation section by section; this document is for written review.

## 1. Goal

Finish Review 1 objectives 3 (metric), 4, 5 (AI half) and 6 (evaluation) so the
project is complete for the final demo, with everything needed later for a report
or slides. No holes: every promised metric is produced by code in this repository.

The rule that governs every design choice, from Review 1: **the LLM proposes, the
compiler decides.** Model output is only ever a candidate. It is accepted only
through `compile_source()` and, for rewrites, `policyc.verifier.verify()`.

### Measured outcomes (from the Review 1 report)

| Obj. | Measured by | Produced by |
|---|---|---|
| 3 | seeded duplicates and conflicts detected in the test suite | semantic suite (section 6.3) |
| 4 | % LLM outputs that parse first try, after correction; average correction cycles | generation suite |
| 5 | % suggestions accepted vs rejected; reduction in rule count | optimization suite |
| 6 | AI pipeline vs compiler-only baseline on validity, rule reduction, tokens, latency per policy | evaluation report |
| novelty | every AI output classified: invalid, syntactically valid, semantically valid, verified, improving | classification (section 7.2) |

### Non-goals

- Grammar-constrained decoding or provider JSON-schema modes (approach B, rejected:
  it hides the errors the correction loop is meant to measure and ties us to one API).
- Fine-tuning, embeddings, retrieval.
- Changing the DSL, the IR or the verifier's guarantees.
- Proving codegen (unchanged limitation).

## 2. Decisions

| Decision | Choice | Why |
|---|---|---|
| Approach | A: plain-text DSL output judged by the compiler, plus C's `--samples N` (default 1) | Matches Review 1's novelty argument; works with any hosted API |
| Provider | Gemini first, via REST with the Python standard library | Team's likely key; no new runtime dependency |
| Model | `POLICYC_MODEL`, default `gemini-flash-lite-latest` | Aliases survive model retirement; flash-lite has the most free quota. Confirm against the official rate-limits page once a key exists |
| Optimizer verification mode | `equivalent` default, `--mode non-weakening` optional | Review 1 obj. 5: "decision ... unchanged" |
| Warnings in generation | do not trigger a retry; recorded | A policy that compiles has one meaning; warnings are reported in the evaluation |
| Vocabulary | each generation task supplies subjects, resources (+ SUPPORTS) and attributes | "Controlled English"; makes reference comparison possible |
| Data location | new top-level `evaluation/` | `benchmarks/` stays the verifier timing benchmark |

## 3. Architecture

```
English + vocabulary ─▶ generator ─▶ LLM ─▶ DSL ─▶ compile_source ─┐
                           ▲                                        │ errors (JSON diagnostics)
                           └────────────────────────────────────────┘
                                                                     ok ─▶ policy (+ verify vs reference in evaluation)

policy ─▶ llm_optimizer ─▶ LLM ─▶ N proposals ─▶ compile ─▶ same declarations? ─▶ verify ─▶ smaller? ─▶ accept best
               ▲                                  │            │                    │          │
               └──── feedback: errors / counterexamples / "not smaller" ────────────┴──────────┘
                                                               no proposal accepted ─▶ keep original

evaluation runner ─▶ suites (generation, optimization, semantic) ─▶ raw JSONL ─▶ report (txt, csv, md, png)
```

New and changed files:

```
policyc/ai/llm_client.py         Completion, LLMClient protocol, as_client(), GeminiClient, FakeClient, default_client()
policyc/ai/prompts.py            grammar text, examples, prompt builders, PROMPT_VERSION fingerprint
policyc/ai/generator.py          English -> DSL correction loop (rewritten around Completion)
policyc/ai/llm_optimizer.py      propose -> check -> verify -> select loop (rewritten)
policyc/ai/metrics.py            policy size, classification, DENY-guard check
policyc/ai/evaluation/__init__.py
policyc/ai/evaluation/tasks.py   load and validate evaluation/ data
policyc/ai/evaluation/run.py     suites, resume, raw JSONL
policyc/ai/evaluation/report.py  summary.txt, CSVs, results.md, run.json
policyc/ai/evaluation/charts.py  optional matplotlib charts
policyc/__main__.py              new subcommands: generate, optimize, evaluate
evaluation/generation/tasks.json + evaluation/generation/*.pol
evaluation/optimization/*.pol
evaluation/semantic/*.pol
docs/EVALUATION.md
```

`policyc/ai/evaluation.py` becomes the `policyc/ai/evaluation/` package;
`baseline_metrics()` keeps its name and output.

## 4. LLM client (`policyc/ai/llm_client.py`)

```python
@dataclass
class Completion:
    text: str
    prompt_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    latency_s: float = 0.0

class LLMClient(Protocol):
    name: str                                   # e.g. "gemini:gemini-flash-lite-latest", "fake"
    def complete(self, prompt: str, n: int = 1) -> List[Completion]: ...

def as_client(llm) -> LLMClient                 # wraps a plain prompt->str function (existing tests keep working)
class LLMError(RuntimeError)                     # .kind in {"quota", "auth", "network", "bad_response"}
```

`GeminiClient`:
- `POST https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent`,
  key in the `x-goog-api-key` header, read from `GEMINI_API_KEY`. Model from `POLICYC_MODEL`.
- `generationConfig`: `temperature` 0.2 (configurable), `candidateCount` = n. If the
  API rejects `candidateCount` > 1 for the model, fall back to n sequential calls.
- Tokens from `usageMetadata` (`promptTokenCount`, `candidatesTokenCount`; split evenly
  across candidates when n > 1, prompt tokens attributed to the first). Latency measured
  around the HTTP call.
- HTTP 429 / 503: retry with exponential backoff (honouring `Retry-After` when present),
  3 retries, then `LLMError("quota")`. 401/403: `LLMError("auth")` immediately, no retry.
  Missing key: `LLMError("auth", "set GEMINI_API_KEY")` before any request.
- The key is never logged, printed, stored in results, or included in exceptions.

`FakeClient(replies)`: replays scripted replies (cycling or by prompt-matching
function), reports `name = "fake"`. Used by tests and `--llm fake`.

`default_client()`: `POLICYC_LLM` selects the provider (`gemini` default, `fake`).
Adding a provider = one class implementing `complete()`.

## 5. Prompts (`policyc/ai/prompts.py`)

One module so prompts are reviewable and fingerprinted (`PROMPT_VERSION` = short hash
of the templates, written to `run.json`).

Shared preamble: the EBNF grammar, the semantics (deny-overrides, default deny, ACCESS
= every supported action, NOT > AND > OR), "output only DSL, no prose, no code fences"
(fences are stripped anyway).

Generation prompt: preamble + vocabulary (SUBJECT/RESOURCE lines to declare, attribute
names allowed) + two worked examples (English -> DSL) + the requirement.
Fix prompt: the previous attempt with line numbers + `Diagnostic.to_dict()` JSON of the
errors + "fix only what the errors point at".

Optimization prompt: preamble + source policy + IR dump + the compiler's warnings
(W101-W104, W107) + allowed rewrites (drop duplicates; merge actions into ACCESS when
they cover every supported action; join same-effect, same-request conditions with OR;
simplify conditions; drop ALLOWs an unconditional DENY always beats) + constraints
(keep SUBJECT/RESOURCE lines unchanged; keep explicit DENY rules).
Retry feedback appended per failure kind (section 6.2).

## 6. Components

### 6.1 Generator (`policyc/ai/generator.py`)

```python
@dataclass
class Vocabulary:
    subjects: List[str]
    resources: Dict[str, List[str]]      # resource -> supported actions
    attributes: List[str]

def generate_policy(english: str, vocabulary: Optional[Vocabulary] = None,
                    llm=None, max_attempts: int = 3) -> GenerationResult
```

Loop: prompt -> one completion -> strip fences -> `compile_source` -> stop if `ok`,
else fix prompt with errors. `GenerationResult` keeps `attempts` (dsl, CompileResult,
Completion), `ok`, `dsl`, `retries`, `tokens`, `latency_s`. `LLMError` propagates (the
evaluation runner records it as "not run"). Only compiler errors trigger a retry: a
policy that compiles but ignores the vocabulary is accepted by the loop and simply
fails the reference-equivalence check in the evaluation.

### 6.2 Optimizer (`policyc/ai/llm_optimizer.py`)

```python
def optimize_with_llm(source: str, llm=None, max_attempts: int = 3, samples: int = 1,
                      mode: str = "equivalent") -> OptimizeResult
```

Per attempt: `complete(prompt, n=samples)`; each proposal goes through the stages in
order and stops at the first failure:

| Stage | Fails when | Feedback for next attempt |
|---|---|---|
| `compile` | errors | the error lines |
| `declarations` | SUBJECT set or RESOURCE->actions map differs from the original | "keep declarations unchanged" + the difference |
| `verifier` | `verify(original, candidate, mode)` not proven | `result.feedback()` (up to 3 counterexamples) |
| `not_smaller` | proven but `size(candidate) >= size(original)` | "correct, but not smaller; look for ..." |
| `accepted` | none of the above | |

`size(policy)` (in `metrics.py`) = (IR rule count, source policy-statement count,
condition node count), compared lexicographically. Among accepted proposals in an
attempt the smallest wins and the loop stops. If none is accepted after all attempts,
the original is kept. `Proposal` records dsl, stage, verification summary, size,
`drops_deny_guard` (a DENY rule of the original has no syntactically equal DENY in the
candidate's IR), completion. `OptimizeResult` exposes `accepted`, `rules_before/after`
(IR and statements), proposal counts by stage, tokens, latency.

### 6.3 Evaluation data (`evaluation/`)

`generation/tasks.json`: list of `{id, difficulty: easy|medium|hard, english,
vocabulary: {subjects, resources, attributes}, reference: "<id>.pol"}`. 20 tasks:
6 easy (1-2 rules, no conditions), 8 medium (conditions, ACCESS, several rules, one DENY
exception), 6 hard (nested AND/OR/NOT where precedence matters, overlapping ALLOW/DENY,
"except when" phrasing). English is written as requirements, not DSL read aloud.

`optimization/*.pol`: 8 policies: 3 the compiler-only optimizer fully handles; 3 that
need rewrites the baseline cannot do (OR-merge, `x AND y` + `x AND NOT y` -> `x`,
READ+WRITE -> ACCESS); 1 mixed; 1 already-optimal control.

`semantic/*.pol`: 10 files with markers `# expect: CODE[, CODE]` on the line where the
diagnostic is reported (diagnostics report at the later rule of a pair, and W106 at the
declaration). Covers S001-S006, W101-W104, W106, W107, clean lines that must not be
flagged, and a few documented hard cases the analyzer is known to miss (e.g. an ALLOW
killed only by two partial DENYs together). Files with S-errors stop before pair checks,
so error and warning cases live in separate files.

### 6.4 Evaluation runner (`policyc/ai/evaluation/`)

```
python -m policyc evaluate [--suite generation|optimization|semantic|all]
                           [--llm gemini|fake] [--samples N] [--attempts N]
                           [--limit N] [--out evaluation/results] [--fresh]
```

- Semantic suite and the compiler-only baseline need no LLM. With no key (and no
  `--llm fake`), AI suites are skipped with a message; the rest still runs.
- Raw records appended to `<out>/raw/<suite>.jsonl`, one per task, keyed by
  (task id, provider, model, samples, attempts, mode, PROMPT_VERSION). Re-running skips
  keys already present (resume after quota); `--fresh` starts over.
- `LLMError` on a task is recorded as `status: "not_run"` with its kind and excluded from
  rates (counts reported separately).
- `--llm fake` uses scripted replies; every output it writes carries
  "FAKE - not real results" in its header.

## 7. Metrics and outputs

### 7.1 Metrics

Generation (overall and per difficulty): % compiling on first try; % compiling after
correction; mean correction cycles (attempts - 1) over compiled tasks and over all;
% equivalent to the reference (verifier, `equivalent` mode) on first try and final;
mean tokens and latency per task.

Optimization (per policy and totals): baseline vs LLM rule reduction (IR rules and
statements, absolute and %); proposals accepted vs rejected (%), rejections by stage;
DENY guards dropped; tokens and latency per policy. Baseline row: validity 100% by
construction, 0 tokens, measured ms.

Semantic: per code expected / detected / missed / false alarms; totals and detection
rate.

### 7.2 Classification of every AI output

| Tier | Rule |
|---|---|
| invalid | any L001 or P001 |
| syntactically valid | parses, has semantic errors (S0xx) |
| semantically valid | compiles |
| verified | generation: equivalent to reference; optimization: proven by verifier |
| improving | optimization only: verified and strictly smaller |

Each attempt and each proposal gets its highest tier reached. Reported as counts and %.

### 7.3 Outputs (`evaluation/results/`)

`summary.txt` (what the CLI prints), `generation.csv` (one row per attempt),
`optimization.csv` (one row per policy, baseline and LLM), `proposals.csv` (one row per
proposal), `semantic.csv` (one row per expected or reported diagnostic),
`results.md` (tables + short factual findings, ready to paste), `run.json` (date,
provider, model, settings, PROMPT_VERSION, task counts; never the key), and PNGs when
matplotlib is installed: classification tiers, baseline vs LLM rule reduction per
policy, first-try vs final correctness by difficulty, detection per code.

## 8. CLI additions (`policyc/__main__.py`)

```
python -m policyc generate "ENGLISH" [--task ID | --subjects a,b --resource db=READ,WRITE ... --attributes x,y]
                                     [--attempts N] [--llm gemini|fake] [-o out.pol]
python -m policyc optimize FILE.pol [--samples N] [--attempts N] [--mode equivalent|non-weakening]
                                    [--llm gemini|fake] [-o out.pol]
python -m policyc evaluate ...      (section 6.4)
```

Exit codes follow the existing convention: 0 success, 1 rejected / not compiled /
nothing accepted, 2 bad usage, I/O, or `LLMError` (message names the kind; never the key).

## 9. Error handling summary

- Model output never bypasses `compile_source()` or `verify()`.
- Anything not proven is rejected (inherited from the verifier: timeouts, caps).
- Network and quota failures surface as `LLMError` with a kind; the evaluation records
  them as not run instead of model failures.
- Missing optional pieces degrade, never crash: no z3 -> enumeration; no matplotlib ->
  no PNGs; no key -> AI suites skipped.

## 10. Testing (no network, no key)

- `GeminiClient` against a local `http.server` stub: success, `candidateCount` fallback,
  429 then success, 429 exhausted, 401, malformed JSON, missing key, key absent from
  every error message and result file.
- Prompts: contain grammar, vocabulary, examples; fix prompt contains numbered attempt
  and diagnostic JSON; fingerprint changes when a template changes.
- Generator and optimizer with `FakeClient`: every stage path in 6.2, best-of-N
  selection, fallback to original, DENY-guard flag, non-weakening mode.
- Metrics: size ordering, classification on hand-built outputs.
- Data integrity: every reference and benchmark policy compiles; each task's vocabulary
  matches its reference's declarations; markers parse. (Detection is the metric, not a
  test assertion.)
- Evaluation end-to-end with `FakeClient` into a temp dir: every output file exists and
  parses, FAKE header present, resume skips done tasks, `not_run` excluded from rates.
- CLI: generate / optimize / evaluate exit codes and outputs with `--llm fake`.

All existing tests keep passing, with and without z3.

## 11. Documentation

New `docs/EVALUATION.md` (setup incl. `GEMINI_API_KEY`, how to run and resume, every
metric's definition, the classification, how to read the outputs, free-tier budget:
a full run is about 20 x 3 + 8 x 3 = 84 requests at most with `--samples 1`).
README: status table (objectives 3-6 done), quick start and demo script with the new
commands, "what is left" replaced by limitations. `policyc/ai/README.md` rewritten to
describe the implemented pieces.

## 12. Done means

1. All tests pass with and without z3, and with no network.
2. `python -m policyc evaluate --llm fake` produces every output file.
3. `python -m policyc evaluate` with no key runs the semantic suite and baseline and
   explains the skip.
4. With a real `GEMINI_API_KEY`, the same command produces real numbers (run by the
   team; results committed afterwards).
5. Docs describe exactly what the code does.
