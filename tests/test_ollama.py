"""Synthetic mocked Ollama transport tests; no real inference is performed."""

import base64
import copy
import json
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from app.models import Question, TranscriptionResult
from app.ollama import ModelDriftError, OllamaClient, OllamaError


MODEL = "qwen3-vl:4b-instruct-q4_K_M"
DIGEST = "a" * 64
SCHEMA = {"type": "object", "properties": {"answer": {"type": "string"}},
          "required": ["answer"], "additionalProperties": False}


class FakeOllama:
    def __init__(self):
        self.calls = []
        self.tags_count = 0
        self.version_count = 0
        self.digest_after = DIGEST
        self.version_after = "0.13.0"
        self.tag_extra = {}
        self.show_extra = {}
        self.capabilities = ["completion", "vision"]
        self.response = {"model": MODEL, "done": True, "done_reason": "stop",
                         "message": {"role": "assistant", "content": '{"answer":"synthetic"}'}}

    def request(self, path, payload=None):
        self.calls.append((path, copy.deepcopy(payload)))
        if path == "/api/version":
            self.version_count += 1
            return {"version": "0.13.0" if self.version_count == 1 else self.version_after}
        if path == "/api/tags":
            self.tags_count += 1
            return {"models": [{"name": MODEL,
                                "digest": DIGEST if self.tags_count == 1 else self.digest_after,
                                "size": 3_300_000_000, **self.tag_extra}]}
        if path == "/api/show":
            return {"capabilities": self.capabilities, "details": {"family": "qwen3vl"}, **self.show_extra}
        if path == "/api/chat":
            return copy.deepcopy(self.response)
        raise AssertionError(f"Unexpected endpoint: {path}")


class EndpointTests(unittest.TestCase):
    def test_private_loopback_and_ula_endpoints_are_accepted(self):
        for url in ["http://127.0.0.1:11434", "http://192.168.1.4:11434", "http://10.0.0.2:11434",
                    "http://172.16.4.8:11434", "http://[::1]:11434", "http://[fd12::4]:11434"]:
            with self.subTest(url=url):
                self.assertTrue(OllamaClient(url)._request_origin)

    def test_public_link_local_metadata_credentials_and_paths_are_rejected(self):
        urls = ["https://8.8.8.8", "http://169.254.169.254", "http://100.100.100.200",
                "http://0.0.0.0:11434", "http://[fe80::1]:11434", "file:///tmp/ollama",
                "http://user:password@127.0.0.1:11434", "http://127.0.0.1:11434/api",
                "http://127.0.0.1:11434?key=x", "http://127.0.0.1:11434#fragment",
                "http://127.0.0.1:11434\n", "http://[::ffff:8.8.8.8]:11434"]
        for url in urls:
            with self.subTest(url=url), self.assertRaises(OllamaError):
                OllamaClient(url)

    def test_mixed_private_public_dns_is_rejected(self):
        records = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 11434))
                   for address in ["192.168.1.20", "8.8.8.8"]]
        with patch("app.ollama.socket.getaddrinfo", return_value=records), self.assertRaises(OllamaError):
            OllamaClient("http://ianchat:11434")

    def test_dns_connection_is_pinned_and_proxy_redirects_disabled(self):
        records = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.1.20", 11434))]
        observed = []
        options = []
        original_client = httpx.Client

        def handler(request):
            observed.append(request)
            return httpx.Response(200, json={"version": "0.13.0"})

        def client_factory(**kwargs):
            options.append(kwargs)
            return original_client(transport=httpx.MockTransport(handler), **kwargs)

        with patch("app.ollama.socket.getaddrinfo", return_value=records) as resolver:
            client = OllamaClient("http://ianchat:11434")
            with patch("app.ollama.httpx.Client", side_effect=client_factory):
                self.assertEqual(client._request("/api/version")["version"], "0.13.0")
            self.assertEqual(resolver.call_count, 1)
        self.assertEqual(observed[0].url.host, "192.168.1.20")
        self.assertEqual(observed[0].headers["host"], "ianchat:11434")
        self.assertFalse(options[0]["trust_env"])
        self.assertFalse(options[0]["follow_redirects"])

    def test_http_redirect_http_error_invalid_json_and_timeout_are_failures(self):
        original_client = httpx.Client
        outcomes = [httpx.Response(302, headers={"location": "https://example.org"}),
                    httpx.Response(500, text="internal data"), httpx.Response(200, text="not JSON"),
                    httpx.Response(200, text='{"version":"x","version":"y"}'),
                    httpx.Response(200, text='{"value":NaN}')]
        for outcome in outcomes:
            transport = httpx.MockTransport(lambda request, value=outcome: value)
            with self.subTest(outcome=outcome), patch("app.ollama.httpx.Client", side_effect=lambda **kw: original_client(transport=transport, **kw)):
                with self.assertRaises(OllamaError):
                    OllamaClient("http://127.0.0.1:11434")._request("/api/version")

        def timeout(request):
            raise httpx.ReadTimeout("synthetic timeout")
        transport = httpx.MockTransport(timeout)
        with patch("app.ollama.httpx.Client", side_effect=lambda **kw: original_client(transport=transport, **kw)):
            with self.assertRaisesRegex(OllamaError, "timed out"):
                OllamaClient("http://127.0.0.1:11434")._request("/api/version")


