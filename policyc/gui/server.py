"""HTTP server for the demo GUI (standard library only).

Serves the single-page app in static/ and a small JSON API that calls the
same functions as the CLI, so the GUI can never disagree with it:

    GET  /api/status            model, key present?, sample policies, benchmark tasks
    GET  /api/source?name=P     text of a sample policy (examples/ and evaluation/ only)
    POST /api/compile           every phase's output for a policy
    POST /api/generate          English -> DSL, with the attempt timeline
    POST /api/optimize          LLM proposals, each checked by compiler and verifier
    POST /api/verify            original vs candidate, with counterexamples
    GET  /api/results           evaluation/results/run.json and its charts
    GET  /results/<chart>.png   one chart

It listens on 127.0.0.1 only. The API key stays in this process: it is never
sent to the browser and never appears in a response.
"""

from __future__ import annotations

import glob
import json
import mimetypes
import os
import re
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional

from .. import ast_nodes as A
from ..ai.evaluation.run import render_source
from ..ai.evaluation.tasks import EVAL_ROOT, load_generation_tasks
from ..ai.generator import generate_policy
from ..ai.llm_client import DEFAULT_MODEL, FakeClient, GeminiClient, LLMError
from ..ai.llm_optimizer import optimize_with_llm
from ..ai.prompts import Vocabulary
from ..compiler import CompileResult, compile_file, compile_source
from ..verifier import verify

STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
REPO = os.path.dirname(EVAL_ROOT)
SOURCE_DIRS = ("examples", "evaluation/optimization", "evaluation/semantic", "evaluation/generation")
PHASES = ("Lexer", "Parser", "Semantic", "IR", "Optimizer", "Verifier", "Codegen")


class BadRequest(Exception):
    """400: the request cannot be served (optionally with compiler diagnostics)."""

    def __init__(self, message: str, diagnostics=None):
        super().__init__(message)
        self.diagnostics = diagnostics or []


def _strip_comments(text: str) -> str:
    return "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#")).strip() + "\n"


# ---- payload builders ---------------------------------------------------------

def _diags(res: CompileResult):
    return [d.to_dict() for d in res.diagnostics]


def _phases(res: CompileResult, optimize: bool):
    def status_of(phase):
        own = [d for d in res.diagnostics if d.phase == phase]
        if any(d.is_error for d in own):
            return "error"
        return "warning" if own else "ok"

    out = {"Lexer": status_of("lexer"), "Parser": status_of("parser")}
    front_ok = out["Lexer"] != "error" and out["Parser"] != "error"
    out["Semantic"] = status_of("semantic") if front_ok else "skipped"
    out["IR"] = "ok" if res.ir is not None else "skipped"
    if optimize and res.optimization is not None:
        out["Optimizer"] = "ok"
        out["Verifier"] = "ok" if res.verification.proven else "error"
    else:
        out["Optimizer"] = out["Verifier"] = "skipped"
    out["Codegen"] = "ok" if res.target is not None else "skipped"
    return [{"name": p, "status": out[p]} for p in PHASES]


def _ir_rows(ir):
    return [{"id": r.id, "effect": r.effect, "subject": r.subject, "action": r.action,
             "resource": r.resource, "condition": "" if r.condition is None else r.cond_key,
             "line": r.line} for r in ir.rules]


def compile_payload(source: str, optimize: bool = False) -> dict:
    res = compile_source(source, optimize_ir=optimize)
    out = {"ok": res.ok, "diagnostics": _diags(res), "phases": _phases(res, optimize),
           "tokens": [{"type": t.type.name, "lexeme": t.lexeme, "line": t.line, "col": t.col}
                      for t in res.tokens],
           "ast": A.dump(res.ast) if res.ast is not None and res.ok else None,
           "ir": _ir_rows(res.ir) if res.ir is not None else [],
           "optimization": None, "target": res.target}
    if optimize and res.optimization is not None:
        out["optimization"] = {
            "removed": [{"id": rm.rule.id, "line": rm.rule.line, "reason": rm.reason}
                        for rm in res.optimization.removed],
            "rules_before": len(res.ir.rules), "rules_after": len(res.final_ir.rules),
            "proven": res.verification.proven, "summary": res.verification.summary(),
            "kept": res.final_ir is not res.ir}
    return out


def _cex(v):
    return [c.to_dict() for c in v.counterexamples[:3]] if v is not None else []


# ---- the app --------------------------------------------------------------------

