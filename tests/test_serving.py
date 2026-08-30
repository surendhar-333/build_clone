"""FastAPI Ops Console smoke tests (offline DuckDB; no Spark/Java needed)."""
import os
import sys
import tempfile
from pathlib import Path

import pytest

_SERVING = str(Path(__file__).resolve().parents[1] / "serving")
if _SERVING not in sys.path:
    sys.path.insert(0, _SERVING)


@pytest.fixture(scope="module")
def client():
    os.environ["DUCKDB_PATH"] = os.path.join(tempfile.mkdtemp(), "ops_test.duckdb")
    from fastapi.testclient import TestClient

    import app as appmod  # serving/app.py

    with TestClient(appmod.app) as test_client:  # triggers startup -> seed
        yield test_client


def test_healthz(client):
    assert client.get("/healthz").json()["status"] == "ok"


def test_queue_is_seeded(client):
    cases = client.get("/cases").json()
    assert len(cases) > 0
    assert {"case_id", "case_type", "amount_diff", "effective_status"} <= set(cases[0].keys())


def test_kpis_present(client):
    kpis = client.get("/kpis").json()
    for key in ("open_count", "auto_resolved_count", "manual_review_count", "sum_abs_amount_diff"):
        assert key in kpis


def test_disposition_is_idempotent_and_updates_lifecycle(client):
    import duckdb

    case_id = client.get("/cases").json()[0]["case_id"]
    idem = "pytest-idem-1"

    for _ in range(2):  # double-submit with the same idempotency key
        resp = client.post(
            f"/cases/{case_id}/disposition",
            data={"action": "escalate", "note": "needs docs", "idempotency_key": idem},
            follow_redirects=False,
        )
        assert resp.status_code == 303

    con = duckdb.connect(os.environ["DUCKDB_PATH"])
    action_rows = con.execute(
        "SELECT count(*) FROM ops_case_actions WHERE case_id = ?", [case_id]
    ).fetchone()[0]
    status = con.execute(
        "SELECT status FROM ops_case_state WHERE case_id = ?", [case_id]
    ).fetchone()[0]
    con.close()

    assert action_rows == 1  # idempotency: two posts, exactly one audit row
    assert status == "MANUAL_REVIEW"  # escalate -> MANUAL_REVIEW


def test_resolve_closes_and_drops_from_queue(client):
    cases = client.get("/cases").json()
    # pick a case not already actioned by the previous test
    target = next(c for c in cases if c["case_id"] != cases[0]["case_id"])
    cid = target["case_id"]
    resp = client.post(
        f"/cases/{cid}/disposition",
        data={"action": "resolve", "note": "ok", "idempotency_key": "pytest-idem-2"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    remaining = [c["case_id"] for c in client.get("/cases").json()]
    assert cid not in remaining  # CLOSED cases leave the active queue
