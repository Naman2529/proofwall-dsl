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

## How the rest of the compiler uses it

All three call sites go through `policyc.verifier.verify()`, a thin wrapper around `verify_candidate()`:

| Caller | Mode | Accepts only if |
|---|---|---|
| Compiler gate (`compile --optimize`) | `equivalent` | `res.equivalent` (proven, same decisions) |
| CLI (`python -m policyc verify`, `--mode`, `--backend`) | `equivalent` by default | `res.proven` |
| LLM optimizer loop (`ai/llm_optimizer.py`, `mode=` argument) | `equivalent` by default | `res.proven`; otherwise `res.feedback()` goes into the retry prompt |

The optimizer paths default to `equivalent` because an optimizer must not change meaning; `non-weakening` is available wherever a stricter rewrite is acceptable. This standalone CLI keeps `non-weakening` as its default.

Without `z3-solver`, `verify_z3()` returns UNPROVEN and the layered pipeline uses exact enumeration as its proof layer instead (still failing closed past 16 attributes per request), so the compiler itself has no hard dependency on Z3.

`normalize()` reads the IR by duck typing (`ir.rules`, and per rule `id/effect/subject/action/resource/condition/line`). Conditions are matched by class name (`Attr`/`Ident`, `And`, `Or`, `Not`) or by the codegen JSON shape. `tests/test_integration.py` checks it reads the compiler's real IR identically to the source. This CLI compiles `.pol` files with the real front end; `loader.py` remains as a dependency-free reader for the unit tests (one statement per line).

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