class InferenceTests(unittest.TestCase):
    def setUp(self):
        self.client = OllamaClient("http://127.0.0.1:11434")
        self.fake = FakeOllama()
        self.client._request = self.fake.request

    def call(self, **kwargs):
        return self.client.chat_structured(MODEL, SCHEMA, "Synthetic system", "Synthetic input",
                                           expected_digest=DIGEST, **kwargs)

    def test_success_is_checked_before_and_after(self):
        self.assertEqual(self.call(), {"answer": "synthetic"})
        self.assertEqual([path for path, _ in self.fake.calls],
                         ["/api/version", "/api/tags", "/api/show", "/api/chat",
                          "/api/version", "/api/tags", "/api/show"])
        payload = next(body for path, body in self.fake.calls if path == "/api/chat")
        self.assertEqual(payload["options"]["temperature"], 0)
        self.assertEqual(payload["options"]["num_ctx"], 4096)
        self.assertFalse(payload["stream"])
        self.assertNotIn("tools", payload)

    def test_oversized_user_guidance_or_schema_fails_before_any_server_request(self):
        oversized = "á" * 4000  # UTF-8 byte budget, not Python character count.
        for location in ["user", "system", "schema"]:
            with self.subTest(location=location):
                schema = copy.deepcopy(SCHEMA)
                system, user = "Synthetic system", "Synthetic input"
                if location == "schema":
                    schema["description"] = oversized
                elif location == "system":
                    system = oversized
                else:
                    user = oversized
                with self.assertRaisesRegex(OllamaError, "Oversized input.*no score was assigned"):
                    self.client.chat_structured(MODEL, schema, system, user, expected_digest=DIGEST)
                self.assertFalse(self.fake.calls)

    def test_text_budget_counts_guidance_schema_and_answer_together(self):
        schema = copy.deepcopy(SCHEMA)
        schema["description"] = "s" * 3000
        with self.assertRaisesRegex(OllamaError, "Oversized input"):
            self.client.chat_structured(MODEL, schema, "s" * 2500, "u" * 2500,
                                        expected_digest=DIGEST)
        self.assertFalse(self.fake.calls)

    def test_larger_configured_context_preserves_full_input_without_fallback(self):
        literal = "SYNTHETIC " + "a" * 9000
        self.assertEqual(self.client.chat_structured(MODEL, SCHEMA, "s", literal,
                         expected_digest=DIGEST, num_ctx=8192), {"answer": "synthetic"})
        payload = next(body for path, body in self.fake.calls if path == "/api/chat")
        self.assertEqual(payload["messages"][1]["content"], literal)
        self.assertEqual(payload["model"], MODEL)
        self.assertEqual(payload["options"]["num_ctx"], 8192)

    def test_short_rubric_grading_fits_guard_and_preserves_evidence(self):
        question = Question(id="1", type="rubric", prompt="Translate: I eat an apple.", max_points=1,
                            rubric=[{"id": "meaning", "description": "Express the stated meaning.", "max_points": 1}],
                            notes="Assessment instructions: Follow the approved rubric.\nQuestion policy: Accept omitted subject pronouns.")
        literal = "Como una manzana."
        raw = {"suggested_points": 1, "criteria": [{"criterion_id": "meaning", "points": 1,
               "evidence": literal, "feedback": "Synthetic feedback."}],
               "feedback": "Synthetic model suggestion.", "review_reasons": []}
        self.fake.response["message"]["content"] = json.dumps(raw)
        result = self.client.grade(MODEL, question, literal, expected_digest=DIGEST)
        self.assertEqual(result["suggested_points"], 1)
        self.assertEqual(result["criteria"][0]["evidence"], literal)
        self.assertEqual(result["method"], "model")

    def test_oversized_rubric_payload_is_failure_not_zero(self):
        question = Question(id="1", type="rubric", prompt="Translate: I eat an apple.", max_points=1,
                            rubric=[{"id": "meaning", "description": "Express the stated meaning.", "max_points": 1}],
                            notes="Synthetic guidance. " * 500)
        with self.assertRaisesRegex(OllamaError, "Oversized input"):
            self.client.grade(MODEL, question, "Como una manzana.", expected_digest=DIGEST)
        self.assertFalse(self.fake.calls)

    def test_cloud_tag_is_rejected_before_network(self):
        with self.assertRaises(OllamaError):
            self.client.inspect("qwen3-vl:235b-cloud")
        self.assertFalse(self.fake.calls)

    def test_remote_alias_metadata_is_rejected_recursively(self):
        for target, metadata in [("tag_extra", {"remote_model": "big"}),
                                 ("tag_extra", {"details": {"remote_host": "https://ollama.com"}}),
                                 ("show_extra", {"nested": [{"remote_host": "https://ollama.com"}]})]:
            self.fake = FakeOllama()
            self.client._request = self.fake.request
            setattr(self.fake, target, metadata)
            with self.subTest(target=target, metadata=metadata), self.assertRaises(OllamaError):
                self.call()
            self.assertNotIn("/api/chat", [path for path, _ in self.fake.calls])

    def test_missing_vision_is_failure(self):
        self.fake.capabilities = ["completion"]
        with self.assertRaisesRegex(OllamaError, "vision|image"):
            self.call(images=[base64.b64encode(b"synthetic image").decode()])

    def test_missing_local_weights_and_short_digest_are_failures(self):
        for metadata in [{"name": "other:latest"}, {"digest": "abc123"}]:
            self.fake.tag_extra = metadata
            with self.subTest(metadata=metadata), self.assertRaises(OllamaError):
                self.call()

    def test_pinned_digest_cannot_change_before_inference(self):
        with self.assertRaises(ModelDriftError):
            self.client.chat_structured(MODEL, SCHEMA, "s", "u", expected_digest="b" * 64)
        self.assertNotIn("/api/chat", [path for path, _ in self.fake.calls])

    def test_digest_and_runtime_drift_discard_results(self):
        self.fake.digest_after = "b" * 64
        with self.assertRaises(ModelDriftError):
            self.call()
        self.fake = FakeOllama()
        self.client._request = self.fake.request
        self.fake.version_after = "0.14.0"
        with self.assertRaises(ModelDriftError):
            self.call()

    def test_incomplete_malformed_wrong_schema_and_wrong_model_fail(self):
        modifications = [
            {"done": False}, {"done_reason": "length"}, {"model": "other:latest"},
            {"message": {"content": "```json\n{}\n```"}},
            {"message": {"content": '{"wrong":"field"}'}},
            {"message": {"content": '{"answer":"a","answer":"b"}'}},
            {"message": {"content": '{"answer":"x"}', "tool_calls": [{"name": "send"}]}},
            {"remote_host": "https://ollama.com"},
        ]
        for modification in modifications:
            self.fake = FakeOllama()
            self.client._request = self.fake.request
            self.fake.response.update(modification)
            with self.subTest(modification=modification), self.assertRaises(OllamaError):
                self.call()

    def test_external_schema_references_and_remote_image_urls_never_sent(self):
        with self.assertRaises(OllamaError):
            self.client.chat_structured(MODEL, {"$ref": "https://example.org/schema.json"}, "s", "u")
        with self.assertRaises(OllamaError):
            self.call(images=["https://example.org/student.png"])
        self.assertFalse(self.fake.calls)

    def test_transcription_keeps_wrong_spelling_and_has_no_answer_key(self):
        self.fake.response["message"]["content"] = json.dumps({"text": "esta", "status": "ok", "flags": []})
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / "crop.png"
            image.write_bytes(b"synthetic mock image; not used for real inference")
            result = self.client.transcribe(MODEL, image, DIGEST)
        self.assertEqual(result["text"], "esta")
        payload = next(body for path, body in self.fake.calls if path == "/api/chat")
        self.assertNotIn("teacher_question", json.dumps(payload))
        self.assertNotIn("accepted_answers", json.dumps(payload))
        self.assertEqual(payload["format"], TranscriptionResult.model_json_schema())

    def test_contradictory_transcription_is_failure(self):
        self.fake.response["message"]["content"] = json.dumps({"text": "esta", "status": "blank", "flags": []})
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / "crop.png"
            image.write_bytes(b"synthetic")
            with self.assertRaises(OllamaError):
                self.client.transcribe(MODEL, image, DIGEST)


if __name__ == "__main__":
    unittest.main()
