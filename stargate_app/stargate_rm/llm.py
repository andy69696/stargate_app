"""The model behind Explain and Ask.

config.LLM_ENDPOINT names either
  * a Unity Catalog function, catalog.schema.function (three parts), e.g. ubds_ai_models.default.gpt_apikey_test_payload:
    called through the app's SQL warehouse as  SELECT <function>(...)  with the arguments bound as parameters; or
  * a model serving endpoint (a plain name, e.g. databricks-claude-sonnet-4): called through the Databricks SDK.

For a function, its signature is read once with DESCRIBE FUNCTION EXTENDED and the arguments are filled by parameter name:
  a parameter called payload / request / body / json / messages gets an OpenAI-style chat request as JSON
      {"messages": [{"role": "user", "content": ...}], "max_tokens": n, "temperature": t}
  prompt / question / text / input / content / query / message gets the conversation as plain text
  max_tokens and temperature get those values; system / instructions gets an empty string; anything else NULL.
A single parameter with any other name gets the JSON request if its name suggests it, otherwise the plain text.
The result may be plain text or JSON (an OpenAI-style response, or an object with content / text / output / response);
complete() returns the text either way.  GET /api/llm-check shows the signature, the call and the raw answer.
"""
from __future__ import annotations

import json
import re
import threading
import time

from . import config, db

_FUNCTION_NAME = re.compile(r"^[A-Za-z0-9_`]+\.[A-Za-z0-9_`]+\.[A-Za-z0-9_`]+$")
_JSON_PARAMS = ("payload", "request", "body", "json", "messages")
_TEXT_PARAMS = ("prompt", "question", "text", "input", "content", "query", "message")
_sig_lock = threading.Lock()
_sig_cache: dict[str, dict] = {}


def kind(name: str | None = None) -> str:
    return "uc_function" if _FUNCTION_NAME.match(name or config.LLM_ENDPOINT) else "serving_endpoint"


# ---------------------------------------------------------------- Unity Catalog function
def signature(name: str | None = None, refresh: bool = False) -> dict:
    """{'params': [(name, type), ...], 'returns': str, 'describe': [lines]} from DESCRIBE FUNCTION EXTENDED."""
    name = name or config.LLM_ENDPOINT
    with _sig_lock:
        if not refresh and name in _sig_cache:
            return _sig_cache[name]
    df = db.query(f"DESCRIBE FUNCTION EXTENDED {name}", cached=False)
    lines = [str(v) for v in df.iloc[:, 0].tolist()] if len(df.columns) else []
    params, returns, section = [], None, None
    for ln in lines:
        m = re.match(r"^\s*([A-Za-z][A-Za-z ]*?):\s*(.*)$", ln)
        if m and not ln.startswith((" ", "\t")):
            section, rest = m.group(1).strip().lower(), m.group(2).strip()
        else:
            rest = ln.strip()
        if section == "input" and rest and rest != "()":
            p = rest.split()
            if p:
                params.append((p[0].strip("`"), " ".join(p[1:]).upper() or "STRING"))
        elif section == "returns" and rest and returns is None:
            returns = rest
    sig = {"params": params, "returns": returns, "describe": lines[:40]}
    with _sig_lock:
        _sig_cache[name] = sig
    return sig


def _as_text(messages: list[dict]) -> str:
    if len(messages) == 1:
        return messages[0]["content"]
    return "\n\n".join(f"{m['role'].upper()}: {m['content']}" for m in messages)


def _request_json(messages: list[dict], max_tokens: int, temperature: float) -> str:
    return json.dumps({"messages": messages, "max_tokens": max_tokens, "temperature": temperature}, ensure_ascii=False)


def _arguments(sig: dict, messages: list[dict], max_tokens: int, temperature: float) -> tuple[list[str], dict]:
    """Placeholders and bound values for each declared parameter, filled by name."""
    params = sig["params"] or [("payload", "STRING")]
    holders, values = [], {}
    for i, (pname, ptype) in enumerate(params):
        key, low = f"a{i}", pname.lower()
        if len(params) == 1:
            values[key] = _request_json(messages, max_tokens, temperature) if any(k in low for k in _JSON_PARAMS) or "STRUCT" in ptype or "MAP" in ptype \
                else (_request_json(messages, max_tokens, temperature) if not any(k in low for k in _TEXT_PARAMS) else _as_text(messages))
        elif any(k in low for k in _JSON_PARAMS):
            values[key] = _request_json(messages, max_tokens, temperature)
        elif any(k in low for k in _TEXT_PARAMS):
            values[key] = _as_text(messages)
        elif "max" in low and "token" in low:
            values[key] = int(max_tokens)
        elif "temp" in low:
            values[key] = float(temperature)
        elif "system" in low or "instruction" in low:
            values[key] = ""
        else:
            holders.append("NULL")
            continue
        holders.append(f":{key}")
    return holders, values


