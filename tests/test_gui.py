"""GUI server API, exercised over real HTTP on localhost with the fake model."""
import json
import os
import shutil
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest import mock

from policyc.gui.server import make_server

from helpers import read_example

KEY = "SENTINEL-GUI-KEY-42"


class GuiServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.results = tempfile.mkdtemp()
        cls.env = mock.patch.dict(os.environ, {"GEMINI_API_KEY": KEY})
        cls.env.start()
        cls.server = make_server(port=0, llm="fake", results_dir=cls.results)
        cls.base = "http://127.0.0.1:%d" % cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, kwargs={"poll_interval": 0.05},
                         daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.env.stop()
        shutil.rmtree(cls.results)

    def get(self, path):
        with urllib.request.urlopen(self.base + path, timeout=30) as r:
            return r.status, r.headers.get("Content-Type", ""), r.read()

    def post(self, path, payload):
        req = urllib.request.Request(self.base + path, data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_binds_to_localhost_only(self):
        self.assertEqual(self.server.server_address[0], "127.0.0.1")

    def test_index_and_static_files(self):
        status, ctype, body = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", ctype)
        self.assertIn(b"policyc", body)
        for path, kind in (("/app.js", "javascript"), ("/app.css", "css")):
            status, ctype, _ = self.get(path)
            self.assertEqual(status, 200)
            self.assertIn(kind, ctype)

    def test_unknown_path_and_traversal_are_404(self):
        for path in ("/nope", "/../README.md", "/results/..%2F..%2FREADME.md", "/results/x.txt"):
            with self.assertRaises(urllib.error.HTTPError) as cm:
                self.get(path)
            self.assertEqual(cm.exception.code, 404, path)

    def test_status_lists_samples_and_never_the_key(self):
        status, _, body = self.get("/api/status")
        data = json.loads(body)
        self.assertEqual(data["llm"], "fake")
        self.assertTrue(data["key_set"])
        self.assertIn("gemini-", data["model"])
        self.assertEqual(len(data["tasks"]), 20)
        self.assertIn("examples/optimizable.pol", data["sources"])
        self.assertIn("evaluation/optimization/o4_or_merge.pol", data["sources"])
        self.assertNotIn(KEY.encode(), body)

    def test_source_endpoint(self):
        _, _, body = self.get("/api/source?name=examples/valid.pol")
        self.assertIn("SUBJECT", json.loads(body)["source"])
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.get("/api/source?name=../README.md")
        self.assertEqual(cm.exception.code, 404)

    def test_compile_valid_with_optimizer(self):
        status, data = self.post("/api/compile", {"source": read_example("optimizable.pol"),
                                                  "optimize": True})
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        phases = {p["name"]: p["status"] for p in data["phases"]}
        self.assertEqual(phases["Lexer"], "ok")
        self.assertEqual(phases["Semantic"], "warning")
        self.assertEqual(phases["Verifier"], "ok")
        self.assertEqual(len(data["ir"]), 9)
        self.assertEqual(len(data["optimization"]["removed"]), 3)
        self.assertTrue(data["optimization"]["proven"])
        self.assertEqual(data["target"]["format"], "policyc-target/1")
        self.assertTrue(data["tokens"][0]["type"] == "SUBJECT")
        self.assertIn("Program", data["ast"])

    def test_compile_with_syntax_errors(self):
        status, data = self.post("/api/compile", {"source": read_example("syntax_errors.pol")})
        self.assertEqual(status, 200)
        self.assertFalse(data["ok"])
        self.assertEqual(len(data["diagnostics"]), 5)
        phases = {p["name"]: p["status"] for p in data["phases"]}
        self.assertEqual(phases["Parser"], "error")
        self.assertEqual(phases["IR"], "skipped")
        self.assertIsNone(data["target"])

    def test_generate_task_with_fake_model(self):
        status, data = self.post("/api/generate", {"task": "g16"})
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        self.assertTrue(data["verified"])
        self.assertTrue(data["fake"])
        self.assertIn("floor_terminal", data["dsl"])
        self.assertIn("reference", data)
        # The fake model's first answer has a typo, so the demo shows the correction loop.
        self.assertEqual(len(data["attempts"]), 2)
        self.assertFalse(data["attempts"][0]["ok"])
        self.assertEqual(data["attempts"][0]["errors"][0]["code"], "P001")
        self.assertTrue(data["attempts"][1]["ok"])

    def test_generate_free_text_with_vocabulary(self):
        status, data = self.post("/api/generate", {
            "english": "analysts may read reports",
            "vocabulary": {"subjects": ["analyst"], "resources": {"reports": ["READ"]},
                           "attributes": []}})
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        self.assertIsNone(data["verified"])

    def test_generate_needs_text_or_task(self):
        status, data = self.post("/api/generate", {})
        self.assertEqual(status, 400)
        self.assertIn("error", data)

    def test_optimize_with_fake_model(self):
        status, data = self.post("/api/optimize", {"source": read_example("optimizable.pol"),
                                                   "samples": 1, "attempts": 2})
        self.assertEqual(status, 200)
        self.assertEqual(data["baseline"]["rules_after"], 6)
        self.assertEqual(data["proposals"][0]["stage"], "accepted")
        self.assertEqual((data["rules_before"], data["rules_after"]), (9, 6))
        self.assertTrue(data["accepted"])
        self.assertIn("SUBJECT", data["final_source"])

    def test_optimize_rejects_a_policy_that_does_not_compile(self):
        status, data = self.post("/api/optimize", {"source": "ALLOW nobody FLY x"})
        self.assertEqual(status, 400)
        self.assertTrue(data["diagnostics"])

    def test_verify_good_and_bad(self):
        original = read_example("optimizable.pol")
        _, good = self.post("/api/verify", {"original": original,
                                            "candidate": read_example("candidate_good.pol")})
        self.assertTrue(good["proven"])
        _, bad = self.post("/api/verify", {"original": original,
                                           "candidate": read_example("candidate_bad.pol"),
                                           "mode": "equivalent"})
        self.assertFalse(bad["proven"])
        cex = bad["counterexamples"][0]
        self.assertEqual((cex["subject"], cex["original"], cex["candidate"]),
                         ("analyst", "DENY", "ALLOW"))
        _, broken = self.post("/api/verify", {"original": original, "candidate": "ALLOW x FLY y"})
        self.assertFalse(broken["proven"])
        self.assertTrue(broken["candidate_errors"])

    def test_results_absent_then_present(self):
        _, _, body = self.get("/api/results")
        self.assertFalse(json.loads(body)["available"])
        with open(os.path.join(self.results, "run.json"), "w", encoding="utf-8") as f:
            json.dump({"fake": True, "run": {"llm": "fake"}, "summary": {"x": 1}}, f)
        with open(os.path.join(self.results, "tiers.png"), "wb") as f:
            f.write(b"\x89PNG\r\n")
        _, _, body = self.get("/api/results")
        data = json.loads(body)
        self.assertTrue(data["available"])
        self.assertEqual(data["charts"], ["tiers.png"])
        status, ctype, _ = self.get("/results/tiers.png")
        self.assertEqual((status, ctype), (200, "image/png"))

    def test_bad_json_is_400(self):
        req = urllib.request.Request(self.base + "/api/compile", data=b"{not json",
                                     headers={"Content-Type": "application/json"}, method="POST")
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req, timeout=10)
        self.assertEqual(cm.exception.code, 400)

    def test_llm_errors_become_502_without_the_key(self):
        from policyc.ai.llm_client import LLMError

        class Broken:
            name = "gemini:test"

            def complete(self, prompt, n=1):
                raise LLMError("quota", "HTTP 429 after 3 retries")

        with mock.patch("policyc.gui.server.App.client", lambda self, mode, fake: Broken()):
            status, data = self.post("/api/generate", {"english": "x", "llm": "gemini"})
        self.assertEqual(status, 502)
        self.assertEqual(data["kind"], "quota")
        self.assertNotIn(KEY, json.dumps(data))


class GuiCommandTests(unittest.TestCase):
    def test_gui_command_starts_the_server(self):
        from policyc.__main__ import main
        with mock.patch("policyc.gui.server.serve", return_value=0) as serve:
            self.assertEqual(main(["gui", "--port", "9999", "--no-browser", "--llm", "fake"]), 0)
        serve.assert_called_once_with(port=9999, open_browser=False, llm="fake")


if __name__ == "__main__":
    unittest.main()
