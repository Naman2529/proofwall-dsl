# policyc: AI-Assisted Cybersecurity Policy DSL Compiler (Review 2 build)

BCSE307P Compiler Design Lab. Team: Naman Bhatia (24BDS0056), George Christian Mathew (24BCE2753), Yohan George (24BAI0356).

This is the trusted half of the compiler: every stage that decides whether a policy is accepted. The two AI stages (LLM generator and LLM optimizer) plug into interfaces that already exist here, so the remaining work is mostly wiring an LLM into them.

Pure Python 3.8+, no external libraries.

---

## 1. Quick start

```bash
python -m policyc compile examples/valid.pol --emit all          # every phase, start to finish
python -m policyc compile examples/syntax_errors.pol             # lexer + parser errors
python -m policyc compile examples/semantic_issues.pol           # semantic errors + security warnings
python -m policyc compile examples/optimizable.pol --emit ir --optimize
python -m policyc verify examples/optimizable.pol examples/candidate_good.pol   # accepted
python -m policyc verify examples/optimizable.pol examples/candidate_bad.pol    # rejected
python -m unittest discover -s tests -v                          # 40 tests
```

Other flags: `--emit tokens|ast|ir|json|all`, `-o out.json` to save the target policy, `--json-diagnostics` to print errors as JSON.

---

## 2. How much is done

| Objective (from Review 1) | Status |
|---|---|
| 1. DSL + formal grammar | Done. Grammar is LL(1) with no ambiguous productions. |
| 2. Lexer + recursive-descent parser + AST, with line numbers and expected tokens | Done, plus panic-mode error recovery. |
| 3. Security semantic analysis (duplicates, conflicts, undefined names, invalid action/resource pairs) | Done, plus redundancy, shadowing and unused-declaration checks. |
| 4. LLM English to DSL with correction loop | Not started. The structured diagnostics it needs are done (`--json-diagnostics`). |
| 5. IR + AI-suggested, compiler-verified optimization | IR, verifier and a compiler-only optimizer are done. The LLM that proposes rewrites is not started. |
| 6. Target format + evaluation vs baseline | JSON code generator done. The compiler-only baseline exists. Evaluation not started. |

So the review requirement of 25% is comfortably passed. Of the seven components in our "division of responsibility" table, the five trusted ones are working.

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

**The verifier** answers: do these two IRs make the same decision for every possible request? The DSL is small, so the set of possible requests is finite: every subject, every resource, every action it supports, and every true/false combination of the attributes used in that request's rules. The verifier checks all of them. If any decision differs, it rejects and prints a concrete counterexample.

The key design point: **the optimizer's output is not trusted.** `compile()` runs the verifier on (original, optimized) and only keeps the optimized version if it is proven equivalent. This is called translation validation (Necula, PLDI 2000, paper 12 in our survey). Later, the LLM optimizer's suggestions go through the exact same gate. The LLM proposes, the verifier decides.

Demo:

```bash
python -m policyc compile examples/optimizable.pol --emit ir --optimize
# 9 IR rules -> 6, "VERIFIED: same decision in all 14 cases checked"

python -m policyc verify examples/optimizable.pol examples/candidate_bad.pol
# REJECTED: analyst READ reports (authenticated=False): original says DENY, candidate says ALLOW
```

`candidate_bad.pol` is the story to tell: it looks like a harmless cleanup, but it dropped `WHEN authenticated`, which quietly lets unauthenticated analysts read reports. This is exactly the kind of subtle, unsafe change an LLM could make, and the verifier catches it with a specific example.

Why 14 cases? 3 subjects x 4 (resource, action) pairs = 12 requests. Two of them have one attribute each (mfa, authenticated), so those two are checked twice (true and false). 10 + 4 = 14.

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

---

## 5. Suggested demo order (about 5 minutes)

1. `--emit all` on `valid.pol`. Scroll through tokens, AST, IR, JSON. "This is one policy going through every phase."
2. `syntax_errors.pol`. Point at FLY, the missing resource, the missing `)`, the `;`, and the lowercase `allow` hint. "Five errors, one run, because of panic-mode recovery."
3. `semantic_issues.pol`. "All of these lines parse. Semantic analysis catches the typo, the impossible action, the duplicate, the dead ALLOW, the conflict."
4. `optimizable.pol --emit ir --optimize`. "9 rules down to 6, and the compiler proves it did not change any decision."
5. `verify ... candidate_bad.pol`. "This is what will happen to a bad LLM suggestion. Rejected, with the exact request that would have been wrongly allowed."
6. Run the tests: 40 passing.

