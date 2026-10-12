# Security verification (Yohan's part)

The guarantee this part enforces: **an optimized policy never allows a request the original policy would have denied.** The LLM proposes, this decides. Anything that is not a full proof is rejected (timeouts, malformed input and enumeration caps all fail closed).

## What was added

| File | Role |
|---|---|
| `policyc/verification/z3_backend.py` | The proof. Encodes deny-overrides as a Z3 formula per request and asks if the candidate can allow something the original denies. UNSAT = proven, SAT = counterexample. |
| `policyc/verification/enumerate_backend.py` | Exact brute-force baseline (2^attrs per request, capped at 16). Used to cross-check Z3 and as the benchmark baseline. |
| `policyc/verification/differential.py` | Fast reject layer: runs both policies on many generated inputs. Can reject, can never prove. |
| `policyc/verification/layered.py` | `verify_candidate()`: well-formedness, then differential test, then Z3. **The one function the optimizer loop calls.** |
| `policyc/verification/model.py` | Reads the compiler's IR (or JSON/AST conditions) into a neutral rule list; defines the reference `decide()`. |
| `policyc/verification/result.py` | `VerificationResult` / `Counterexample`, including `.feedback()` text for the LLM retry prompt and `.to_dict()` for JSON. |
| `policyc/verification/loader.py` | Small standalone `.pol` reader so the verifier runs and tests without the rest of the compiler. |
| `policyc/verification/__main__.py` | CLI: `python -m policyc.verification verify A.pol B.pol` |
| `tests/test_verification.py` | 33 tests, including Z3 vs enumeration vs independent brute force on 400 random policy pairs. |
| `benchmarks/bench_verifiers.py` | Z3 vs enumeration timing; writes `benchmarks/results/bench_results.csv` and `bench_chart.png`. |
| `examples/candidate_drops_deny.pol`, `candidate_stricter.pol` | Two new demo candidates (see below). |

## Run it

```
pip install -r requirements.txt
python -m unittest discover -s tests -v                       # 33 tests
python -m policyc.verification verify examples/optimizable.pol examples/candidate_good.pol          # PROVEN
python -m policyc.verification verify examples/optimizable.pol examples/candidate_bad.pol           # REJECTED + counterexample
python -m policyc.verification verify examples/optimizable.pol examples/candidate_drops_deny.pol    # REJECTED: deleted DENY guard
python -m policyc.verification verify examples/optimizable.pol examples/candidate_stricter.pol      # PROVEN (non-weakening)
python -m policyc.verification verify examples/optimizable.pol examples/candidate_stricter.pol --mode equivalent   # REJECTED (admin lost access)
python benchmarks/bench_verifiers.py                          # ~15 s
```

Flags: `--backend layered|z3|enumerate|differential`, `--mode non-weakening|equivalent`, `--all` (every counterexample), `--json`, `--timeout-ms`.

## Two modes, on purpose

- `non-weakening` (default) is the ProofWall guarantee: the candidate may deny *more*, never allow more.
- `equivalent` demands identical decisions. That is what the existing compiler gate checks today.

`candidate_stricter.pol` shows the difference: it passes the first and fails the second.

## Plugging it into the rest of the compiler

1. **LLM optimizer loop** (`policyc/ai/llm_optimizer.py`):
   ```python
   from policyc.verification import verify_candidate
   res = verify_candidate(original_ir, proposed_ir)       # mode="non-weakening"
   if res.proven:
       accept(proposed_ir)
   else:
       prompt += res.feedback()                           # counterexample text for the retry
   ```
2. **Compiler gate** (`compiler.py`): replace the call to `verifier.verify(ir, optimized)` with
   `verify_candidate(ir, optimized, mode="equivalent")`. `.equivalent` and `.counterexample` still work; `str(res.counterexample)` has the same format the existing tests assert.
3. **CLI** (`__main__.py` `verify` subcommand): call `verify_candidate(a_ir, b_ir, backend=args.backend)` and print `res.summary()`; exit 0 only if `res.proven`.

`normalize()` reads the IR by duck typing (`ir.rules`, and per rule `id/effect/subject/action/resource/condition/line`). Conditions are matched by class name (`Attr`/`Ident`, `And`, `Or`, `Not`) or by the codegen JSON shape. If the real AST classes differ, pass `cond_adapter=` (a 3-line function) instead of editing the verifier. **I wrote this against the interface the existing tests use; the compiler modules themselves are not in the repo yet, so run the tests once they are merged.**

## What to say in the review

- Why Z3: brute force is 2^k per request. The benchmark shows enumeration going from 1 ms (k=4) to 6.2 s (k=16) and then refusing, while Z3 stays at roughly 25 to 45 ms up to k=48. (Z3 is actually slower for tiny policies, about 25 ms of fixed cost against 1 ms; the win is scaling.)
- Why you can trust the verifier: Z3 and enumeration are cross-checked against an independent brute force on 400 random policy pairs in both modes; every Z3 counterexample is replayed through plain Python before it is reported; deliberately breaking the encoding makes 6 tests fail.
- The story: `candidate_bad.pol` and `candidate_drops_deny.pol` both look like harmless cleanups, and both are caught with a concrete request.

## Honest limits

- Proves the **IR**, not codegen (IR to target JSON is tested, not proven). Same limitation the README states.
- Attributes are treated as independent booleans. If two attributes can never both be true in reality (e.g. `on_vpn` and `on_public_wifi`), the verifier may flag a difference on an input that cannot occur. That errs on the safe side.
- Only requests that appear in some rule are enumerated; any request with no rules in either policy is DENY in both, so nothing is missed.
- `cases_checked` is not comparable between backends: Z3 counts solver queries, enumeration counts attribute assignments. Requests whose rule sets are identical are skipped (provably the same decisions).
- Z3 is part of the trusted base.

## Repo cleanup before you commit

The repo root has files that belong in folders (the tests import `helpers`, which expects to live in `tests/` with the `.pol` files in `examples/`):

Do this after unzipping this package (it already puts identical copies of the `.pol` files in `examples/`, so the root copies just get removed). Tell the team first, since it touches their files:

```
git mv test_cli_ai.py test_ir.py test_lexer.py test_optimizer_verifier.py test_parser.py test_semantic.py helpers.py tests/
git rm candidate_bad.pol candidate_good.pol optimizable.pol semantic_issues.pol syntax_errors.pol valid.pol
git commit -m "Move tests into tests/ and sample policies into examples/"
```

Suggested branch and commits:

```
git checkout -b verify-z3
git add policyc/verification
git commit -m "Add Z3 non-weakening verifier, enumeration baseline and layered pipeline"
git add tests examples requirements.txt
git commit -m "Add verification tests and demo candidate policies"
git add benchmarks docs
git commit -m "Add Z3 vs enumeration benchmark and verification docs"
git push -u origin verify-z3     # then open a pull request into main
```
