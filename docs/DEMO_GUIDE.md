# ProofWall / policyc: demo guide and project explainer

BCSE307P Compiler Design Lab. Naman Bhatia (24BDS0056), George Christian Mathew (24BCE2753),
Yohan George (24BAI0356).

This guide has two halves. **Part A** is how to run the demo: setup, a step-by-step
script with what to say, and what to do if something goes wrong. **Part B** explains
every part of the project and how it works, for the report and for questions.

---

## The project in one paragraph

`policyc` is a compiler for a small access-control language: who (subject) may do what
(action) to which thing (resource), and under what conditions. Two AI stages sit inside
the compiler. One turns plain English into a policy; the other proposes smaller,
cleaner policies. The model is never trusted. Every policy it writes must pass the
compiler's lexer, parser and semantic checks, and every rewrite must be **proven** by a
verifier (Z3) to make the same decision for every possible request. In one line:
**the LLM proposes, the compiler decides.**

---

# Part A: running the demo

## A1. Setup (once, before the demo)

```powershell
cd proofwall-dsl
pip install -r requirements.txt matplotlib      # Z3 verifier + charts
$env:GEMINI_API_KEY = "..."                     # your key; never put it in a file
python -m policyc demo-check                    # proves every Review 1 outcome (about 30 s)
python -m policyc evaluate                      # real numbers for the Results tab (skip if evaluation/results exists)
python -m policyc gui                           # opens http://127.0.0.1:8765
```

`demo-check` should end with **ALL OUTCOMES DEMONSTRATED**. With the real model it usually shows
two WARNs on purpose (see step 5 below). `--llm fake` rehearses everything offline.

Defaults: model `gemini-3.5-flash-lite` (about 1 s per call); change with
`$env:POLICYC_MODEL`. The free tier allows 15 requests a minute; the client waits when
Google asks it to.

## A2. Demo script (about 8 minutes, GUI)

The GUI has five tabs and a Gemini / Fake switch in the header. Every example below is
in the `demo/` folder and is already selected by default in its tab.

| # | Tab | Do this | Say this | Objective |
|---|---|---|---|---|
| 1 | Compile | `demo/01_full_language.pol` (default). Click through Lexer, Parser, Semantic, IR, Optimizer, Verifier, Codegen. | "A hospital policy going through every compiler phase. Tokens, the syntax tree, semantic checks, a flat IR where ACCESS is expanded, and the target JSON." | 1, 6 |
| 2 | Compile | Pick `demo/02_syntax_errors.pol`. Click a red diagnostic. | "Six errors in one run, because the parser recovers and keeps going. Each one has a line, a column and the tokens it expected. `allow` gets a 'did you mean ALLOW?' hint." | 2 |
| 3 | Compile | `demo/03_semantic_errors.pol`, then `demo/04_security_warnings.pol`, Semantic step. | "Every line parses, but semantic analysis finds undefined names, actions a resource doesn't support, duplicates, redundant rules, conflicts, an ALLOW a DENY always beats, and unused names." | 3 |
| 4 | Generate | Gemini on. Task **d02**, Generate. | "Plain English in, a policy out in about a second. It compiled, and the verifier proved it equivalent to our hand-written reference, which the model never saw." | 4 |
| 5 | Generate | Task **d03**, Generate. Then "Compare with reference in Verify". | "Here the model got it wrong: it wrote `approved OR emergency AND NOT freeze` without brackets. AND binds tighter, so an approved change can deploy during a code freeze. It compiles fine; only the verifier catches it, with the exact request." | 4, 5 |
| 6 | Generate | Switch to **Fake**, task **d01**, Generate. Switch back to Gemini. | "The correction loop. The first answer uses FLY, the compiler's error goes back as structured JSON, the second attempt is fixed. Real Gemini rarely needs this, so the fake model shows it on demand." | 4 |
| 7 | Optimize | `demo/06_optimize_llm.pol` (default), Optimize. | "Our own optimizer can't shrink this policy at all: 7 rules stay 7. The LLM merges conditions with OR, simplifies `a AND b` plus `a AND NOT b` to `a`, and merges READ+WRITE into ACCESS: 7 to 5 rules, proven equivalent. It's accepted only because the verifier proved it." | 5 |
| 8 | Verify | Preset "Bank, unsafe" (default), Verify. Then "Bank, stricter" in both modes. | "A rewrite that drops `dual_control` from the vault rule is rejected: one manager could open the vault alone. The stricter one is rejected as 'equivalent' but proven 'non-weakening': it never allows more." | 5 |
| 9 | Results | Open the tab. | "The full evaluation on 20 tasks and 8 policies with the real model: 95% of generated policies proven correct, the LLM removed 25.9% of rules against 16.7% for our optimizer, 90.9% of seeded issues caught." | 6 |

