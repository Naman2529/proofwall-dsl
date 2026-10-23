# Evaluation (Review 1 objectives 3 to 6)

`python -m policyc evaluate` runs every measurement Review 1 promised and writes
the numbers, tables and charts for the demo and the report.

## Setup

```bash
pip install -r requirements.txt          # z3-solver (optional) for the verifier
pip install matplotlib                   # optional, for the PNG charts
export GEMINI_API_KEY=...                # PowerShell: $env:GEMINI_API_KEY="..."
```

Optional settings:

| Variable | Default | Meaning |
|---|---|---|
| `POLICYC_LLM` | `gemini` | provider: `gemini` or `fake` |
| `POLICYC_MODEL` | `gemini-flash-lite-latest` | any Gemini model name or alias |

The key is read from the environment only. It is never printed, logged or written
to any result file.

## Run it

```bash
python -m policyc evaluate                        # everything (needs the key for the AI suites)
python -m policyc evaluate --llm fake             # rehearsal with scripted answers, marked FAKE
python -m policyc evaluate --suite semantic       # objective 3 only, no key needed
python -m policyc evaluate --limit 5              # first 5 tasks / policies per suite
python -m policyc evaluate --samples 3            # optimizer asks for 3 proposals per attempt
python -m policyc evaluate --fresh                # discard earlier raw results of the suites being run
```

Without `GEMINI_API_KEY` the command still runs the semantic suite, says the AI
suites were skipped, and reports the latest earlier AI results if there are any
(otherwise the compiler-only baseline). Running a single suite never blanks the
others: their latest results stay in the reports.

### Free-tier budget and resuming

A full run asks the model at most 20 x 3 + 8 x 3 = 84 times (`--attempts 3`,
`--samples 1`); with `--samples N` and Gemini's `candidateCount` the request count
stays the same. Each finished task is saved immediately to
`evaluation/results/raw/*.jsonl`. If the daily quota runs out (or the key is
rejected), that task and every remaining one are recorded as **not run** without
calling the model again (never as model failures), and the same command later
picks up where it stopped. Waits requested by the server are capped at 60 s, and
one progress line per task goes to stderr. Records are keyed by task, model, settings and the
prompt fingerprint, so results from different settings are never mixed.

### Single runs for the demo

```bash
python -m policyc generate --task g16                         # one benchmark requirement
python -m policyc generate "contractors may read reports from the VPN" \
    --subjects contractor --resource reports=READ,WRITE --attributes on_vpn -o out.pol
python -m policyc optimize evaluation/optimization/o4_or_merge.pol --samples 3
```

Add `--llm fake` to any of them to rehearse without a key.

## What is measured

### Objective 3: seeded semantic issues (`evaluation/semantic/`)

Ten policies with marked mistakes (`# expect: CODE`). Each expected diagnostic is
**detected** or **missed**; anything reported without a marker is a **false
alarm**. Two cases are marked `# known miss CODE: why`: they are beyond what the
pairwise checks can see (an ALLOW beaten only by two DENYs together, and a rule
covered only by two others together) and are kept so the rate is honest.

### Objective 4: English to DSL (`evaluation/generation/`)

20 requirements (6 easy, 8 medium, 6 hard), each with the names to use and a
hand-written reference policy the model never sees.

| Metric | Definition |
|---|---|
| Compiles (1st try) | the first answer compiles |
| Compiles (final) | some answer compiled within the attempts |
| Correct (1st try / final) | the verifier proves the answer **equivalent to the reference** |
| Correction cycles | attempts - 1 |
| Tokens, latency | per task, summed over its attempts |

### Objective 5: optimization (`evaluation/optimization/`)

Eight policies: three the compiler-only optimizer already handles, three that need
rewrites it cannot do (OR-merge, `x AND y` + `x AND NOT y`, READ+WRITE to ACCESS),
one mixed and one already-optimal control. Each is run through the compiler-only
optimizer (the baseline) and the LLM optimizer.

| Metric | Definition |
|---|---|
| Rule reduction | IR rules before and after, baseline and LLM, on the same policies |
| Statements | DSL statements before and after (an ACCESS merge shortens the policy, not the IR) |
| Accepted / rejected | proposals accepted, and rejections by stage: compile, declarations, verifier, not smaller |
| DENY guards dropped | proposals (and accepted proposals) that remove an explicit DENY rule |
| Tokens, latency | per policy; the baseline uses 0 tokens |

The baseline is valid by construction: its output is kept only when the verifier
proves it equivalent.

With `--mode non-weakening` a proposal may deny more than the original. Such
proposals are marked stricter, never count as "improving", and lose to any
equivalent proposal; `optimize` prints a warning when it accepts one. Rule
reduction is only comparable to the baseline in the default `equivalent` mode.

### Classification of every AI output

| Tier | Meaning |
|---|---|
| invalid | lexer or parser errors (or an empty answer) |
| syntactically valid | parses, but has semantic errors |
| semantically valid | compiles |
| verified | generation: equivalent to the reference; optimization: proven by the verifier |
| improving | optimization only: verified and strictly smaller |

## Outputs (`evaluation/results/`)

| File | Contents |
|---|---|
| `summary.txt` | what the command prints |
| `results.md` | tables and short findings, ready to paste into the report |
| `generation.csv` | one row per generation attempt |
| `optimization.csv` | one row per policy, baseline and LLM |
| `proposals.csv` | one row per optimizer proposal, with its DSL |
| `semantic.csv` | one row per expected or reported diagnostic |
| `run.json` | date, model, settings, prompt fingerprint and every number |
| `*.png` | charts (with matplotlib): tiers, rule reduction, correctness, semantic |
| `raw/*.jsonl` | the raw records the reports are built from |

Everything from a `--llm fake` run is marked `FAKE - not real results`: the text,
CSV and Markdown files start with it, `run.json` has `"fake": true` and a
`"banner"`, and every chart carries it as a heading.
`evaluation/results/` is git-ignored so rehearsals never land in git; commit a real
run deliberately:

```bash
git add -f evaluation/results
```
