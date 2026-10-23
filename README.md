# policyc: AI-Assisted Cybersecurity Policy DSL Compiler

BCSE307P Compiler Design Lab. Team: Naman Bhatia (24BDS0056), George Christian Mathew (24BCE2753), Yohan George (24BAI0356).

A compiler for a small access-control policy language, with two AI stages inside it: an LLM that turns English requirements into policies, and an LLM that proposes smaller policies. The LLM proposes, the compiler decides: every model output must compile, and every rewrite must be proven by the verifier, before it is accepted.

Pure Python 3.8+. The compiler and the AI stages use only the standard library. Optional: `pip install -r requirements.txt` for the Z3 verifier, `matplotlib` for evaluation charts, and a `GEMINI_API_KEY` for the real model (everything also runs with `--llm fake`).

---

## 1. Quick start

```bash
python -m policyc compile examples/valid.pol --emit all          # every phase, start to finish
python -m policyc compile examples/syntax_errors.pol             # lexer + parser errors
python -m policyc compile examples/semantic_issues.pol           # semantic errors + security warnings
python -m policyc compile examples/optimizable.pol --emit ir --optimize
python -m policyc verify examples/optimizable.pol examples/candidate_good.pol   # accepted
python -m policyc verify examples/optimizable.pol examples/candidate_bad.pol    # rejected
python -m policyc.verification verify examples/optimizable.pol examples/candidate_bad.pol   # Z3 verifier, see docs/VERIFICATION.md
python -m policyc gui                                           # the demo GUI in your browser (localhost only)
python -m policyc generate --task g16 --llm fake                # English -> DSL (drop --llm fake with a key)
python -m policyc optimize evaluation/optimization/o4_or_merge.pol --llm fake
python -m policyc evaluate --llm fake                            # every Review 1 metric, see docs/EVALUATION.md
python -m unittest discover -s tests -v                          # 222 tests (190 without z3)
```

Other flags: `--emit tokens|ast|ir|json|all`, `-o out.json` to save the target policy, `--json-diagnostics` to print errors as JSON.

---

## 2. How much is done

| Objective (from Review 1) | Status |
|---|---|
| 1. DSL + formal grammar | Done. Grammar is LL(1) with no ambiguous productions. |
| 2. Lexer + recursive-descent parser + AST, with line numbers and expected tokens | Done, plus panic-mode error recovery. |
| 3. Security semantic analysis (duplicates, conflicts, undefined names, invalid action/resource pairs) | Done, plus redundancy, shadowing and unused-declaration checks. Measured on 10 seeded policies (`evaluate --suite semantic`). |
| 4. LLM English to DSL with correction loop | Done (`policyc/ai/generator.py`, `generate`). Gemini client, compiler errors fed back as JSON, measured on 20 tasks against reference policies. |
| 5. IR + AI-suggested, compiler-verified optimization | Done (`policyc/ai/llm_optimizer.py`, `optimize`). Proposals must compile, keep declarations, be proven by the verifier and be smaller; counterexamples go back to the model. |
| 6. Target format + evaluation vs baseline | Done (`evaluate`). Validity, rule reduction, tokens and latency vs the compiler-only baseline, plus the five-tier classification of every AI output. Real numbers need one run with a `GEMINI_API_KEY`. |

All seven components of the Review 1 "division of responsibility" table are implemented: the two untrusted LLM stages and the five trusted compiler stages that judge them.

---

## 3. The language

```
SUBJECT admin, guest, analyst, external

RESOURCE database     SUPPORTS READ, WRITE
RESOURCE reports      SUPPORTS READ, WRITE
RESOURCE internal_api SUPPORTS READ, EXECUTE

ALLOW admin READ database
DENY guest WRITE database
ALLOW analyst READ reports WHEN authenticated AND NOT on_public_wifi
DENY external ACCESS internal_api
```

Things we added on top of the Review 1 draft, and why:

