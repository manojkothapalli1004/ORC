"""Tests for backend.logging_hooks._sanitize and JsonFormatter redaction.

These never call get_orchestrator_logger (it creates handlers and log files);
they exercise _sanitize directly and JsonFormatter.format on hand-built records.
"""

from __future__ import annotations

import copy
import json
import logging

import pytest

from backend.logging_hooks import JsonFormatter, _sanitize

R = "***REDACTED***"


def _record(event: dict) -> logging.LogRecord:
    record = logging.LogRecord("t", logging.INFO, __file__, 1, "msg", None, None)
    record.event = event
    return record


def _format(event: dict) -> dict:
    return json.loads(JsonFormatter().format(_record(event)))


def test_exact_secret_keys_still_redacted():
    ev = {
        "api_key": "a",
        "OPENAI_API_KEY": "b",
        "anthropic_api_key": "c",
        "Authorization": "d",
        "token": "e",
    }
    assert _sanitize(ev) == {
        "api_key": R,
        "OPENAI_API_KEY": R,
        "anthropic_api_key": R,
        "Authorization": R,
        "token": R,
    }


@pytest.mark.parametrize(
    "key",
    [
        "apikey",
        "api-key",
        "x-api-key",
        "X-Api-Key",
        "x-authorization",
        "proxy-authorization",
        "access_token",
        "refresh_token",
        "bearer_token",
        "client_secret",
        "password",
        "db_password",
        "github_token",
        " api_key ",
    ],
)
def test_near_miss_secret_keys_redacted(key):
    out = _sanitize({key: "v"})
    assert out == {key: R}
    assert list(out.keys()) == [key]


def test_token_counters_not_redacted():
    ev = {
        "token_usage": {"input_tokens": 5, "output_tokens": 7, "total_tokens": 12},
        "max_tokens": 100,
        "token_count": 3,
    }
    assert _sanitize(ev) == ev


def test_ordinary_keys_not_redacted():
    ev = {
        "role": "builder",
        "provider": "anthropic",
        "model": "m",
        "request_id": "r1",
        "latency_ms": 12,
        "is_mock": False,
        "stop_reason": "end_turn",
        "finish_reason": "stop",
        "cost_usd": 0.01,
        "keyboard": "qwerty",
        "monkey": "banana",
    }
    assert _sanitize(ev) == ev


def test_non_string_keys_do_not_crash():
    ev = {1: "x", None: "y", ("a", "b"): "z"}
    assert _sanitize(ev) == {1: "x", None: "y", ("a", "b"): "z"}

    parsed = _format({"metadata": {1: "x"}})
    assert parsed["event"]["metadata"] == {"1": "x"}


def test_nested_and_tuple_values_redacted():
    ev = {"metadata": {"items": [{"api-key": "s"}], "pair": ({"password": "p"}, 2)}}
    out = _sanitize(ev)
    assert out["metadata"]["items"] == [{"api-key": R}]
    pair = out["metadata"]["pair"]
    assert isinstance(pair, tuple)
    assert pair[0] == {"password": R}
    assert pair[1] == 2


def test_secret_key_with_container_value_replaced_whole():
    assert _sanitize({"credentials": {"user": "u", "pass": "p"}}) == {"credentials": R}


def test_idempotent_and_no_mutation():
    ev = {
        "request_id": "r1",
        "metadata": {
            "x-api-key": "sk-live",
            "items": [{"client_secret": "cs"}, 3],
            "pair": ({"password": "p"}, "ok"),
        },
        "token_usage": {"input_tokens": 1, "output_tokens": 2, "total_tokens": 3},
    }
    before = copy.deepcopy(ev)
    s1 = _sanitize(ev)
    s2 = _sanitize(s1)
    assert s1 == s2
    assert ev == before
    assert s1 is not ev


def test_formatter_redacts_in_output():
    raw = JsonFormatter().format(_record({"request_id": "r1", "metadata": {"x-api-key": "sk-test"}}))
    assert R in raw
    assert "sk-test" not in raw
    parsed = json.loads(raw)
    assert parsed["event"]["request_id"] == "r1"
    assert parsed["event"]["metadata"]["x-api-key"] == R
