# GUI design: demo web app

Date: 2026-10-23. Approved in conversation ("build the GUI as proposed").

## Goal

A GUI that makes the final demo readable on a projector, built on the same code as
the CLI so the two can never disagree.

## Decisions

| Decision | Choice | Why |
|---|---|---|
| Approach | Local web app on Python's `http.server`; plain HTML/CSS/JS | No install, works offline, looks modern |
| Command | `python -m policyc gui [--port 8765] [--no-browser] [--llm gemini\|fake]` | Opens the browser |
| Binding | `127.0.0.1` only | Nothing on the lab network can reach it |
| Model | Header toggle Gemini / Fake; Gemini disabled without `GEMINI_API_KEY` | Rehearse offline; key stays server-side |
| Key | Never sent to the browser, never in responses or logs | Same rule as the CLI |

## Tabs

1. **Compile**: an editor with an example picker, and a pipeline stepper (Lexer, Parser,
   Semantic, IR, Optimizer, Verifier, Codegen). Each step is coloured by status; clicking
   one shows its output (tokens, AST, diagnostics, IR, removals and verdict, target
   JSON). Diagnostics are clickable and select their line.
2. **Generate**: a task picker (g01-g20) or free text plus vocabulary. Shows an attempt
   timeline (policy, errors fed back), the final policy, a verified-vs-reference badge,
   and latency and tokens.
3. **Optimize**: a policy picker or editor, with samples, attempts and mode. Shows a
   compiler-only baseline card, one card per proposal with its stage badge, verifier
   summary and counterexamples, and a line diff of the accepted rewrite.
4. **Verify**: two editors with presets (optimizable vs good, bad, drops-DENY, stricter)
   and a mode. Shows a verdict banner and a counterexample table.
5. **Results**: headline numbers, tables and charts from `evaluation/results/`, with the
   FAKE banner when they are fake.

## API (JSON)

`GET /api/status`, `GET /api/source?name=`, `POST /api/compile`, `POST /api/generate`,
`POST /api/optimize`, `POST /api/verify`, `GET /api/results`, `GET /results/<chart>.png`.
Errors: 400 for bad input, 502 with `{error, kind}` for a failed model call.

## Testing

`tests/test_gui.py` starts the server on port 0 with the fake model and exercises every
endpoint, including the 404 and 400 cases, key absence, and the 127.0.0.1 binding. Then
every tab is driven in a browser and checked with screenshots.
