"""Stargate Workspace v2 on Databricks Apps (FastAPI).

Serves the Stargate Workspace v2 artifact's own pages from web/ unchanged, so the UI is identical to the artifact, and
answers the data the pages fetch from the lake:

  live from the lake (built by stargate_rm.pipeline, cached one hour):
    dash/rm/data.json             Resource manager: utilisation, margin           (legacy/rm_build.py)
    dash/resourcing/data.json     Resourcing & margin: placeholders, named, months (legacy/resourcing_refresh.py)
    dash/resourcing/match.json    matcher: people, roles, matches                   (legacy/match.py)
    dash/resourcing/history.json  weekly placeholder history (app-state table)
    the "gold" data embedded in index.html, swapped in as the page is served       (stargate_rm/gold)
    dash/rm/demand.json           demand by family, from the same gold build
  snapshot, as published in the artifact (no lake build yet):
    dash/revenue/*, dash/resrev/*, the pricing page's own files
  If the gold build itself errors (not a single failed query, which is a data gap), index.html keeps its published snapshot
  and /api/status says so.

The claude.ai artifact runtime the pages call (claude.use: user, db, sample, downloads) is provided by web_runtime/claude.js,
injected into each page's <head> as it is served, backed by the /api routes below.

Run locally:   STARGATE_SAMPLE=1 uvicorn server:app --port 8000
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
import threading
import time
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response

from stargate_rm import config, db, pipeline
from stargate_rm.workspace_store import store

HERE = Path(__file__).resolve().parent
WEB = HERE / "web"
RUNTIME_JS = HERE / "web_runtime" / "claude.js"
SHIM_TAG = b'<script src="/_stargate/claude.js"></script>'
BUILD_TTL = int(os.environ.get("STARGATE_BUILD_TTL", "3600"))

log = logging.getLogger("stargate")
app = FastAPI(title="Stargate Workspace", docs_url=None, redoc_url=None, openapi_url=None)


# ---------------------------------------------------------------- live data build (one at a time, cached)
class Build:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.data: dict | None = None
        self.at = 0.0
        self.error: str | None = None
        self.seconds: float | None = None

    def get(self) -> dict:
        with self.lock:
            if self.data is not None and time.time() - self.at < BUILD_TTL:
                return self.data
            t0 = time.time()
            try:
                self.data = pipeline.build_data()
                self.error = None
            except Exception as e:  # noqa: BLE001
                log.exception("lake build failed")
                self.error = f"{type(e).__name__}: {str(e).splitlines()[0][:300] if str(e) else ''}"
                if self.data is None:
                    raise
            finally:
                self.seconds = round(time.time() - t0, 1)
            self.at = time.time()
            return self.data

    def clear(self) -> None:
        with self.lock:
            self.data = None
            self.at = 0.0
        db.clear_cache()


build = Build()

# path served -> key in the build result
LIVE = {
    "dash/rm/data.json": "rm",
    "dash/resourcing/data.json": "res",
    "dash/resourcing/match.json": "match",
    "dash/resourcing/history.json": "history",
    "dash/rm/demand.json": "rm_demand",
}
GOLD_TAG = b'<script id="gold" type="application/json">'
_index_cache: dict = {"key": None, "html": None}


def _live_json(key: str, path: str) -> Response:
    try:
        data = build.get()
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"error": f"lake build failed: {e}"}, status_code=503, headers={"Cache-Control": "no-store"})
    payload = data.get(key)
    if payload is None:   # sample mode keeps no history: fall back to the published snapshot
        return _static(path)
    return Response(json.dumps(payload, default=str, separators=(",", ":"), ensure_ascii=False), media_type="application/json", headers={"Cache-Control": "no-store", "X-Stargate-Source": "lake"})


# ---------------------------------------------------------------- user identity (Databricks Apps forwards the signed-in user)
def _user(request: Request) -> dict:
    h = request.headers
    email = h.get("x-forwarded-email") or h.get("x-forwarded-preferred-username") or os.environ.get("STARGATE_USER")
    name = h.get("x-forwarded-preferred-username") or email
    return {"id": email, "name": name}


@app.get("/api/me")
def me(request: Request) -> dict:
    return _user(request)


# ---------------------------------------------------------------- status and rebuild
@app.get("/api/status")
def status() -> dict:
    data = build.data
    return {
        "mode": "sample" if config.SAMPLE_MODE else "live",
        "kantata": f"{config.KANTATA_CATALOG}.{config.KANTATA_SCHEMA}",
        "build": {
            "built": dt.datetime.fromtimestamp(build.at, dt.timezone.utc).isoformat() if build.at else None,
            "seconds": build.seconds,
            "error": build.error,
            "gaps": (data or {}).get("gaps", []),
            "rm_people": len(((data or {}).get("rm") or {}).get("rows", [])) if data else None,
            "gold": _gold_status(data),
            "live_files": list(LIVE),
        },
        "store": store.status(),
        "llm": {"enabled": True, "endpoint": config.LLM_ENDPOINT, "sample_mode": config.SAMPLE_MODE},
    }


@app.get("/api/diagnose")
def diagnose(download: int = 0) -> Response:
    """Fresh build with every query observed plus probe queries (stargate_rm/diagnose.py).  Takes as long as a build, a minute or two."""
    from stargate_rm import diagnose as diag

    with build.lock:
        report, data = diag.run()
        if data and not report["build"]["error"]:
            build.data, build.at, build.error, build.seconds = data, time.time(), None, report["build"]["seconds"]
            _index_cache.update(key=None, html=None)
    headers = {"Cache-Control": "no-store"}
    if download:
        headers["Content-Disposition"] = f'attachment; filename="stargate-diagnose-{report["today"]}.json"'
    return Response(json.dumps(report, default=str, indent=1, ensure_ascii=False), media_type="application/json", headers=headers)


@app.post("/api/rebuild")
def rebuild() -> dict:
    build.clear()
    build.get()
    return status()


# ---------------------------------------------------------------- shared workspace db (Firestore-style, see web_runtime/claude.js)
@app.get("/api/db")
def db_all() -> Response:
    return Response(json.dumps({"collections": store.all()}), media_type="application/json", headers={"Cache-Control": "no-store"})


@app.put("/api/db/{collection}/{doc_id}")
def db_set(collection: str, doc_id: str, request: Request, body: dict = Body(...)) -> dict:
    try:
        store.set(collection, doc_id, body.get("data"), _user(request)["id"])
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(503, f"save failed: {str(e).splitlines()[0][:200]}") from e
    return {"ok": True}


@app.delete("/api/db/{collection}/{doc_id}")
def db_delete(collection: str, doc_id: str) -> dict:
    try:
        store.delete(collection, doc_id)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:  # noqa: BLE001
        raise HTTPException(503, f"delete failed: {str(e).splitlines()[0][:200]}") from e
    return {"ok": True}


# ---------------------------------------------------------------- Explain / Ask (the artifact's 'sample' capability)
@app.post("/api/sample")
def sample(body: dict = Body(...)) -> Response:
    raw = body.get("input")
    if isinstance(raw, str):
        msgs = [{"role": "user", "content": raw}]
    elif isinstance(raw, list):
        msgs = [{"role": "assistant" if m.get("role") == "assistant" else "user", "content": str(m.get("content", ""))} for m in raw if isinstance(m, dict)]
    else:
        return JSONResponse({"error": "input must be text or a list of messages", "code": "bad_request"}, status_code=400)
    if not msgs:
        return JSONResponse({"error": "empty input", "code": "bad_request"}, status_code=400)
    if config.SAMPLE_MODE:
        return JSONResponse({"text": "So what: (sample mode) the model is not called; the executive summary appears here.\n"
                                     "Recommended action: (sample mode) the recommended action appears here.", "truncated": False})
    max_tokens = int(body.get("max_tokens") or 1500)
    try:
        from databricks.sdk import WorkspaceClient
        from databricks.sdk.service.serving import ChatMessage, ChatMessageRole

        w = WorkspaceClient()
        role = {"user": ChatMessageRole.USER, "assistant": ChatMessageRole.ASSISTANT}
        r = w.serving_endpoints.query(name=config.LLM_ENDPOINT, messages=[ChatMessage(role=role[m["role"]], content=m["content"]) for m in msgs],
                                      max_tokens=min(max_tokens, 4000), temperature=0.2)
        ch = r.choices[0]
        return JSONResponse({"text": (ch.message.content or "").strip(), "truncated": getattr(ch, "finish_reason", None) == "length"})
    except Exception as e:  # noqa: BLE001
        log.exception("model call failed")
        return JSONResponse({"error": f"Explain is not available: {str(e).splitlines()[0][:200]} (check LLM_ENDPOINT and the app's CAN_QUERY grant)",
                             "code": "sampling_disabled"}, status_code=503)


# ---------------------------------------------------------------- pages and files
@app.get("/_stargate/claude.js")
def runtime_js() -> Response:
    return FileResponse(RUNTIME_JS, media_type="application/javascript", headers={"Cache-Control": "no-cache"})


def _gold_status(data: dict | None) -> dict:
    if not data:
        return {"source": "not built yet"}
    g = data.get("gold")
    if not g:
        return {"source": "snapshot (published 4 Nov 2026)", "reason": data.get("gold_error") or ("sample mode" if config.SAMPLE_MODE else "not built")}
    return {"source": "lake", "built": g["meta"]["built"], "people": len(g["people"]), "engagements": len(g["engagements"]), "deals": len(g["deals"]),
            "exceptions": len(g["exceptions"]), "proposals": len(g["proposals"])}


def _with_gold(html: bytes) -> bytes:
    """Replace the JSON inside <script id="gold"> with the lake build (cached per build)."""
    try:
        data = build.get()
    except Exception:  # noqa: BLE001 - the page still works on its snapshot
        return html
    gold = data.get("gold")
    if not gold:
        return html
    key = id(gold)
    if _index_cache["key"] != key:
        i = html.find(GOLD_TAG)
        j = html.find(b"</script>", i) if i >= 0 else -1
        if i < 0 or j < 0:
            log.error("index.html has no <script id=gold> block; serving the snapshot")
            return html
        payload = json.dumps(gold, default=str, separators=(",", ":"), ensure_ascii=False).replace("</", "<\\/").encode("utf-8")
        _index_cache.update(key=key, html=html[: i + len(GOLD_TAG)] + payload + html[j:])
    return _index_cache["html"]


def _inject(html: bytes) -> bytes:
    i = html.lower().find(b"<head>")
    if i >= 0:
        return html[: i + 6] + SHIM_TAG + html[i + 6:]
    return SHIM_TAG + html


def _static(path: str) -> Response:
    target = (WEB / path).resolve()
    if WEB.resolve() not in target.parents and target != WEB.resolve():
        raise HTTPException(404)
    if target.is_dir():
        target = target / "index.html"
    if not target.is_file():
        raise HTTPException(404)
    if target.suffix == ".html":
        html = target.read_bytes()
        if target == (WEB / "index.html").resolve():
            html = _with_gold(html)
        return Response(_inject(html), media_type="text/html; charset=utf-8", headers={"Cache-Control": "no-cache"})
    return FileResponse(target, headers={"Cache-Control": "no-cache", "X-Stargate-Source": "snapshot"})


@app.get("/{path:path}")
def files(path: str) -> Response:
    path = path.strip("/")
    if path in LIVE:
        return _live_json(LIVE[path], path)
    return _static(path or "index.html")