class App:
    def __init__(self, llm: Optional[str] = None, results_dir: Optional[str] = None):
        self.default_llm = llm or ("gemini" if os.environ.get("GEMINI_API_KEY") else "fake")
        self.results_dir = results_dir or os.path.join(EVAL_ROOT, "results")
        self.tasks = {t.id: t for t in load_generation_tasks()}

    def client(self, mode: str, fake_reply):
        if mode == "gemini":
            return GeminiClient()
        return FakeClient(fake_reply)

    def _mode(self, body: dict) -> str:
        mode = body.get("llm") or self.default_llm
        if mode not in ("gemini", "fake"):
            raise BadRequest("llm must be gemini or fake")
        return mode

    # GET -----------------------------------------------------------------------
    def status(self) -> dict:
        sources = sorted(os.path.relpath(p, REPO).replace(os.sep, "/")
                         for d in SOURCE_DIRS for p in glob.glob(os.path.join(REPO, d, "*.pol")))
        return {"llm": self.default_llm, "key_set": bool(os.environ.get("GEMINI_API_KEY")),
                "model": os.environ.get("POLICYC_MODEL") or DEFAULT_MODEL,
                "sources": sources,
                "tasks": [{"id": t.id, "difficulty": t.difficulty, "english": t.english,
                           "vocabulary": {"subjects": t.vocabulary.subjects,
                                          "resources": t.vocabulary.resources,
                                          "attributes": t.vocabulary.attributes}}
                          for t in self.tasks.values()]}

    def source(self, name: str) -> Optional[dict]:
        name = (name or "").replace("\\", "/")
        if ".." in name or not any(name.startswith(d + "/") for d in SOURCE_DIRS) \
                or not name.endswith(".pol"):
            return None
        path = os.path.join(REPO, *name.split("/"))
        if not os.path.isfile(path):
            return None
        with open(path, encoding="utf-8") as f:
            return {"name": name, "source": f.read()}

    def results(self) -> dict:
        path = os.path.join(self.results_dir, "run.json")
        if not os.path.exists(path):
            return {"available": False}
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        charts = sorted(os.path.basename(p) for p in glob.glob(os.path.join(self.results_dir, "*.png")))
        return {"available": True, "fake": data.get("fake", False), "run": data.get("run", {}),
                "summary": data.get("summary", {}), "charts": charts}

    def chart(self, name: str) -> Optional[str]:
        if not re.fullmatch(r"[A-Za-z0-9_\-]+\.png", name or ""):
            return None
        path = os.path.join(self.results_dir, name)
        return path if os.path.isfile(path) else None

    # POST ----------------------------------------------------------------------
    def compile(self, body: dict) -> dict:
        return compile_payload(str(body.get("source", "")), bool(body.get("optimize")))

    def generate(self, body: dict) -> dict:
        mode = self._mode(body)
        task = self.tasks.get(body.get("task") or "")
        if body.get("task") and task is None:
            raise BadRequest("unknown task %r" % body.get("task"))
        english = (body.get("english") or (task.english if task else "")).strip()
        if not english:
            raise BadRequest("type a requirement or pick a benchmark task")
        if task:
            vocab = task.vocabulary
        elif body.get("vocabulary"):
            v = body["vocabulary"]
            vocab = Vocabulary(list(v.get("subjects", [])), dict(v.get("resources", {})),
                               list(v.get("attributes", [])))
        else:
            vocab = None
        reference = None
        if task:
            with open(task.reference_path, encoding="utf-8") as f:
                reference = _strip_comments(f.read())
        fake_answer = reference or (vocab.declarations() + "\n" if vocab else "SUBJECT nobody\n")
        # Fake model: the first answer has a typo (an action that does not exist), so the
        # demo shows the compiler's errors going back and the fix coming in.
        typo = re.sub(r"^((?:ALLOW|DENY) \w+ )(READ|WRITE|EXECUTE|ACCESS)\b", r"\1FLY",
                      fake_answer, count=1, flags=re.M)
        replies = [typo, fake_answer] if typo != fake_answer else [fake_answer]
        client = self.client(mode, lambda prompt: replies.pop(0) if len(replies) > 1 else replies[0])
        res = generate_policy(english, vocab, llm=client, max_attempts=int(body.get("attempts", 3)))
        verified = None
        if task and res.ok:
            verified = verify(compile_file(task.reference_path).ir,
                              res.attempts[-1].result.ir, mode="equivalent").proven
        return {"ok": res.ok, "dsl": res.dsl, "verified": verified, "reference": reference,
                "english": english, "fake": mode == "fake", "model": client.name,
                "tokens": res.tokens, "latency_s": round(res.latency_s, 3),
                "attempts": [{"dsl": a.dsl, "ok": a.ok, "errors": a.errors(),
                              "warnings": [d.to_dict() for d in a.result.warnings] if a.result.ok else [],
                              "latency_s": round(a.completion.latency_s, 3)} for a in res.attempts]}

    def optimize(self, body: dict) -> dict:
        mode = self._mode(body)
        source = str(body.get("source", ""))
        original = compile_source(source)
        if not original.ok:
            raise BadRequest("the policy must compile before it can be optimized",
                             _diags(original))
        baseline = compile_source(source, optimize_ir=True)
        fake_answer = render_source(baseline.final_ir) if baseline.final_ir is not baseline.ir else source
        client = self.client(mode, lambda prompt: fake_answer)
        res = optimize_with_llm(source, llm=client, max_attempts=int(body.get("attempts", 3)),
                                samples=int(body.get("samples", 1)),
                                mode=body.get("mode") or "equivalent")
        return {
            "fake": mode == "fake", "model": client.name, "mode": res.mode,
            "baseline": {"rules_before": len(baseline.ir.rules),
                         "rules_after": len(baseline.final_ir.rules),
                         "removed": [{"id": rm.rule.id, "line": rm.rule.line, "reason": rm.reason}
                                     for rm in baseline.optimization.removed]},
            "proposals": [{"attempt": p.attempt, "stage": p.stage, "tier": p.tier, "dsl": p.dsl,
                           "note": p.note, "stricter": p.stricter,
                           "drops_deny_guard": p.drops_deny_guard,
                           "errors": [d.to_dict() for d in p.compiled.errors],
                           "verifier": p.verification.summary() if p.verification else None,
                           "counterexamples": _cex(p.verification),
                           "rules": p.size[0] if p.size else None,
                           "chosen": p is res.accepted} for p in res.proposals],
            "accepted": res.accepted is not None,
            "rules_before": res.rules_before, "rules_after": res.rules_after,
            "statements_before": res.statements_before, "statements_after": res.statements_after,
            "final_source": res.final_source, "original_source": source,
            "tokens": res.tokens, "latency_s": round(res.latency_s, 3)}

    def verify(self, body: dict) -> dict:
        a = compile_source(str(body.get("original", "")))
        b = compile_source(str(body.get("candidate", "")))
        mode = body.get("mode") or "equivalent"
        out = {"original_errors": [d.to_dict() for d in a.errors],
               "candidate_errors": [d.to_dict() for d in b.errors], "mode": mode}
        if not (a.ok and b.ok):
            out.update(proven=False, verdict="DOES NOT COMPILE", summary="", counterexamples=[])
            return out
        v = verify(a.ir, b.ir, mode=mode, find_all=True)
        out.update(proven=v.proven, verdict=v.verdict, summary=v.summary(),
                   counterexamples=[c.to_dict() for c in v.counterexamples[:10]],
                   rules=[len(a.ir.rules), len(b.ir.rules)])
        return out


