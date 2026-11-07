"""FastAPI server (Stargate Workspace v2) in sample mode: pages served unchanged, live JSON in the artifact's shapes, runtime routes.
Run:  python -m pytest tests -q
"""
import json
import os
from pathlib import Path

os.environ["STARGATE_SAMPLE"] = "1"

from fastapi.testclient import TestClient  # noqa: E402

import server  # noqa: E402

WEB = Path(server.__file__).resolve().parent / "web"
client = TestClient(server.app)


def test_pages_are_the_artifact_pages_plus_the_runtime_tag():
    for page in ["index.html", "dash/rm/index.html", "dash/resourcing/index.html", "dash/revenue/index.html", "dash/resrev/index.html", "dash/pricing/index.html"]:
        r = client.get("/" + page)
        assert r.status_code == 200
        assert r.content.count(server.SHIM_TAG) == 1
        assert r.content.replace(server.SHIM_TAG, b"", 1) == (WEB / page).read_bytes()
    assert client.get("/").content == client.get("/index.html").content


def test_live_json_has_the_artifact_shape():
    for path in server.LIVE:
        r = client.get("/" + path)
        assert r.status_code == 200
        snap = json.loads((WEB / path).read_text())
        if isinstance(snap, dict):
            assert set(r.json()) == set(snap), path


def test_runtime_routes():
    assert client.get("/_stargate/claude.js").status_code == 200
    assert client.put("/api/db/approvals/T1", json={"data": {"status": "approved"}}).json() == {"ok": True}
    assert client.get("/api/db").json()["collections"]["approvals"]["T1"] == {"status": "approved"}
    assert client.delete("/api/db/approvals/T1").json() == {"ok": True}
    assert "T1" not in client.get("/api/db").json()["collections"].get("approvals", {})
    assert client.put("/api/db/a%2Fb/x", json={"data": 1}).status_code in (400, 404, 405)
    assert client.post("/api/sample", json={"input": "hi"}).json()["text"]
    assert client.get("/api/status").json()["mode"] == "sample"


def test_no_path_traversal():
    assert client.get("/../server.py").status_code == 404
    assert client.get("/%2e%2e/server.py").status_code == 404


def test_index_serves_lake_gold_when_built(monkeypatch):
    gold = {"meta": {"built": "2026-11-06", "months": []}, "people": [], "note": "</script> stays inside the JSON"}
    monkeypatch.setattr(server.build, "get", lambda: {"gold": gold, "rm_demand": {"rows": [1]}})
    server._index_cache.update(key=None, html=None)
    html = client.get("/").content
    i = html.find(server.GOLD_TAG) + len(server.GOLD_TAG)
    body = html[i: html.find(b"</script>", i)]
    assert json.loads(body.decode().replace("<\\/", "</")) == gold
    assert client.get("/dash/rm/demand.json").json() == {"rows": [1]}
    server._index_cache.update(key=None, html=None)
