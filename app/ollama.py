"""Local-only Ollama transport with model checks before and after inference.

No model downloads, cloud APIs, automatic fallbacks, redirects, environment
proxies, or external authentication are used by this module.
"""

from __future__ import annotations

import base64
import ipaddress
import json
import math
from pathlib import Path
import re
import socket
import threading
from typing import Any
from urllib.parse import urlsplit

import httpx
from jsonschema import Draft202012Validator, ValidationError as SchemaValidationError, SchemaError

from .grading import grade_rubric_result
from .models import GradeResult, Question, TranscriptionResult
from .prompts import GRADING_PROMPT, TRANSCRIPTION_PROMPT


class OllamaError(RuntimeError):
    """An actionable, safe-to-display processing error; never a score."""


class ModelDriftError(OllamaError):
    """Model/runtime changed from the snapshot used for this inference."""


_ALLOWED_NETWORKS = tuple(ipaddress.ip_network(value) for value in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8",
    "::1/128", "fc00::/7",
))


def _local_address(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    return any(address.version == network.version and address in network
               for network in _ALLOWED_NETWORKS)


def _canonical_model(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./-]*(?::[A-Za-z0-9_.-]+)?", value):
        raise OllamaError("Use an installed local Ollama model name, not a URL.")
    if re.search(r"(?:^|[-:/])cloud(?:$|[-:/])", value, re.IGNORECASE):
        raise OllamaError("Cloud model tags are disabled. Select an installed local model.")
    return value if ":" in value.rsplit("/", 1)[-1] else value + ":latest"


def _contains_remote(value: Any) -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            if key.lower() in {"remote_model", "remote_host"} and child not in (None, ""):
                return True
            if _contains_remote(child):
                return True
    elif isinstance(value, list):
        return any(_contains_remote(child) for child in value)
    return False


def _strict_json(value: str | bytes) -> Any:
    def constant(_: str) -> None:
        raise ValueError("Non-finite JSON numbers are not supported.")

    def unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in pairs:
            if key in result:
                raise ValueError("Duplicate JSON fields are not supported.")
            result[key] = item
        return result

    return json.loads(value, parse_constant=constant, object_pairs_hook=unique_pairs)


def _validate_local_schema(schema: dict[str, Any]) -> None:
    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key in {"$ref", "$dynamicRef"} and (
                    not isinstance(item, str) or not item.startswith("#")
                ):
                    raise OllamaError("JSON schemas must not use external references.")
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)
    walk(schema)
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        raise OllamaError("The requested output schema is invalid.") from exc