---

## 6. Questions you might get

**Why hand-write the lexer and parser instead of using Lex/Yacc or ANTLR?**
The grammar is small and LL(1), so a hand-written recursive-descent parser is short and easy to follow. More importantly, we control the error messages completely, and good structured error messages are what the LLM correction loop depends on.

**Is the grammar ambiguous?**
No. Every statement starts with a unique keyword, and the condition grammar encodes precedence through separate levels (or_expr, and_expr, not_expr), so each input has exactly one parse tree.

**Why are conflicts warnings and not errors?**
Under deny-overrides a conflicting policy still has one clear meaning: DENY wins. Real policies often use a broad ALLOW plus a narrow DENY on purpose. We flag it so a human can check, but we do not block it. Undefined names and impossible actions are errors because the policy is meaningless with them.

**How do you know the verifier is correct?**
It does not approximate. The space of requests is finite, and it checks every single one using the same `decide()` function that defines the policy's meaning. If it says equivalent, there is no request where they differ. If it says not equivalent, it shows you one. The only limit is size, which is why it only enumerates attributes that actually appear in the rules for each request (capped at 16 per request).

**Does it scale?**
Cost grows with (subjects x resource-actions x 2^attributes-per-request). For the policy sizes in this project that is tiny (14 cases above, milliseconds). For very large policies the next step would be an SMT solver, like Zelkova and Cedar do (papers 16 and 18).

**Why does the optimizer keep explicit DENY rules?**
With default deny, a DENY with no overlapping ALLOW technically changes nothing. But it is a guard rail: if someone later adds `ALLOW guest ACCESS reports`, the DENY still blocks it. Removing it would be "correct" today and dangerous tomorrow.

**Where does the AI go?**
Two places, both already prepared for. (1) Generator: the LLM turns English into DSL, we compile it, and if it fails we send the JSON diagnostics back and ask it to fix it. (2) Optimizer: the LLM proposes a rewritten policy, and we run exactly the `verify` command shown above. In both cases the LLM never decides anything on its own.

---

## 7. What is left (for the final review)

The hooks for all three items already exist in `policyc/ai/`. Start with `policyc/ai/README.md`, which says what each file does today and what still needs doing.

- LLM generator: English to DSL, with the compile, feedback, retry loop. Metrics: first-try parse rate, rate after correction, average retries.
- LLM optimizer: send the IR, get a candidate policy back, verify it. Metrics: accepted vs rejected suggestions, rule reduction.
- Evaluation script comparing the AI pipeline against the compiler-only optimizer that already exists here (validity, rule reduction, tokens, latency).

---

## 8. Repository layout and who owns what

We split the work three ways: the compiler, the AI part and the security verification part.

```
policyc/
  tokens.py lexer.py parser.py ast_nodes.py      compiler: phases 1-2
  semantic.py logic.py                           compiler: phase 3
  ir.py optimizer.py codegen.py                  compiler: phases 4, 5a, 6
  compiler.py __main__.py diagnostics.py         compiler: driver, CLI, errors
  verifier.py                                    security verification (working baseline, see note at top of file)
  ai/                                            AI part (hooks and loops ready, no model connected yet)
    README.md          start here
    llm_client.py      the one place a model gets called (stub)
    generator.py       English -> DSL correction loop
    llm_optimizer.py   propose -> verify -> retry loop
    evaluation.py      baseline numbers done, AI comparison not started
examples/              the .pol files used in the demo
tests/                 40 unittest tests
```

Ground rules for anyone adding to this:

- Every model output goes through `compile_source()`, and every proposed rewrite also goes through `verify()`. Nothing skips those two.
- If you change a diagnostic code or the shape of `Diagnostic.to_dict()`, update the table in section 4, because the LLM correction loop reads those fields.
- If you change `verify()` or `VerificationResult`, keep the field names. The compiler, the CLI and `ai/llm_optimizer.py` all use them.