To close: run `python -m policyc demo-check` in the terminal and show the checklist, or
`python -m unittest discover -s tests` (all tests pass).

## A3. If something goes wrong

| Problem | Fix |
|---|---|
| No internet or the key fails | Switch the header to **Fake**. Every tab still works offline; outputs are labelled FAKE. |
| "429" or slow answers | Free-tier limit (15 a minute). The client waits automatically; give it up to a minute, or switch to Fake. |
| Gemini button greyed out | The server started without `GEMINI_API_KEY`. Set it in that terminal and restart `python -m policyc gui`. |
| Port 8765 busy | `python -m policyc gui --port 8766` |
| Results tab says "no results" | Copy `evaluation/results/` from the machine that ran `evaluate`, or run `python -m policyc evaluate`. |
| Verdicts say "z3 not installed" | `pip install -r requirements.txt`. Everything still works with exact enumeration. |

## A4. Demo examples and what they prove

All in `demo/`. They are new scenarios, separate from the evaluation benchmark, so the
demo shows the compiler on policies it was not tuned on. `python -m policyc demo-check`
runs all of them and grades each outcome PASS / WARN / FAIL.

| File | Shows | Expected result |
|---|---|---|
| `01_full_language.pol` | Every keyword, ACCESS, AND/OR/NOT, parentheses (hospital) | Compiles, 0 errors, 0 warnings, 8 IR rules |
| `02_syntax_errors.pol` | DELETE as an action, missing resource, missing `)`, `;`, lowercase `allow`, empty condition | 6 errors (5 P001, 1 L001) at the marked lines, in one run |
| `03_semantic_errors.pol` | S001-S006 (school) | 6 errors at the marked lines |
| `04_security_warnings.pol` | W101, W102, W103, W104, W106 x2 | 6 warnings at the marked lines, no false alarms |
| `05_optimize_baseline.pol` | What the compiler-only optimizer removes (invoices) | 9 -> 6 IR rules, proven |
| `06_optimize_llm.pol` | What only a smarter rewrite can remove (deployments) | Compiler-only 7 -> 7; LLM 7 -> 5 rules, 7 -> 4 statements, proven |
| `06_optimize_llm.rewrite.pol` | A hand-written optimal answer (used by the fake model) | Proven equivalent to 06 |
| `07_verify_original.pol` + `_safe` / `_unsafe` / `_stricter` | The verifier on a bank policy | safe: PROVEN; unsafe: REJECTED (manager opens vault without dual control); stricter: REJECTED as equivalent, PROVEN non-weakening |
| `requirements.json` + `requirements/d01-d05.pol` | 5 English requirements (easy to hard) with reference policies | Real run: all 5 compile first try; 4 proven equivalent; d03 caught by the verifier (precedence bug) |

Every file's `# expect: CODE` comments are checked by `demo-check`; change a file and
the check tells you whether it still shows what it claims.

## A5. Review 1 outcomes: where each is shown