class OllamaClient:
    def __init__(self, url: str, timeout: float = 180, num_ctx: int = 4096):
        if not isinstance(url, str) or any(character.isspace() for character in url):
            raise OllamaError("Ollama URL must be a local HTTP or HTTPS origin without whitespace.")
        try:
            parsed = urlsplit(url)
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
        except ValueError as exc:
            raise OllamaError("Ollama URL is invalid.") from exc
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                or parsed.username is not None or parsed.password is not None
                or parsed.query or parsed.fragment or parsed.path not in {"", "/"}
                or "%" in parsed.hostname):
            raise OllamaError("Ollama URL must be a private HTTP/HTTPS origin, without credentials, path, or query.")
        if not 1 <= port <= 65535:
            raise OllamaError("Ollama port is invalid.")
        if not math.isfinite(timeout) or timeout <= 0:
            raise OllamaError("Ollama timeout must be a positive finite number.")
        self._check_context(num_ctx)
        host = parsed.hostname
        try:
            ipaddress.ip_address(host)
            addresses = [host]
        except ValueError:
            try:
                addresses = sorted(
                    {item[4][0] for item in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)},
                    key=lambda item: (":" in item, item),
                )
            except OSError as exc:
                raise OllamaError("The configured Ollama hostname could not be resolved.") from exc
        if not addresses or not all(_local_address(address) for address in addresses):
            raise OllamaError("Ollama must resolve only to loopback, RFC1918 private IPv4, or IPv6 ULA addresses.")
        # Connect to the checked literal address, not the hostname. DNS changes
        # cannot redirect this client to a public host between check and request.
        address = addresses[0]
        authority = f"[{address}]" if ":" in address else address
        self.url = url.rstrip("/")
        self._request_origin = f"{parsed.scheme}://{authority}:{port}"
        self._host_header = parsed.netloc
        self._hostname = host
        self.timeout = timeout
        self.num_ctx = num_ctx
        self._inference_lock = threading.RLock()

    @staticmethod
    def _check_context(num_ctx: int) -> None:
        if isinstance(num_ctx, bool) or not isinstance(num_ctx, int) or not 1024 <= num_ctx <= 32768:
            raise OllamaError("Context size must be an integer between 1024 and 32768.")

    def _request(self, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        method = "GET" if payload is None else "POST"
        kwargs: dict[str, Any] = {
            "headers": {"Host": self._host_header},
            # Preserve TLS hostname verification while connecting to a pinned IP.
            "extensions": {"sni_hostname": self._hostname},
        }
        if payload is not None:
            kwargs["json"] = payload
        try:
            with httpx.Client(timeout=httpx.Timeout(self.timeout, connect=min(self.timeout, 10)),
                              follow_redirects=False, trust_env=False) as client:
                response = client.request(method, self._request_origin + path, **kwargs)
            if 300 <= response.status_code < 400:
                raise OllamaError("Ollama redirects are disabled; configure the private service directly.")
            if response.status_code >= 400:
                raise OllamaError(f"Ollama request failed (HTTP {response.status_code}). Check the service and selected model.")
            if len(response.content) > 5_000_000:
                raise OllamaError("Ollama returned an unexpectedly large response.")
            data = _strict_json(response.content)
            if not isinstance(data, dict):
                raise OllamaError("Ollama returned an invalid response object.")
            if data.get("error"):
                raise OllamaError("Ollama reported a processing error. Check its local service logs.")
            return data
        except httpx.TimeoutException as exc:
            raise OllamaError("Ollama timed out. No score was assigned; retry or enter the answer manually.") from exc
        except httpx.HTTPError as exc:
            raise OllamaError("Could not reach the configured local Ollama service. No score was assigned.") from exc
        except (ValueError, UnicodeDecodeError) as exc:
            raise OllamaError("Ollama returned invalid JSON. No score was assigned.") from exc

    def list_models(self) -> dict[str, Any]:
        version = self._request("/api/version").get("version")
        if not isinstance(version, str) or not version:
            raise OllamaError("Ollama did not report its runtime version.")
        tags = self._request("/api/tags").get("models")
        if not isinstance(tags, list):
            raise OllamaError("Ollama did not return its installed models.")
        local_models = []
        for item in tags:
            if not isinstance(item, dict) or _contains_remote(item):
                continue
            try:
                name = _canonical_model(item.get("name") or item.get("model"))
            except OllamaError:
                continue
            if not isinstance(item.get("digest"), str) or not item["digest"]:
                continue
            local_models.append({"name": name, "digest": item["digest"], "size": item.get("size"),
                                 "details": item.get("details", {})})
        return {"ok": True, "version": version, "models": local_models}

    def inspect(self, model: str, require_vision: bool = False) -> dict[str, Any]:
        requested = _canonical_model(model)
        version = self._request("/api/version").get("version")
        if not isinstance(version, str) or not version.strip():
            raise OllamaError("Ollama did not report its runtime version.")
        tags = self._request("/api/tags").get("models")
        if not isinstance(tags, list):
            raise OllamaError("Ollama did not return its installed models.")
        matching = []
        for item in tags:
            if not isinstance(item, dict):
                continue
            try:
                if _canonical_model(item.get("name") or item.get("model")) == requested:
                    matching.append(item)
            except OllamaError:
                continue
        if len(matching) != 1:
            raise OllamaError("Selected model is not uniquely installed locally. Pull it manually before grading.")
        metadata = matching[0]
        if _contains_remote(metadata):
            raise OllamaError("Selected model points to a remote host; only local model weights are allowed.")
        digest = metadata.get("digest")
        if not isinstance(digest, str) or not re.fullmatch(r"(?:sha256:)?[a-fA-F0-9]{64}", digest):
            raise OllamaError("Ollama did not return a full model digest; the batch cannot be pinned safely.")
        show = self._request("/api/show", {"model": requested})
        if _contains_remote(show):
            raise OllamaError("Selected model is remote; local-only processing refused the request.")
        capabilities = show.get("capabilities")
        if not isinstance(capabilities, list) or not all(isinstance(value, str) for value in capabilities):
            raise OllamaError("Ollama did not report model capabilities. Update Ollama and check the model.")
        if "completion" not in capabilities:
            raise OllamaError("Selected model does not support text generation.")
        if require_vision and "vision" not in capabilities:
            raise OllamaError("Selected model does not support image transcription; choose a local vision model.")
        details = show.get("details") or metadata.get("details", {})
        family = details.get("family", "") if isinstance(details, dict) else ""
        if "qwen3-vl" in requested.lower() or family == "qwen3vl":
            match = re.match(r"^v?(\d+)\.(\d+)\.(\d+)", version)
            if not match or tuple(int(part) for part in match.groups()) < (0, 12, 7):
                raise OllamaError("Qwen3-VL requires Ollama 0.12.7 or newer; use a current release for local-only controls.")
        return {"model": requested, "digest": digest, "ollama_version": version,
                "capabilities": capabilities, "details": details}

    def chat_structured(self, model: str, schema: dict[str, Any], system: str, user: str,
                        images: list[str] | None = None, expected_digest: str | None = None,
                        num_ctx: int | None = None) -> dict[str, Any]:
        # Jobs, key extraction, and synthetic capability checks share this
        # client. Serialize their complete preflight/inference/postflight span.
        with self._inference_lock:
            return self._chat_structured(model, schema, system, user, images,
                                         expected_digest, num_ctx)

    def _chat_structured(self, model: str, schema: dict[str, Any], system: str, user: str,
                         images: list[str] | None, expected_digest: str | None,
                         num_ctx: int | None) -> dict[str, Any]:
        """Use a conservative combined text budget before any server request.

        UTF-8 bytes are a practical guard for this short-response milestone,
        not an exact token count or a guarantee that text plus vision tokens
        fit the model context. Image bytes remain separately bounded. No input
        is truncated, and a budget failure never triggers another model.
        """
        context = self.num_ctx if num_ctx is None else num_ctx
        self._check_context(context)
        _validate_local_schema(schema)
        if not isinstance(system, str) or not isinstance(user, str):
            raise OllamaError("System guidance and response input must be text.")
        schema_text = json.dumps(schema, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        num_predict = min(1200, max(256, context // 3))
        text_budget_bytes = (context - num_predict - 512) * 3
        if len((system + schema_text + user).encode("utf-8")) > text_budget_bytes:
            raise OllamaError(
                "Oversized input for this short-response context budget. No request was sent and no score was assigned. "
                "Shorten the key or grading guidance, grade manually, or create an assessment with a larger context. "
                "Input was not truncated and no fallback was used."
            )
        if images is not None and (not isinstance(images, list) or any(not isinstance(item, str) for item in images)):
            raise OllamaError("Images must be locally encoded base64 strings.")
        for encoded in images or []:
            try:
                decoded = base64.b64decode(encoded, validate=True)
            except (ValueError, TypeError) as exc:
                raise OllamaError("An image is not valid local base64 data.") from exc
            if not decoded or len(decoded) > 15_000_000:
                raise OllamaError("Each cropped image must contain 1–15000000 bytes.")
        before = self.inspect(model, require_vision=bool(images))
        if expected_digest is not None and before["digest"] != expected_digest:
            raise ModelDriftError("The installed model changed from the assessment snapshot. Restore its model version or create a new assessment.")
        user_message: dict[str, Any] = {"role": "user", "content": user}
        if images:
            user_message["images"] = images
        response = self._request("/api/chat", {
            "model": before["model"],
            "messages": [{"role": "system", "content": system + "\nOutput JSON schema:\n" + schema_text}, user_message],
            "format": schema,
            "stream": False,
            "options": {"temperature": 0, "num_ctx": context, "num_predict": num_predict},
            "keep_alive": "5m",
        })
        after = self.inspect(model, require_vision=bool(images))
        if after["digest"] != before["digest"] or after["ollama_version"] != before["ollama_version"]:
            raise ModelDriftError("Model or Ollama runtime changed during inference. The suggestion was discarded.")
        if response.get("done") is not True or response.get("done_reason") in {"length", "max_tokens"}:
            raise OllamaError("Model output was incomplete or truncated. No score was assigned.")
        if _contains_remote(response):
            raise OllamaError("Ollama reported remote processing; the suggestion was discarded.")
        if "model" in response and _canonical_model(response["model"]) != before["model"]:
            raise ModelDriftError("Ollama responded using a different model; the suggestion was discarded.")
        message = response.get("message")
        if not isinstance(message, dict) or not isinstance(message.get("content"), str):
            raise OllamaError("Ollama returned no structured answer. No score was assigned.")
        if message.get("tool_calls") or message.get("role", "assistant") != "assistant":
            raise OllamaError("Unexpected model action or message type; no score was assigned.")
        try:
            result = _strict_json(message["content"])
            if not isinstance(result, dict):
                raise ValueError("Result must be an object.")
            Draft202012Validator(schema).validate(result)
        except (ValueError, SchemaValidationError) as exc:
            raise OllamaError("Model output did not match the required JSON schema. No score was assigned.") from exc
        return result

    def transcribe(self, model: str, image_path: str | Path, expected_digest: str | None = None,
                   num_ctx: int | None = None) -> dict[str, Any]:
        path = Path(image_path)
        if not path.is_file() or not 0 < path.stat().st_size <= 15_000_000:
            raise OllamaError("The selected answer crop is missing, empty, or too large.")
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        raw = self.chat_structured(
            model, TranscriptionResult.model_json_schema(), TRANSCRIPTION_PROMPT,
            "Read only the attached answer crop literally. Distinguish empty writing from an unreadable or uncertain region.",
            images=[encoded], expected_digest=expected_digest, num_ctx=num_ctx,
        )
        try:
            return TranscriptionResult.model_validate(raw).model_dump()
        except ValueError as exc:
            raise OllamaError("Transcription status contradicts its text or flags. Review the original crop; no score was assigned.") from exc

    def grade(self, model: str, question: Question, text: str, expected_digest: str | None = None,
              num_ctx: int | None = None) -> dict[str, Any]:
        if question.type != "rubric":
            raise OllamaError("Use deterministic scoring for this objective question.")
        if not isinstance(text, str) or not text.strip():
            raise OllamaError("Confirm a true blank separately; it is not sent to the grading model.")
        user = json.dumps({"teacher_question": question.model_dump(), "student_response": text}, ensure_ascii=False)
        raw = self.chat_structured(model, GradeResult.model_json_schema(), GRADING_PROMPT, user,
                                   expected_digest=expected_digest, num_ctx=num_ctx)
        try:
            return grade_rubric_result(question, text, raw)
        except ValueError as exc:
            raise OllamaError(f"Invalid grading suggestion: {exc}") from exc
