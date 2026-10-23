"""LLM client: Gemini REST backend against a local stub server (no network, no key)."""
import json
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest import mock

from policyc.ai.llm_client import (Completion, FakeClient, GeminiClient, LLMError, as_client,
                                   default_client, strip_code_fence, total_tokens)

KEY = "TESTKEY-do-not-leak"


def ok_body(*texts, prompt_tokens=10, output_tokens=4):
    return {"candidates": [{"content": {"parts": [{"text": t}]}} for t in texts],
            "usageMetadata": {"promptTokenCount": prompt_tokens,
                              "candidatesTokenCount": output_tokens}}


class Stub:
    """Serves a queue of (status, headers, body) and records every request."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                stub.requests.append({"path": self.path,
                                      "headers": {k.lower(): v for k, v in self.headers.items()},
                                      "body": json.loads(self.rfile.read(length) or b"{}")})
                status, headers, body = stub.responses.pop(0)
                data = body if isinstance(body, bytes) else json.dumps(body).encode()
                self.send_response(status)
                for k, v in headers.items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = "http://127.0.0.1:%d/v1beta" % self.server.server_port
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05},
                         daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class GeminiClientTests(unittest.TestCase):
    def client(self, responses, **kw):
        stub = Stub(responses)
        self.addCleanup(stub.close)
        kw.setdefault("api_key", KEY)
        return GeminiClient(base_url=stub.url, backoff_s=0, **kw), stub

    def test_success_returns_text_tokens_and_latency(self):
        client, stub = self.client([(200, {}, ok_body("ALLOW a READ r"))])
        [c] = client.complete("hello")
        self.assertEqual((c.text, c.prompt_tokens, c.output_tokens), ("ALLOW a READ r", 10, 4))
        self.assertGreater(c.latency_s, 0)
        req = stub.requests[0]
        self.assertTrue(req["path"].endswith("/models/gemini-flash-lite-latest:generateContent"))
        self.assertEqual(req["headers"]["x-goog-api-key"], KEY)
        self.assertEqual(req["body"]["generationConfig"]["temperature"], 0.2)
        self.assertNotIn("candidateCount", req["body"]["generationConfig"])
        self.assertEqual(req["body"]["contents"][0]["parts"][0]["text"], "hello")
        self.assertEqual(client.name, "gemini:gemini-flash-lite-latest")

    def test_model_from_environment(self):
        with mock.patch.dict(os.environ, {"POLICYC_MODEL": "gemini-x"}):
            client, stub = self.client([(200, {}, ok_body("x"))])
        client.complete("p")
        self.assertIn("/models/gemini-x:generateContent", stub.requests[0]["path"])

    def test_n_candidates_in_one_request(self):
        client, stub = self.client([(200, {}, ok_body("a", "b", output_tokens=8))])
        cs = client.complete("p", n=2)
        self.assertEqual([c.text for c in cs], ["a", "b"])
        self.assertEqual([c.output_tokens for c in cs], [4, 4])
        self.assertEqual([c.prompt_tokens for c in cs], [10, None])
        self.assertEqual(len(stub.requests), 1)
        self.assertEqual(stub.requests[0]["body"]["generationConfig"]["candidateCount"], 2)

    def test_candidate_count_rejected_falls_back_to_sequential(self):
        rejected = {"error": {"code": 400, "message": "candidateCount must be 1 for this model"}}
        client, stub = self.client([(400, {}, rejected), (200, {}, ok_body("a")),
                                    (200, {}, ok_body("b"))])
        self.assertEqual([c.text for c in client.complete("p", n=2)], ["a", "b"])
        self.assertEqual(len(stub.requests), 3)

    def test_429_then_success(self):
        client, stub = self.client([(429, {"Retry-After": "0"}, {"error": {"message": "slow down"}}),
                                    (200, {}, ok_body("x"))])
        self.assertEqual(client.complete("p")[0].text, "x")
        self.assertEqual(len(stub.requests), 2)

    def test_429_exhausted_raises_quota(self):
        client, _ = self.client([(429, {}, {"error": {"message": "quota"}})] * 2, max_retries=1)
        with self.assertRaises(LLMError) as cm:
            client.complete("p")
        self.assertEqual(cm.exception.kind, "quota")

    def test_401_raises_auth_without_retry(self):
        client, stub = self.client([(401, {}, {"error": {"message": "API key not valid"}})])
        with self.assertRaises(LLMError) as cm:
            client.complete("p")
        self.assertEqual(cm.exception.kind, "auth")
        self.assertEqual(len(stub.requests), 1)

    def test_missing_key_raises_auth_before_request(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            client, stub = self.client([], api_key=None)
            with self.assertRaises(LLMError) as cm:
                client.complete("p")
        self.assertEqual(cm.exception.kind, "auth")
        self.assertIn("GEMINI_API_KEY", str(cm.exception))
        self.assertEqual(stub.requests, [])

    def test_no_candidates_is_bad_response(self):
        client, _ = self.client([(200, {}, {"promptFeedback": {"blockReason": "SAFETY"}})])
        with self.assertRaises(LLMError) as cm:
            client.complete("p")
        self.assertEqual(cm.exception.kind, "bad_response")

    def test_candidate_without_parts_is_empty_text(self):
        body = {"candidates": [{"finishReason": "SAFETY", "content": {}}]}
        client, _ = self.client([(200, {}, body)])
        self.assertEqual(client.complete("p")[0].text, "")

    def test_malformed_json_is_bad_response(self):
        client, _ = self.client([(200, {}, b"not json")])
        with self.assertRaises(LLMError) as cm:
            client.complete("p")
        self.assertEqual(cm.exception.kind, "bad_response")

    def test_key_never_in_error_messages(self):
        echo = {"error": {"message": "bad key %s" % KEY}}
        cases = [[(401, {}, echo)], [(429, {}, echo)] * 2, [(500, {}, echo)], [(200, {}, b"x")],
                 [(200, {}, {})]]
        for responses in cases:
            client, _ = self.client(responses, max_retries=1)
            with self.assertRaises(LLMError) as cm:
                client.complete("p")
            self.assertNotIn(KEY, str(cm.exception))
            self.assertNotIn(KEY, repr(cm.exception))
        self.assertNotIn(KEY, repr(client))


class HelperTests(unittest.TestCase):
    def test_as_client_wraps_plain_function(self):
        client = as_client(lambda p: p.upper())
        self.assertEqual(client.name, "function")
        [c] = client.complete("abc")
        self.assertEqual((c.text, c.prompt_tokens), ("ABC", None))
        fake = FakeClient(["x"])
        self.assertIs(as_client(fake), fake)

    def test_fake_client_list_and_callable(self):
        fake = FakeClient(["one", "two"])
        self.assertEqual([fake.complete("a")[0].text, fake.complete("b")[0].text,
                          fake.complete("c")[0].text], ["one", "two", "two"])
        self.assertEqual(fake.prompts, ["a", "b", "c"])
        self.assertEqual([c.text for c in fake.complete("d", n=2)], ["two", "two"])
        echo = FakeClient(lambda p: "re:" + p)
        self.assertEqual(echo.complete("q")[0].text, "re:q")
        self.assertEqual(echo.name, "fake")

    def test_default_client_env(self):
        with mock.patch.dict(os.environ, {"POLICYC_LLM": "fake"}):
            self.assertIsInstance(default_client(), FakeClient)
        self.assertIsInstance(default_client("gemini"), GeminiClient)
        with self.assertRaises(ValueError):
            default_client("nope")

    def test_total_tokens(self):
        self.assertEqual(total_tokens([Completion("a", 3, 2), Completion("b", None, 1)]), 6)
        self.assertIsNone(total_tokens([Completion("a")]))

    def test_strip_code_fence_unchanged(self):
        self.assertEqual(strip_code_fence("```\nALLOW a READ r\n```"), "ALLOW a READ r")
        self.assertEqual(strip_code_fence("ALLOW a READ r"), "ALLOW a READ r")


if __name__ == "__main__":
    unittest.main()