| Objective (Review 1) | Measured by | Evidence |
|---|---|---|
| 1. DSL + formal grammar | Complete, unambiguous grammar | Grammar in `policyc/parser.py` (LL(1)); `demo/01` compiles cleanly; demo-check objective 1 |
| 2. Lexer + recursive-descent parser + AST | Accepts valid, rejects invalid with line numbers and expected tokens | `demo/02`; Compile tab; demo-check objective 2 |
| 3. Security semantic analysis | Seeded duplicates and conflicts detected | `demo/03`, `demo/04`; evaluation: 20/22 seeded issues (2 documented known misses), 0 false alarms |
| 4. LLM English -> DSL with correction loop | % parse first try, after correction, average cycles | Generate tab; evaluation: 100% compile first try, 95% proven correct; demo-check objective 4 |
| 5. AI-suggested, compiler-verified optimization | % accepted vs rejected, rule reduction | Optimize + Verify tabs; evaluation: 7/10 accepted, 25.9% vs 16.7% reduction |
| 6. Target format + evaluation vs baseline | Validity, rule reduction, tokens, latency | Codegen step; Results tab; `evaluation/results/results.md` |
| Novelty: classify every AI output | invalid / syntactically valid / semantically valid / verified / improving | Results tab "tiers" chart; demo-check objective 6 |

---

# Part B: how the project works

## B1. The big picture

```
English ──► [LLM generator] ──► DSL text ─┐            (untrusted)
                                          ▼
.pol source ─► Lexer ─► Parser ─► Semantic ─► IR ─► Optimizer ─► Verifier ─► Codegen ─► JSON
               (tokens)  (AST)    (checks)   (flat)  (compiler   (Z3 proof)              (target
                                                ▲     or LLM)                              policy)
                                                └── LLM optimizer proposals ──┘          (untrusted)
```

Seven components, two of them untrusted (the LLMs) and five trusted (lexer/parser,
semantic analyzer, IR, verifier, code generator). Nothing the LLM produces reaches the
output without going through the trusted ones.

## B2. The language

```
SUBJECT doctor, nurse
RESOURCE patient_records SUPPORTS READ, WRITE
ALLOW doctor ACCESS patient_records WHEN on_shift AND (mfa OR hospital_network)
DENY nurse WRITE patient_records
```

- `SUBJECT` and `RESOURCE ... SUPPORTS` declare names, so the compiler can report
  undefined names and actions a resource does not support.
- Rules are `ALLOW` or `DENY` subject action resource, with an optional `WHEN` condition.
- Actions: READ, WRITE, EXECUTE, and ACCESS meaning "every action the resource supports".
- Conditions: attribute names with NOT (tightest), then AND, then OR, and parentheses.
- **Meaning (deny-overrides, default deny):** for a request, if any matching DENY's
  condition is true the answer is DENY; else if any matching ALLOW's condition is true,
  ALLOW; else DENY. This is the same idea as XACML and Cedar, defined once in `ir.decide()`.

Grammar (EBNF):

```
program       -> statement* EOF
statement     -> subject_decl | resource_decl | policy
subject_decl  -> SUBJECT IDENT ( ',' IDENT )*
resource_decl -> RESOURCE IDENT SUPPORTS action ( ',' action )*
policy        -> effect IDENT action IDENT ( WHEN or_expr )?
effect        -> ALLOW | DENY
action        -> READ | WRITE | EXECUTE | ACCESS
or_expr       -> and_expr ( OR and_expr )*
and_expr      -> not_expr ( AND not_expr )*
not_expr      -> NOT not_expr | primary
primary       -> IDENT | '(' or_expr ')'
```

Every statement starts with a different keyword, so one token of lookahead picks the
rule: the grammar is LL(1) and unambiguous. Precedence comes from the layering
or_expr > and_expr > not_expr.

## B3. The compiler phases (`policyc/`)