- **SUBJECT and RESOURCE declarations.** Objective 3 says "detect undefined subjects or resources". You can only call something undefined if there is a list of what is defined. Same idea as declaring variables before using them.
- **SUPPORTS.** Objective 3 also says "invalid action-resource pairs". SUPPORTS is how a resource says which actions make sense for it. `WRITE` on a read-only `reports` resource is now a compile error.
- **ACCESS means "every action the resource supports".** It is a wildcard, so `DENY external ACCESS internal_api` blocks both READ and EXECUTE.
- **Conditions are boolean expressions** over attribute names: `AND`, `OR`, `NOT`, parentheses.
- `#` starts a comment. Keywords are UPPERCASE and case-sensitive.

### Grammar (EBNF)

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

### What a policy means (semantics)

For a request "can subject S do action A on resource R, given these attribute values":

1. If any matching DENY rule has a true condition, the answer is **DENY**.
2. Otherwise, if any matching ALLOW rule has a true condition, the answer is **ALLOW**.
3. Otherwise the answer is **DENY**.

This is "deny-overrides with default deny", the same idea XACML and Cedar use. It is defined in one place, `ir.decide()`, and everything that reasons about meaning (verifier, optimizer) uses that one function.

---

## 4. The pipeline, file by file

```
.pol source
   |  lexer.py        phase 1   characters -> tokens
   |  parser.py       phase 2   tokens -> AST
   |  semantic.py     phase 3   AST + symbol table -> errors / warnings
   |  ir.py           phase 4   AST -> IR (flat, normalised rules)
   |  optimizer.py    phase 5a  IR -> smaller IR (candidate)
   |  verifier.py     phase 5b  is the candidate equivalent? accept or reject
   |  codegen.py      phase 6   IR -> JSON target policy
   v
target JSON
```

`compiler.py` runs these in order. `__main__.py` is the command-line interface. `diagnostics.py` defines the error object every phase uses. `logic.py` holds the small boolean helpers (evaluate a condition, canonical form, implication) that semantic analysis, the IR and the verifier share.

### Phase 1: Lexer (`lexer.py`, `tokens.py`)

A hand-written scanner. It walks the text one character at a time and groups characters into tokens: keywords (ALLOW, READ, WHEN...), identifiers (admin, database), and punctuation (`,` `(` `)`). It skips spaces, newlines and `#` comments.

Points worth saying in the presentation:

- It tracks **line and column** for every token, which is why every error later can point at an exact position.
- Keywords are recognised by reading a whole word and then looking it up in a keyword table. If it is not in the table it is an identifier. That is the standard trick, and it means `allow` (lowercase) is an identifier, not a keyword.
- On an illegal character like `;` it reports **L001**, skips that character and keeps scanning, so one typo does not hide the rest of the errors.

Try: `python -m policyc compile examples/valid.pol --emit tokens`

### Phase 2: Parser (`parser.py`, `ast_nodes.py`)

A **recursive-descent parser**: one method per grammar rule (`_policy`, `_resource_decl`, `_or_expr` and so on). Each method consumes tokens and returns an AST node.

Points worth saying:

- **Why recursive descent works:** every statement begins with a different keyword, so the parser can choose the right rule by looking at one token. The grammar is LL(1). No backtracking needed.
- **Operator precedence** (NOT binds tightest, then AND, then OR) comes from the layering `or_expr -> and_expr -> not_expr -> primary`. `x OR y AND NOT z` parses as `x OR (y AND (NOT z))`. Parentheses override it.
- **The output is an AST**, a tree of objects (Policy, Ident, And, Or, Not, Attr), not just a yes/no.
- **Error messages** say what was expected and what was found: `ALLOW admin FLY database` gives "expected an action but found identifier 'FLY'", line 1 col 13, expected READ, WRITE, EXECUTE, ACCESS. That is exactly the measurable outcome from Objective 2.
- **Panic-mode error recovery:** after an error, the parser skips tokens until it sees a keyword that starts a new statement (ALLOW, DENY, SUBJECT, RESOURCE) and carries on. That is why `syntax_errors.pol` reports five separate errors in one run.
- **Friendly hints:** lowercase keyword gets "did you mean ALLOW?". If a line ends early, the error points at the end of that line, not the start of the next one.

Try: `python -m policyc compile examples/valid.pol --emit ast` and `python -m policyc compile examples/syntax_errors.pol`

