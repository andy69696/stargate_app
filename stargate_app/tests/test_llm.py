"""The model behind Explain and Ask (stargate_rm/llm.py) and the /api/sample and /api/llm-check routes.
Run:  python -m pytest tests -q
"""
import json
import os
import sys
import types

os.environ["STARGATE_SAMPLE"] = "1"

import pandas as pd  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import server  # noqa: E402
from stargate_rm import config, db, llm  # noqa: E402

MSG = [{"role": "user", "content": "Explain this"}]


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _auto_kind(monkeypatch):
    """Most tests exercise the auto-detection; the provider test sets LLM_KIND itself."""
    monkeypatch.setattr(config, "LLM_KIND", "auto")
    llm._resolved.clear(); llm._sig_cache.clear()


def test_kind():
    assert llm.kind("gpt_apikey_test") == "serving_endpoint"
    assert llm.kind("databricks-claude-sonnet-4") == "serving_endpoint"
    assert llm.kind("ubds_ai_models.default.some_function") == "uc_function"


def test_text_from_handles_common_shapes():
    assert llm._text_from("plain answer") == ("plain answer", False)
    oa = {"choices": [{"message": {"content": " So what: x "}, "finish_reason": "length"}]}
    assert llm._text_from(json.dumps(oa)) == ("So what: x", True)
    assert llm._text_from({"content": "hi"}) == ("hi", False)
    assert llm._text_from({"output": {"text": "deep"}})[0] == "deep"


def test_function_arguments_by_parameter_name():
    sig = {"params": [("payload", "STRING")], "returns": "STRING"}
    holders, values = llm._arguments(sig, MSG, 100, 0.2)
    assert holders == [":a0"] and json.loads(values["a0"])["messages"] == MSG
    sig = {"params": [("prompt", "STRING"), ("max_tokens", "INT"), ("other", "STRING")], "returns": "STRING"}
    holders, values = llm._arguments(sig, MSG, 100, 0.2)
    assert holders == [":a0", ":a1", "NULL"] and values == {"a0": "Explain this", "a1": 100}


def test_function_call(monkeypatch):
    monkeypatch.setattr(config, "LLM_ENDPOINT", "ubds_ai_models.default.fn")
    calls = []

    def fake_query(sql_text, cached=True, params=None):
        calls.append((sql_text, params))
        if sql_text.startswith("DESCRIBE"):
            return pd.DataFrame({"function_desc": ["Function: ubds_ai_models.default.fn", "Type:     SCALAR", "Input:    payload STRING", "Returns:  STRING"]})
        return pd.DataFrame({"out": [json.dumps({"choices": [{"message": {"content": "OK"}}]})]})
    monkeypatch.setattr(db, "query", fake_query)
    llm._sig_cache.clear(); llm._resolved.clear()
    assert llm.complete(MSG) == ("OK", False)
    assert calls[-1][0] == "SELECT ubds_ai_models.default.fn(:a0) AS out"


def _fake_sdk(monkeypatch, wc):
    sdk = types.ModuleType("databricks.sdk")
    sdk.WorkspaceClient = wc
    serving = types.ModuleType("databricks.sdk.service.serving")
    serving.ChatMessage = lambda role, content: {"role": role, "content": content}
    serving.ChatMessageRole = types.SimpleNamespace(USER="user", ASSISTANT="assistant", SYSTEM="system")
    monkeypatch.setitem(sys.modules, "databricks.sdk", sdk)
    monkeypatch.setitem(sys.modules, "databricks.sdk.service.serving", serving)


def test_registered_model_resolves_to_its_endpoint(monkeypatch):
    monkeypatch.setattr(config, "LLM_ENDPOINT", "ubds_ai_models.default.gpt_apikey_test")

    def no_function(sql_text, cached=True, params=None):
        raise RuntimeError("[ROUTINE_NOT_FOUND] The routine ubds_ai_models.default.gpt_apikey_test cannot be found.")
    monkeypatch.setattr(db, "query", no_function)
    asked = []

    class Endpoints:
        def list(self):
            ent = types.SimpleNamespace(entity_name="ubds_ai_models.default.gpt_apikey_test", external_model=None, name="v1")
            other = types.SimpleNamespace(entity_name=None, external_model=types.SimpleNamespace(provider="openai", name="gpt-4o"), name="x")
            return [types.SimpleNamespace(name="other-ep", task="llm/v1/chat", config=types.SimpleNamespace(served_entities=[other], served_models=None)),
                    types.SimpleNamespace(name="gpt-ep", task=None, config=types.SimpleNamespace(served_entities=[ent], served_models=None))]

        def query(self, name, messages, **kw):
            asked.append(name)
            msg = types.SimpleNamespace(content="OK")
            return types.SimpleNamespace(choices=[types.SimpleNamespace(message=msg, finish_reason="stop")])

    class WC:
        serving_endpoints = Endpoints()
    _fake_sdk(monkeypatch, WC)
    llm._sig_cache.clear(); llm._resolved.clear()
    assert llm.resolve()["target"] == "gpt-ep"
    assert llm.complete(MSG) == ("OK", False) and asked == ["gpt-ep"]