| Phase | File | What it does |
|---|---|---|
| 1. Lexer | `lexer.py`, `tokens.py` | Hand-written scanner: characters to tokens with line and column. Skips spaces and `#` comments. Keywords are found by reading a whole word and looking it up, so `allow` is an identifier. An illegal character is reported (L001), skipped, and scanning continues. |
| 2. Parser | `parser.py`, `ast_nodes.py` | Recursive descent, one method per grammar rule, producing an AST. Errors (P001) say what was expected and what was found. **Panic-mode recovery:** after an error it skips to the next statement keyword and carries on, so one run reports every syntax error. |
| 3. Semantic analysis | `semantic.py`, `logic.py` | Builds a symbol table from the declarations, then checks each rule (S001 undefined subject, S002 undefined resource, S003 unsupported action, S004/S005 declared twice, S006 ACCESS in SUPPORTS), then compares pairs of rules (W101 duplicate, W102 redundant, W103 conflict, W104 ALLOW that can never win, W106 unused, W107 too large to check). Conditions are compared by truth table. Errors stop compilation; warnings do not. |
| 4. IR | `ir.py` | A flat, normalised rule list: ACCESS expanded to one rule per action, each rule with an id and its source line, conditions with a canonical key. `decide()` here is the single definition of what a policy means. |
| 5a. Optimizer | `optimizer.py` | The compiler-only baseline: removes duplicates, ALLOWs an unconditional DENY always beats, and conditional rules an unconditional one covers. Never removes explicit DENYs. Its output is **not trusted**. |
| 5b. Verifier | `verifier.py` -> `verification/` | Decides whether two policies make the same decision for every request (translation validation, Necula 2000). Details in B4. |
| 6. Codegen | `codegen.py` | IR to a JSON policy document (format `policyc-target/1`): combining algorithm, default decision, declarations, every rule with its condition tree and source line. |
| Driver | `compiler.py`, `__main__.py`, `diagnostics.py` | Runs the phases in order; the CLI; one error object (code, line, column, expected tokens, hint) used by every phase and printed as JSON for the LLM. |

## B4. The verifier (`policyc/verification/`, Yohan's part)

The question: for every subject, action, resource and every true/false combination of
the attributes involved, do the two policies decide the same?

1. **Well-formedness:** both inputs must be valid rule lists.
2. **Differential test:** a fast random check that can only reject; it finds most bad
   rewrites in milliseconds.
3. **Z3 proof:** for each request it builds the formula
   `allow(P) = (any ALLOW condition) AND NOT (any DENY condition)` for both policies and
   asks Z3 whether they can ever differ. UNSAT = proven; SAT = a concrete counterexample,
   which is re-checked in plain Python before it is reported. Without Z3 it falls back
   to exact enumeration.

Two modes: **equivalent** (same decisions; used for optimization) and
**non-weakening** (the rewrite may deny more but never allows more). Anything that is
not a full proof (timeout, too many attributes, malformed input) is a rejection.

## B5. The AI stages (`policyc/ai/`)

| File | Role |
|---|---|
| `llm_client.py` | The only place a model is called. `GeminiClient` uses Google's REST API with the Python standard library, retries on rate limits (waiting as long as Google asks, up to 60 s), and never prints or stores the key. `FakeClient` replays scripted answers for tests and offline demos. |
| `prompts.py` | Every prompt: the grammar, the meaning rules, two worked examples, the allowed names. `PROMPT_VERSION` fingerprints them so results from different prompts are never mixed. |
| `generator.py` | **Objective 4.** Ask the model, compile the answer; if it has errors, send the JSON diagnostics back and ask again (up to 3 attempts). Warnings do not cause a retry. |
| `llm_optimizer.py` | **Objective 5.** The model proposes rewrites; each must (1) compile, (2) keep the declarations, (3) be proven by the verifier, (4) be smaller (IR rules, then statements, then condition size). Rejections go back as feedback, including counterexamples. `--samples N` asks for several proposals at once and keeps the best proven one. If nothing passes, the original is kept. |
| `metrics.py` | Policy size, the five-tier classification, the declaration and dropped-DENY checks. |
| `demo_check.py` | The `demo-check` command (Part A4). |
| `cli.py` | `generate`, `optimize`, `evaluate`, `demo-check`, `gui` commands. |

