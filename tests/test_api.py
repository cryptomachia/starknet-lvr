"""Tests for the P5 dashboard API."""
import os
import sys
from pathlib import Path
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

# Use the repo's data/ directory for the file-backed source.
os.environ["LVR_LAB_DATA_DIR"] = str(Path(__file__).resolve().parents[1] / "data")

try:
    from fastapi.testclient import TestClient
    from lvr_lab.api.dashboard import app
    HAS_FASTAPI = (app is not None)
except ImportError:
    HAS_FASTAPI = False


pytestmark = pytest.mark.skipif(not HAS_FASTAPI, reason="fastapi not installed")


@pytest.fixture
def client():
    from lvr_lab.api.dashboard import app
    return TestClient(app)


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "version" in body


def test_metrics_endpoint(client):
    r = client.get("/metrics")
    assert r.status_code == 200


def test_root_redirects_to_pools(client):
    r = client.get("/", follow_redirects=False)
    assert r.status_code in (307, 308)
    assert r.headers["location"].endswith("/pools")


def test_list_pools_returns_data(client):
    r = client.get("/pools")
    assert r.status_code == 200
    body = r.json()
    assert "count" in body
    assert isinstance(body["pools"], list)


def test_unknown_pool_returns_404(client):
    r = client.get("/pools/nonexistent")
    assert r.status_code == 404


def test_cross_amm_panel(client):
    r = client.get("/cross_amm/wedge_panel")
    assert r.status_code == 200
    body = r.json()
    assert "ekubo" in body
    assert "uniswap_v3" in body
    assert isinstance(body["ekubo"], list)


def test_openapi_schema_present(client):
    r = client.get("/openapi.json")
    assert r.status_code == 200
    schema = r.json()
    assert schema["info"]["title"] == "LVR Lab Dashboard API"
    assert "paths" in schema and "/health" in schema["paths"]


def test_known_pool_metadata(client):
    """If we have wedge_timeseries.csv, at least one pool should resolve."""
    r = client.get("/pools")
    pools = r.json()["pools"]
    if not pools:
        pytest.skip("no pool data — run scripts/run_backtest.py first")
    pool_id = pools[0]["pool_id"]
    r = client.get(f"/pools/{pool_id}")
    assert r.status_code == 200
    assert r.json()["pool_id"] == pool_id