### Phase 3: Semantic analysis (`semantic.py`)

The parser only checks shape. `ALLOW admni WRITE database` is grammatically fine, it is just wrong. This phase checks meaning, in four passes:

1. **Build the symbol table** from the declarations (like a compiler recording declared variables). Catches names declared twice and `ACCESS` used inside SUPPORTS.
2. **Check each policy against the symbol table.** Undefined subject (S001), undefined resource (S002), action the resource does not support (S003). Undefined names get a "did you mean 'admin'?" suggestion using string similarity.
3. **Security checks between pairs of rules** that share a subject and resource and have overlapping actions:
   - **W101 duplicate:** identical rule. Condition order is ignored, so `a AND b` equals `b AND a` (we compare a canonical form of the condition).
   - **W102 redundant:** one rule already covers another. For example `ALLOW admin ACCESS database` already covers `ALLOW admin READ database`.
   - **W103 conflict:** the same request is allowed by one rule and denied by another under some conditions.
   - **W104 shadowed ALLOW:** an unconditional DENY always beats this ALLOW, so the ALLOW is dead code.
4. **W106 unused declarations**, like an unused variable warning.

Errors stop compilation. Warnings do not, because the policy still has one well-defined meaning under deny-overrides.

Try: `python -m policyc compile examples/semantic_issues.pol`

### Phase 4: Intermediate representation (`ir.py`)

The AST matches what the user typed. The IR is a flatter form that is easier to analyse:

- ACCESS is **expanded** into one rule per real action (this is desugaring). Every IR rule is about exactly one (subject, action, resource).
- Each rule gets an id (R1, R2...) and keeps its **source line**, so any decision can be traced back to a line of DSL.
- Conditions get a canonical key so equal conditions compare equal.

This is also where `decide()` lives, the single definition of what a policy means.

Try: `python -m policyc compile examples/valid.pol --emit ir`

### Phase 5: Optimizer + verifier (`optimizer.py`, `verifier.py`)

This is the core idea of the project, already working without the AI.

**The optimizer** (compiler-only baseline) removes rules that cannot change any decision: exact duplicates, ALLOWs that an unconditional DENY always beats, and conditional rules that an unconditional rule of the same effect already covers. It never removes explicit DENY rules, on purpose, because they protect you if someone adds a broader ALLOW later.

**The verifier** answers: do these two IRs make the same decision for every possible request? `verifier.verify()` hands the question to `policyc/verification/` (see `docs/VERIFICATION.md`): a cheap randomized differential test first, then a Z3 proof per (subject, action, resource), or exact enumeration of every true/false combination of the attributes if `z3-solver` is not installed. If any decision differs, it rejects and prints a concrete counterexample. Anything short of a full proof (timeout, enumeration cap) is also a rejection. `--mode non-weakening` relaxes "same decisions" to "never allows more".

The key design point: **the optimizer's output is not trusted.** `compile()` runs the verifier on (original, optimized) and only keeps the optimized version if it is proven equivalent. This is called translation validation (Necula, PLDI 2000, paper 12 in our survey). Later, the LLM optimizer's suggestions go through the exact same gate. The LLM proposes, the verifier decides.

Demo:

```bash
python -m policyc compile examples/optimizable.pol --emit ir --optimize
# 9 IR rules -> 6, "PROVEN (equivalent, z3 (after differential)): 6 requests, ..."

python -m policyc verify examples/optimizable.pol examples/candidate_bad.pol
# REJECTED (equivalent, differential): analyst READ reports (authenticated=False): original says DENY, candidate says ALLOW

python -m policyc verify examples/optimizable.pol examples/candidate_stricter.pol --mode non-weakening
# PROVEN: stricter is allowed in this mode, rejected in the default one
```

`candidate_bad.pol` is the story to tell: it looks like a harmless cleanup, but it dropped `WHEN authenticated`, which quietly lets unauthenticated analysts read reports. This is exactly the kind of subtle, unsafe change an LLM could make, and the verifier catches it with a specific example.

Why 6 requests? Only (subject, action, resource) triples that some rule mentions can differ; every other request is DENY in both policies by default. Requests whose rule sets are identical in both policies are skipped too, since they provably decide the same.

