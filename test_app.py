# test_app.py
from fastapi.testclient import TestClient
from app import app

client = TestClient(app)

# ── Auth Tests ────────────────────────────────────────────────────────────────
def test_login_success():
    response = client.post("/token", data={
        "username": "admin",
        "password": "admin123"
    })
    assert response.status_code == 200
    assert "access_token" in response.json()

def test_login_wrong_password():
    response = client.post("/token", data={
        "username": "admin",
        "password": "wrongpassword"
    })
    assert response.status_code == 401

# ── Helper ────────────────────────────────────────────────────────────────────
def get_token():
    response = client.post("/token", data={
        "username": "admin",
        "password": "admin123"
    })
    return response.json()["access_token"]

# ── Protected Endpoint Tests ──────────────────────────────────────────────────
def test_query_without_token():
    response = client.post("/api/query_llm", data={"query": "hello"})
    assert response.status_code == 401

def test_query_with_token():
    token = get_token()
    response = client.post("/api/query_llm",
        headers={"Authorization": f"Bearer {token}"},
        data={"query": "hello"}
    )
    assert response.status_code == 200
    assert "result" in response.json()

def test_upload_without_token():
    response = client.post("/api/upload",
        files={"file": ("test.txt", b"content", "text/plain")},
        data={"filename": "test.txt"}
    )
    assert response.status_code == 401

def test_upload_with_token():
    token = get_token()
    response = client.post("/api/upload",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("test.txt", b"hello world", "text/plain")},
        data={"filename": "test.txt"}
    )
    assert response.status_code == 200

# ── Guard Rail Tests ──────────────────────────────────────────────────────────
def test_query_too_short():
    token = get_token()
    response = client.post("/api/query_llm",
        headers={"Authorization": f"Bearer {token}"},
        data={"query": "hi"}  # too short
    )
    assert response.status_code == 422

def test_query_too_long():
    token = get_token()
    response = client.post("/api/query_llm",
        headers={"Authorization": f"Bearer {token}"},
        data={"query": "x" * 1001}  # too long
    )
    assert response.status_code == 422