## B6. The evaluation (`policyc/ai/evaluation/`, data in `evaluation/`)

- **Data:** 20 English requirements with reference policies (6 easy, 8 medium, 6 hard);
  8 optimization policies (3 the baseline handles, 3 that need the LLM, 1 mixed, 1
  already optimal); 10 files with seeded semantic issues, including 2 documented cases
  pairwise checks cannot see.
- **Runner:** saves each finished task immediately and resumes after a quota stop; a
  failed model call is recorded as "not run", never as a model failure.
- **Metrics:** first-try and final compile rates, correction cycles, % proven equivalent
  to the reference, rule reduction (LLM vs compiler-only on the same policies),
  proposals accepted and rejected by stage, tokens and latency, detection rate, and the
  five tiers for every AI output.
- **Outputs:** `evaluation/results/` (git-ignored): `summary.txt`, `results.md`, CSVs,
  `run.json`, four PNG charts. Fake runs are stamped `FAKE - not real results`.

**Real results** (`gemini-3.5-flash-lite`): 20/20 compile first try, 19/20 proven
correct (g20 also locked out employees; the verifier caught it); LLM 54 -> 40 IR rules
(25.9%) vs compiler-only 54 -> 45 (16.7%); 7/10 proposals accepted (the 3 rejections are
on the already-optimal control); 20/22 seeded issues, 0 false alarms; about 1.1 s and
660 tokens per task.

## B7. The GUI (`policyc/gui/`)

`server.py` is a small web server on 127.0.0.1 only (standard library). It serves a
single page (`static/index.html`, `app.css`, `app.js`, no external libraries) and a JSON
API that calls the same functions as the CLI, so the GUI cannot disagree with it. The key
stays in the server process and is never sent to the browser.

## B8. Tests

`python -m unittest discover -s tests` runs 234 tests in under a minute, with no
network: every compiler phase, the verifier (Z3 against brute force on 400 random
policy pairs), the Gemini client against a local fake server, the generator and
optimizer loops, the evaluation, the GUI API, and demo-check.

## B9. Questions you may get

**Why hand-write the lexer and parser?** The grammar is small and LL(1), and we control
the error messages completely; structured errors are what the correction loop feeds back.

**Why is the LLM not trusted?** Its output is probabilistic. Demo step 5 is a real case:
a policy that compiles, looks right, and lets production deploy during a code freeze.
Only the verifier catches that.

**How do you know the verifier is right?** Z3 encodes deny-overrides exactly; every
counterexample is replayed in plain Python; Z3 and enumeration are cross-checked against
brute force on 400 random policy pairs.

**Why are conflicts warnings, not errors?** Under deny-overrides a conflicting policy
still has one meaning (DENY wins), and a broad ALLOW plus a narrow DENY is a common
deliberate pattern.

**Why keep explicit DENY rules?** They are guard rails: if someone later adds a broader
ALLOW, the DENY still blocks it.

**What are the limits?** The verifier proves the IR, not the JSON; attributes are
treated as independent booleans; semantic checks are pairwise (two documented misses);
real numbers depend on the model and its free-tier quota.

## B10. Repository map

```
policyc/            compiler (lexer ... codegen), verifier wrapper, CLI
policyc/verification/  Z3 / enumeration / differential verifier
policyc/ai/         LLM client, prompts, generator, optimizer, metrics, demo-check, CLI
policyc/ai/evaluation/ benchmark loaders, runner, reports, charts
policyc/gui/        demo web app
demo/               demo examples (this guide, A4)
evaluation/         benchmark data; results/ is git-ignored
examples/           the original sample policies
tests/              unit and integration tests
docs/               this guide, EVALUATION.md, VERIFICATION.md, design specs and plans
```