### Phase 6: Code generation (`codegen.py`)

Turns the IR into a simplified JSON policy document, in the spirit of XACML/Cedar but much smaller. It records the combining algorithm, the default decision, the declarations and every rule with its condition tree and source line.

Try: `python -m policyc compile examples/valid.pol` (default output is the JSON).

### Diagnostics (`diagnostics.py`)

Every error and warning is an object with a code, phase, message, line, column, expected tokens and a hint. With `--json-diagnostics` they print as JSON. This is deliberate: in the next stage these JSON objects are what gets sent back to the LLM in the correction loop ("line 1, col 13, expected one of READ/WRITE/EXECUTE/ACCESS, found FLY").

| Code | Phase | Meaning |
|---|---|---|
| L001 | lexer | illegal character |
| P001 | parser | unexpected or missing token |
| S001 | semantic | undefined subject |
| S002 | semantic | undefined resource |
| S003 | semantic | resource does not support that action |
| S004 / S005 | semantic | subject / resource declared twice |
| S006 | semantic | ACCESS listed inside SUPPORTS |
| W101 | semantic | duplicate rule |
| W102 | semantic | redundant rule |
| W103 | semantic | ALLOW/DENY conflict |
| W104 | semantic | ALLOW that can never take effect |
| W106 | semantic | declared but never used |
| W107 | semantic | pair of rules too large (over 16 attributes) to check for W102/W103 |

---

## 5. Suggested demo order (about 6 minutes, in the GUI)

Before the demo: `pip install -r requirements.txt matplotlib`, set `GEMINI_API_KEY`, run `python -m policyc evaluate` once (for the Results tab), then `python -m policyc gui`. The header toggle switches between Gemini and the offline Fake model.

1. **Compile** `examples/valid.pol`. Click through Lexer, Parser, Semantic, IR, Optimizer, Verifier, Codegen. "One policy through every phase."
2. **Compile** `examples/syntax_errors.pol`. Five errors in one run (panic-mode recovery); click one to jump to its line.
3. **Compile** `examples/semantic_issues.pol`. Everything parses; semantic analysis catches the typo, the impossible action, the duplicate, the dead ALLOW, the conflict.
4. **Compile** `examples/optimizable.pol`, Verifier step. "9 rules down to 6, and the compiler proves no decision changed."
5. **Generate** task g16 with Gemini: the policy, proven equivalent to the hand-written reference, in about a second. Then switch to **Fake** and generate g01 to show the correction loop: a broken first answer, the compiler's errors fed back, the fix.
6. **Optimize** `o4_or_merge.pol` with Gemini: compiler-only 5 -> 5 rules, LLM 5 -> 3, proven equivalent, with the diff.
7. **Verify**, preset "Dropped a condition": REJECTED, with the exact request that would have been wrongly allowed. "This is what happens to an unsafe AI suggestion."
8. **Results**: the Review 1 numbers and charts from the real run.
9. Run the tests: 222 passing.

The same steps work from the command line (`compile`, `verify`, `generate`, `optimize`, `evaluate`).

---

## 6. Questions you might get

**Why hand-write the lexer and parser instead of using Lex/Yacc or ANTLR?**
The grammar is small and LL(1), so a hand-written recursive-descent parser is short and easy to follow. More importantly, we control the error messages completely, and good structured error messages are what the LLM correction loop depends on.

**Is the grammar ambiguous?**
No. Every statement starts with a unique keyword, and the condition grammar encodes precedence through separate levels (or_expr, and_expr, not_expr), so each input has exactly one parse tree.

**Why are conflicts warnings and not errors?**
Under deny-overrides a conflicting policy still has one clear meaning: DENY wins. Real policies often use a broad ALLOW plus a narrow DENY on purpose. We flag it so a human can check, but we do not block it. Undefined names and impossible actions are errors because the policy is meaningless with them.

