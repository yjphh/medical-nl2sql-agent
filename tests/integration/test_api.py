from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health(mirror_settings):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_create_session():
    response = client.post("/sessions", json={"user_id": "u1"})
    assert response.status_code == 200
    assert "session_id" in response.json()


def test_metrics_catalog():
    response = client.get("/metrics")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] >= 35
    ids = {item["id"] for item in body["items"]}
    assert "outpatient_visits" in ids
    item = next(i for i in body["items"] if i["id"] == "outpatient_visits")
    assert item["name"] == "门诊人次"
    assert item["category"]


def test_metrics_search_and_limit():
    response = client.get("/metrics", params={"q": "心内科"})
    assert response.status_code == 200
    body = response.json()
    assert body["total"] <= 35

    response = client.get("/metrics", params={"top_k": 3})
    assert len(response.json()["items"]) == 3

    response = client.get("/metrics", params={"top_k": 0})
    assert response.status_code == 422
