def test_health_returns_ok(client):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ready_reflects_database_connectivity(client):
    response = client.get("/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready", "database": "ok"}