**How do you know the verifier is correct?**
It does not approximate. The space of requests is finite, and Z3 encodes deny-overrides exactly for each one; every counterexample Z3 finds is replayed through plain Python before it is reported. Z3 and exact enumeration are cross-checked against an independent brute force on 400 random policy pairs, and `tests/test_integration.py` checks the verifier reads the compiler's IR the same way as the source. If it says proven, there is no request where they differ. If not, it shows you one, or says why it could not finish (and rejects).

**Does it scale?**
Enumeration costs 2^attributes per request: 1 ms at 4 attributes, 6.2 s at 16, then it refuses. Z3 stays around 25 to 45 ms up to 48 attributes (`benchmarks/bench_verifiers.py`), the same approach as Zelkova and Cedar (papers 16 and 18).

**Why does the optimizer keep explicit DENY rules?**
With default deny, a DENY with no overlapping ALLOW technically changes nothing. But it is a guard rail: if someone later adds `ALLOW guest ACCESS reports`, the DENY still blocks it. Removing it would be "correct" today and dangerous tomorrow.

**Where does the AI go?**
Two places. (1) Generator: the LLM turns English into DSL, we compile it, and if it fails we send the JSON diagnostics back and ask it to fix it. (2) Optimizer: the LLM proposes rewritten policies, and each one must compile, keep the declarations, be proven by the verifier and be smaller. In both cases the LLM never decides anything on its own.

**How do you know a generated policy means what was asked?**
Each benchmark requirement has a hand-written reference policy. The verifier checks whether the generated policy is equivalent to it, so "compiles" and "correct" are measured separately.

---

## 7. Limitations

- The real evaluation numbers need a Gemini API key; until then `--llm fake` produces rehearsal output marked `FAKE - not real results`.
- The verifier proves the IR, not the JSON codegen, and treats attributes as independent booleans (see `docs/VERIFICATION.md`).
- Semantic checks are pairwise: an ALLOW beaten only by two DENYs together, or a rule covered only by two others together, is not flagged. The evaluation keeps both as documented known misses.
- Free-tier quotas are small; the evaluation resumes where it stopped (`docs/EVALUATION.md`).

---

## 8. Repository layout and who owns what

We split the work three ways: the compiler, the AI part and the security verification part.

```
policyc/
  tokens.py lexer.py parser.py ast_nodes.py      compiler: phases 1-2
  semantic.py logic.py                           compiler: phase 3
  ir.py optimizer.py codegen.py                  compiler: phases 4, 5a, 6
  compiler.py __main__.py diagnostics.py         compiler: driver, CLI, errors
  verifier.py                                    verify(): the entry point compiler, CLI and AI call
  verification/                                  security verification: Z3 non-weakening prover (docs/VERIFICATION.md)
  ai/                                            AI part (see policyc/ai/README.md)
    llm_client.py      the one place a model gets called (Gemini REST, fake)
    prompts.py         every prompt template, fingerprinted
    generator.py       English -> DSL correction loop
    llm_optimizer.py   propose -> compile -> verify -> select loop
    metrics.py         policy size, output tiers, declaration and DENY-guard checks
    cli.py             generate / optimize / evaluate / gui commands
  gui/                                           demo web app: server.py (JSON API, localhost only) + static/
    evaluation/        benchmark loaders, resumable runner, reports, charts
examples/              the .pol files used in the demo
evaluation/            benchmark data (generation, optimization, semantic); results/ is git-ignored
tests/                 222 unittest tests (test_verification.py is skipped without z3-solver)
benchmarks/            Z3 vs enumeration timing
docs/                  VERIFICATION.md, EVALUATION.md, superpowers/ (design spec and plan)
```

Ground rules for anyone adding to this:

- Every model output goes through `compile_source()`, and every proposed rewrite also goes through `verify()`. Nothing skips those two.
- If you change a diagnostic code or the shape of `Diagnostic.to_dict()`, update the table in section 4, because the LLM correction loop reads those fields.
- If you change `verify()` or `VerificationResult`, keep the field names. The compiler, the CLI and `ai/llm_optimizer.py` all use them.
- If you change a prompt in `policyc/ai/prompts.py`, its fingerprint changes and the evaluation starts fresh records for it; that is intended.
- Never put an API key in a file. `GEMINI_API_KEY` is read from the environment only.
