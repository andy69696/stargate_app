"""Smoke test: the app renders every tab on the bundled sample data (no warehouse needed).
Run:  STARGATE_SAMPLE=1 python -m pytest tests -q
"""
import os
os.environ["STARGATE_SAMPLE"] = "1"

from streamlit.testing.v1 import AppTest


def test_all_tabs_render():
    at = AppTest.from_file("app.py", default_timeout=120).run()
    assert not at.exception, at.exception
    text = " ".join(m.value for m in at.markdown)
    for word in ("Resourcing exceptions", "Utilisation", "Demand", "Margin", "Purpose", "Objective"):
        assert word in text


def test_exception_rules_run_on_sample():
    from stargate_rm import pipeline, exceptions
    data = pipeline._sample()
    found = exceptions.run(data)
    assert any(f["rule"] == "X27" for f in found)
    assert all({"id", "subject", "detail", "due"} <= set(f) for f in found)