def _text_from(v) -> tuple[str, bool]:
    """Text and 'was it cut off' from whatever the function returned."""
    if v is None:
        return "", False
    if isinstance(v, (bytes, bytearray)):
        v = v.decode("utf-8", "replace")
    if isinstance(v, str):
        s = v.strip()
        if s[:1] in "{[":
            try:
                return _text_from(json.loads(s))
            except ValueError:
                pass
        return s, False
    if hasattr(v, "asDict"):
        v = v.asDict(recursive=True)
    if isinstance(v, dict):
        if v.get("error"):
            raise RuntimeError(f"the model returned an error: {str(v['error'])[:300]}")
        ch = v.get("choices")
        if isinstance(ch, list) and ch:
            c0 = ch[0] or {}
            msg = c0.get("message") or {}
            text = msg.get("content") if isinstance(msg, dict) else None
            text = text if text is not None else c0.get("text")
            return (str(text or "").strip(), c0.get("finish_reason") == "length")
        for k in ("content", "text", "output", "response", "result", "answer", "completion"):
            if k in v and v[k] is not None:
                t, cut = _text_from(v[k])
                return t, cut or v.get("finish_reason") == "length" or v.get("stop_reason") == "max_tokens"
        return json.dumps(v)[:4000], False
    if isinstance(v, list):
        parts = [_text_from(x)[0] for x in v]
        return "\n".join(p for p in parts if p), False
    return str(v), False


def _call_function(messages: list[dict], max_tokens: int, temperature: float) -> tuple[str, bool, object]:
    name = config.LLM_ENDPOINT
    sig = signature(name)
    holders, values = _arguments(sig, messages, max_tokens, temperature)
    df = db.query(f"SELECT {name}({', '.join(holders)}) AS out", cached=False, params=values)
    raw = df.iloc[0, 0] if len(df) else None
    text, cut = _text_from(raw)
    return text, cut, raw


# ---------------------------------------------------------------- serving endpoint
def _call_endpoint(messages: list[dict], max_tokens: int, temperature: float, endpoint: str | None = None) -> tuple[str, bool, object]:
    from databricks.sdk import WorkspaceClient
    from databricks.sdk.service.serving import ChatMessage, ChatMessageRole

    role = {"user": ChatMessageRole.USER, "assistant": ChatMessageRole.ASSISTANT, "system": ChatMessageRole.SYSTEM}
    msgs = [ChatMessage(role=role.get(m["role"], ChatMessageRole.USER), content=m["content"]) for m in messages]
    kw = {"max_tokens": max_tokens, "temperature": temperature}
    w = WorkspaceClient()
    for _ in range(3):     # some models (GPT-5, o-series) reject temperature or max_tokens: drop the one named and retry
        try:
            r = w.serving_endpoints.query(name=endpoint or config.LLM_ENDPOINT, messages=msgs, **kw)
            break
        except Exception as e:  # noqa: BLE001
            low = str(e).lower()
            dropped = [k for k in list(kw) if k in low and ("unsupported" in low or "not supported" in low or "invalid" in low or "does not support" in low)]
            if not dropped:
                raise
            for k in dropped:
                kw.pop(k)
    else:
        raise RuntimeError("the endpoint rejected the request parameters")
    ch = r.choices[0]
    return (ch.message.content or "").strip(), getattr(ch, "finish_reason", None) == "length", None


def visible_endpoints() -> list[dict]:
    """Serving endpoints the app's service principal can see, with what each serves (Unity Catalog model or external model)."""
    from databricks.sdk import WorkspaceClient

    out = []
    for ep in WorkspaceClient().serving_endpoints.list():
        served = []
        cfg = getattr(ep, "config", None)
        for se in (getattr(cfg, "served_entities", None) or []):
            ext = getattr(se, "external_model", None)
            served.append(getattr(se, "entity_name", None) or (f"external {getattr(ext, 'provider', '')}:{getattr(ext, 'name', '')}" if ext else getattr(se, "name", None)))
        for sm in (getattr(cfg, "served_models", None) or []):
            served.append(getattr(sm, "model_name", None))
        out.append({"name": ep.name, "task": getattr(ep, "task", None), "serves": [x for x in served if x]})
    return out


# ---------------------------------------------------------------- what LLM_ENDPOINT points at
_resolved: dict[str, dict] = {}