# ---- HTTP -------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    app: App = None   # set by make_server
    server_version = "policyc-gui"

    def log_message(self, *args):        # keep the terminal clean for the demo
        pass

    def _send(self, status: int, body: bytes, ctype: str):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, data):
        self._send(status, json.dumps(data, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _not_found(self):
        self._json(404, {"error": "not found"})

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        path = urllib.parse.unquote(url.path)
        if path == "/api/status":
            return self._json(200, self.app.status())
        if path == "/api/source":
            found = self.app.source(urllib.parse.parse_qs(url.query).get("name", [""])[0])
            return self._json(200, found) if found else self._not_found()
        if path == "/api/results":
            return self._json(200, self.app.results())
        if path.startswith("/results/"):
            chart = self.app.chart(path[len("/results/"):])
            if not chart:
                return self._not_found()
            with open(chart, "rb") as f:
                return self._send(200, f.read(), "image/png")
        name = "index.html" if path in ("/", "") else path.lstrip("/")
        if not re.fullmatch(r"[A-Za-z0-9_\-]+\.(html|js|css|svg)", name):
            return self._not_found()
        file = os.path.join(STATIC, name)
        if not os.path.isfile(file):
            return self._not_found()
        ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
        if name.endswith(".js"):
            ctype = "text/javascript"
        with open(file, "rb") as f:
            self._send(200, f.read(), ctype + "; charset=utf-8" if ctype.startswith("text/") else ctype)

    def do_POST(self):
        routes = {"/api/compile": self.app.compile, "/api/generate": self.app.generate,
                  "/api/optimize": self.app.optimize, "/api/verify": self.app.verify}
        handler = routes.get(urllib.parse.urlparse(self.path).path)
        if handler is None:
            return self._not_found()
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
            if not isinstance(body, dict):
                raise ValueError("expected a JSON object")
        except (ValueError, UnicodeDecodeError) as e:
            return self._json(400, {"error": "bad request body: %s" % e})
        try:
            return self._json(200, handler(body))
        except BadRequest as e:
            return self._json(400, {"error": str(e), "diagnostics": e.diagnostics})
        except LLMError as e:
            return self._json(502, {"error": str(e), "kind": e.kind})


def make_server(host: str = "127.0.0.1", port: int = 8765, llm: Optional[str] = None,
                results_dir: Optional[str] = None) -> ThreadingHTTPServer:
    handler = type("BoundHandler", (Handler,), {"app": App(llm, results_dir)})
    server = ThreadingHTTPServer((host, port), handler)
    server.daemon_threads = True
    return server


def serve(port: int = 8765, open_browser: bool = True, llm: Optional[str] = None) -> int:
    try:
        server = make_server(port=port, llm=llm)
    except OSError as e:
        print("policyc gui: cannot listen on port %d (%s); try --port 8766" % (port, e))
        return 2
    url = "http://127.0.0.1:%d/" % server.server_address[1]
    app = server.RequestHandlerClass.app
    print("policyc GUI running at %s  (model: %s%s)  Ctrl+C to stop" % (
        url, app.default_llm, "" if app.default_llm == "fake" else
        " " + (os.environ.get("POLICYC_MODEL") or DEFAULT_MODEL)), flush=True)
    if open_browser:
        import webbrowser
        webbrowser.open(url)
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0
