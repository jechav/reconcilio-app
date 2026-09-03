"""Retry-with-backoff at the real LLM client boundaries (issue #7, AC1)."""

import json

import httpx
import pytest

from app.extraction.llm import LiteLLMRefinementClient, OpenRouterRefiner


def test_openrouter_refiner_retries_transient_http_error_then_succeeds(monkeypatch):
    refiner = OpenRouterRefiner(api_key="key", model="test-model", base_url="https://openrouter.test")

    calls = {"n": 0}

    def fake_post(url, headers, json, timeout):
        calls["n"] += 1
        if calls["n"] < 3:
            request = httpx.Request("POST", url)
            response = httpx.Response(503, request=request)
            raise httpx.HTTPStatusError("service unavailable", request=request, response=response)
        request = httpx.Request("POST", url)
        return httpx.Response(
            200,
            request=request,
            json={"choices": [{"message": {"content": '{"fields": {}}'}}]},
        )

    monkeypatch.setattr("httpx.post", fake_post)
    monkeypatch.setattr("app.retry.time.sleep", lambda _seconds: None)

    from app.extraction.types import ExtractedField, ExtractedLine
    from app.models import ExtractionMethod

    line = ExtractedLine(line_number=1, fields={"amount": ExtractedField(value="1", confidence=0.1, method=ExtractionMethod.ocr)})
    result = refiner.refine(line, ["amount"])

    assert result == {}
    assert calls["n"] == 3


def test_openrouter_refiner_does_not_retry_client_error(monkeypatch):
    refiner = OpenRouterRefiner(api_key="key", model="test-model", base_url="https://openrouter.test")
    calls = {"n": 0}

    def fake_post(url, headers, json, timeout):
        calls["n"] += 1
        request = httpx.Request("POST", url)
        response = httpx.Response(401, request=request)
        raise httpx.HTTPStatusError("unauthorized", request=request, response=response)

    monkeypatch.setattr("httpx.post", fake_post)
    monkeypatch.setattr("app.retry.time.sleep", lambda _seconds: None)

    from app.extraction.types import ExtractedField, ExtractedLine
    from app.models import ExtractionMethod

    line = ExtractedLine(line_number=1, fields={"amount": ExtractedField(value="1", confidence=0.1, method=ExtractionMethod.ocr)})

    with pytest.raises(httpx.HTTPStatusError):
        refiner.refine(line, ["amount"])

    assert calls["n"] == 1


def test_litellm_client_retries_rate_limit_then_succeeds(monkeypatch):
    client = LiteLLMRefinementClient(model="vision-model")
    calls = {"n": 0}

    import litellm

    class FakeMessage:
        content = json.dumps({"value": "42.00", "confidence": 0.9})

    class FakeChoice:
        message = FakeMessage()

    class FakeResponse:
        choices = [FakeChoice()]

    def fake_completion(model, messages):
        calls["n"] += 1
        if calls["n"] < 2:
            raise litellm.RateLimitError("slow down", llm_provider="openrouter", model=model)
        return FakeResponse()

    monkeypatch.setattr(litellm, "completion", fake_completion)
    monkeypatch.setattr("app.retry.time.sleep", lambda _seconds: None)

    result = client.refine_field("amount", b"bytes", "application/pdf", "41.00")

    assert result.value == "42.00"
    assert calls["n"] == 2