def test_endpoint_retries_without_rejected_parameter(monkeypatch):
    monkeypatch.setattr(config, "LLM_ENDPOINT", "gpt_apikey_test")
    seen = []

    class Endpoints:
        def query(self, name, messages, **kw):
            seen.append((name, dict(kw)))
            if "temperature" in kw:
                raise RuntimeError("Unsupported value: 'temperature' does not support 0.2 with this model.")
            msg = types.SimpleNamespace(content="So what: fine")
            return types.SimpleNamespace(choices=[types.SimpleNamespace(message=msg, finish_reason="stop")])

    class WC:
        serving_endpoints = Endpoints()

    _fake_sdk(monkeypatch, WC)
    llm._resolved.clear()
    assert llm.complete(MSG, max_tokens=50) == ("So what: fine", False)
    assert seen[0][0] == "gpt_apikey_test" and "temperature" in seen[0][1] and "temperature" not in seen[1][1] and seen[1][1]["max_tokens"] == 50


def test_sample_route_uses_llm(monkeypatch):
    c = TestClient(server.app)
    monkeypatch.setattr(config, "SAMPLE_MODE", False)
    monkeypatch.setattr(llm, "complete", lambda msgs, max_tokens=1500: ("So what: x\nRecommended action: y", False))
    assert c.post("/api/sample", json={"input": "hi"}).json() == {"text": "So what: x\nRecommended action: y", "truncated": False}

    def boom(msgs, max_tokens=1500):
        raise RuntimeError("PERMISSION_DENIED: no CAN_QUERY")
    monkeypatch.setattr(llm, "complete", boom)
    r = c.post("/api/sample", json={"input": "hi"})
    assert r.status_code == 503 and "PERMISSION_DENIED" in r.json()["error"] and "gpt_apikey_test" in r.json()["error"]


def test_llm_check_route():
    r = TestClient(server.app).get("/api/llm-check").json()
    assert r["llm_endpoint"] == config.LLM_ENDPOINT == "ubds_ai_models.default.gpt_apikey_test" and r["called_as"] == "uc_function"


def test_model_provider_service(monkeypatch):
    monkeypatch.setattr(config, "LLM_KIND", "model_provider_service")
    monkeypatch.setattr(config, "LLM_ENDPOINT", "ubds_ai_models.default.gpt_apikey_test")
    monkeypatch.setattr(config, "LLM_MODEL", "gpt-5-mini")
    posts = []

    class Resp:
        def __init__(self, code, data):
            self.status_code, self._d = code, data
            self.ok = code == 200
            self.text = json.dumps(data)

        def json(self):
            return self._d

    def post(url, headers, json, timeout):  # noqa: A002 - mirrors requests.post
        posts.append((url, dict(headers), dict(json)))
        if "max_tokens" in json:
            return Resp(400, {"error": {"message": "Unsupported parameter: 'max_tokens'. Use 'max_completion_tokens' instead."}})
        if "temperature" in json:
            return Resp(400, {"error": {"message": "Unsupported value: 'temperature' does not support 0.2."}})
        return Resp(200, {"choices": [{"message": {"content": "So what: ok"}, "finish_reason": "stop"}]})
    monkeypatch.setitem(sys.modules, "requests", types.SimpleNamespace(post=post))
    cfg = types.SimpleNamespace(host="https://adb-1.azuredatabricks.net/", authenticate=lambda: {"Authorization": "Bearer t"})
    _fake_sdk(monkeypatch, type("WC", (), {"__init__": lambda self: setattr(self, "config", cfg)}))
    llm._resolved.clear()
    assert llm.complete(MSG, max_tokens=50) == ("So what: ok", False)
    url, headers, body = posts[-1]
    assert url == "https://adb-1.azuredatabricks.net/ai-gateway/openai/v1/chat/completions"
    assert headers["Databricks-Model-Provider-Service"] == "ubds_ai_models.default.gpt_apikey_test" and headers["Authorization"] == "Bearer t"
    assert body["model"] == "gpt-5-mini" and body["max_completion_tokens"] == 50 and "temperature" not in body and body["messages"] == MSG


def test_three_part_name_matches_endpoint_named_after_it(monkeypatch):
    monkeypatch.setattr(config, "LLM_ENDPOINT", "ubds_ai_models.default.gpt_apikey_test")

    def no_function(sql_text, cached=True, params=None):
        raise RuntimeError("[UNRESOLVED_ROUTINE] Cannot resolve routine")
    monkeypatch.setattr(db, "query", no_function)

    class Endpoints:
        def list(self):
            ext = types.SimpleNamespace(entity_name=None, external_model=types.SimpleNamespace(provider="openai", name="gpt-4o"), name="gpt")
            return [types.SimpleNamespace(name="databricks-gpt-oss-120b", task="llm/v1/chat", config=None),
                    types.SimpleNamespace(name="gpt-apikey-test", task="llm/v1/chat", config=types.SimpleNamespace(served_entities=[ext], served_models=None))]
    _fake_sdk(monkeypatch, type("WC", (), {"serving_endpoints": Endpoints()}))
    llm._sig_cache.clear(); llm._resolved.clear()
    r = llm.resolve()
    assert r["target"] == "gpt-apikey-test" and r["kind"] == "serving_endpoint"