def resolve(name: str | None = None, refresh: bool = False) -> dict:
    """{'kind': 'uc_function' | 'serving_endpoint', 'target': ..., 'how': ...}.
    A plain name is a serving endpoint.  A catalog.schema.name is a Unity Catalog function if DESCRIBE FUNCTION finds it;
    otherwise a registered model, called through the serving endpoint that serves it."""
    name = name or config.LLM_ENDPOINT
    if not refresh and name in _resolved:
        return _resolved[name]
    if kind(name) == "serving_endpoint":
        r = {"kind": "serving_endpoint", "target": name, "how": "serving endpoint named in LLM_ENDPOINT"}
    else:
        try:
            signature(name, refresh=refresh)
            r = {"kind": "uc_function", "target": name, "how": "Unity Catalog function, called through the SQL warehouse"}
        except Exception as fe:  # noqa: BLE001 - not a function (or not visible): try a registered model behind an endpoint
            fn_err = str(fe).splitlines()[0][:300] if str(fe) else type(fe).__name__
            try:
                eps = visible_endpoints()
            except Exception as ee:  # noqa: BLE001
                raise RuntimeError(f"{name} is not a function the app can describe ({fn_err}) and the serving endpoints could not be listed "
                                   f"({str(ee).splitlines()[0][:200]})") from ee
            hits = [e["name"] for e in eps if any(str(s).lower() == name.lower() for s in e["serves"])]
            if not hits:
                raise RuntimeError(f"{name} is not a function the app can describe ({fn_err}), and none of the {len(eps)} serving endpoints the "
                                   f"app can see serves it as a model. If it is a registered model, give the app CAN_QUERY on the endpoint that "
                                   f"serves it (or set LLM_ENDPOINT to that endpoint's name)")
            r = {"kind": "serving_endpoint", "target": hits[0], "how": f"registered model {name}, served by endpoint {hits[0]}"}
    _resolved[name] = r
    return r


# ---------------------------------------------------------------- public
def complete(messages: list[dict], max_tokens: int = 1500, temperature: float = 0.2) -> tuple[str, bool]:
    """(text, truncated).  Raises on failure; the caller turns the error into a message."""
    max_tokens = max(1, min(int(max_tokens or 1500), 4000))
    r = resolve()
    if r["kind"] == "uc_function":
        text, cut, _raw = _call_function(messages, max_tokens, temperature)
    else:
        text, cut, _raw = _call_endpoint(messages, max_tokens, temperature, r["target"])
    return text, cut


def help_text() -> str:
    if kind() == "uc_function":
        return (f"LLM_ENDPOINT is {config.LLM_ENDPOINT}: as a function the app needs USE CATALOG, USE SCHEMA and EXECUTE; as a registered model, "
                "CAN_QUERY on the serving endpoint that serves it. /api/llm-check shows which it is")
    return f"check LLM_ENDPOINT ({config.LLM_ENDPOINT}) and the app's CAN_QUERY on that serving endpoint; /api/llm-check shows the details"


def check() -> dict:
    """What /api/llm-check reports: what LLM_ENDPOINT resolves to, the function signature, visible endpoints and one test call."""
    out: dict = {"llm_endpoint": config.LLM_ENDPOINT}
    if config.SAMPLE_MODE:
        out["called_as"] = kind()
        out["note"] = "sample mode: the model is not called"
        return out
    try:
        r = resolve(refresh=True)
        out["called_as"], out["target"], out["how"] = r["kind"], r["target"], r["how"]
    except Exception as e:  # noqa: BLE001
        out["called_as"] = "unresolved"
        out["resolve_error"] = str(e)[:900]
        r = None
    if r and r["kind"] == "uc_function":
        sig = signature()
        out["signature"] = {"params": sig["params"], "returns": sig["returns"], "describe": sig["describe"]}
        holders, values = _arguments(sig, [{"role": "user", "content": "Reply with the single word OK."}], 20, 0.0)
        out["call"] = {"sql": f"SELECT {config.LLM_ENDPOINT}({', '.join(holders)}) AS out", "arguments": values}
    try:
        out["visible_endpoints"] = visible_endpoints()[:60]
    except Exception as e:  # noqa: BLE001
        out["visible_endpoints_error"] = str(e).splitlines()[0][:300] if str(e) else type(e).__name__
    if r is None:
        return out
    t0 = time.time()
    try:
        if r["kind"] == "uc_function":
            text, cut, raw = _call_function([{"role": "user", "content": "Reply with the single word OK."}], 20, 0.0)
        else:
            text, cut, raw = _call_endpoint([{"role": "user", "content": "Reply with the single word OK."}], 20, 0.0, r["target"])
        out["test"] = {"ok": True, "seconds": round(time.time() - t0, 2), "text": text[:500], "truncated": cut,
                       "raw": None if raw is None else str(raw)[:1500]}
    except Exception as e:  # noqa: BLE001
        out["test"] = {"ok": False, "seconds": round(time.time() - t0, 2), "error": str(e).splitlines()[0][:600] if str(e) else type(e).__name__,
                       "help": help_text()}
    return out
