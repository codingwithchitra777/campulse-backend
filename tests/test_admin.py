import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.core.security import create_access_token

client = TestClient(app)


def auth_headers(user_id: str, role: str = "user") -> dict:
    token = create_access_token(user_id=user_id, role=role)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def user_id():
    return f"pytest_{uuid.uuid4().hex[:12]}"


@pytest.fixture
def admin_id():
    return f"pytest_{uuid.uuid4().hex[:12]}"


@pytest.fixture
def other_user_id():
    return f"pytest_{uuid.uuid4().hex[:12]}"


def test_admin_routes_reject_normal_user(user_id):
    headers = auth_headers(user_id, role="user")
    assert client.get("/api/admin/users", headers=headers).status_code == 403
    assert client.get("/api/admin/trades", headers=headers).status_code == 403
    assert client.get("/api/admin/stats", headers=headers).status_code == 403
    assert (
        client.patch(f"/api/admin/users/{user_id}/role", headers=headers, json={"role": "admin"}).status_code
        == 403
    )


def test_admin_can_list_users_trades_stats(admin_id):
    headers = auth_headers(admin_id, role="admin")
    assert client.get("/api/admin/users", headers=headers).status_code == 200

    trades_resp = client.get("/api/admin/trades", headers=headers)
    assert trades_resp.status_code == 200
    assert isinstance(trades_resp.json(), list)

    stats_resp = client.get("/api/admin/stats", headers=headers)
    assert stats_resp.status_code == 200
    stats = stats_resp.json()
    assert set(stats.keys()) == {"totalUsers", "totalTrades", "totalRealisedPnl"}


def test_admin_can_promote_and_demote_other_user(admin_id, other_user_id):
    # Create the target user via the demo login flow (real DB row, default role "user").
    client.post("/api/auth/demo", json={"userId": other_user_id, "userName": "Pytest Target"})

    admin_headers = auth_headers(admin_id, role="admin")

    promote_resp = client.patch(
        f"/api/admin/users/{other_user_id}/role", headers=admin_headers, json={"role": "admin"}
    )
    assert promote_resp.status_code == 200
    assert promote_resp.json()["role"] == "admin"

    users = client.get("/api/admin/users", headers=admin_headers).json()
    target = next(u for u in users if u["userId"] == other_user_id)
    assert target["role"] == "admin"

    demote_resp = client.patch(
        f"/api/admin/users/{other_user_id}/role", headers=admin_headers, json={"role": "user"}
    )
    assert demote_resp.status_code == 200
    assert demote_resp.json()["role"] == "user"


def test_admin_cannot_change_own_role(admin_id):
    headers = auth_headers(admin_id, role="admin")
    resp = client.patch(f"/api/admin/users/{admin_id}/role", headers=headers, json={"role": "user"})
    assert resp.status_code == 400


def test_promote_unknown_user_returns_404(admin_id):
    headers = auth_headers(admin_id, role="admin")
    resp = client.patch(
        f"/api/admin/users/pytest_does_not_exist/role", headers=headers, json={"role": "admin"}
    )
    assert resp.status_code == 404
