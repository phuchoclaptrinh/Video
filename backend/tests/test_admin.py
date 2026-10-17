# ruff: noqa: F811 - pytest injects the imported `client` fixture by parameter name.
import time

from sqlalchemy import insert, select

from app.services.platform import story_batches
from app.services.platform_shared import users
from app.services.video_execution_store import executions
from tests.test_platform import client, register  # noqa: F401 - shared fixture

ADMIN, MEMBER = "boss@example.com", "member@example.com"


def login(client, email):
    return client.post("/api/v1/auth/login", json={"email": email, "password": "test-password-123"})


def user_id(client, email):
    with client.platform.engine.connect() as db:
        return db.execute(select(users.c.id).where(users.c.email == email)).scalar_one()


def test_without_admin_emails_every_account_keeps_full_access(client, monkeypatch):
    monkeypatch.delenv("ADMIN_EMAILS", raising=False)
    register(client, MEMBER)
    assert client.get("/api/v1/me").json()["is_admin"] is True
    assert client.get("/api/v1/admin/users").status_code == 200


def test_only_listed_admins_reach_the_console_and_change_keys(client, monkeypatch):
    monkeypatch.setenv("ADMIN_EMAILS", "Boss@example.com, other@example.com")
    register(client, MEMBER)
    assert client.get("/api/v1/me").json() == {"id": user_id(client, MEMBER), "email": MEMBER, "is_admin": False}
    for method, path in (("GET", "/api/v1/admin/overview"), ("GET", "/api/v1/admin/videos"),
                         ("GET", "/api/v1/admin/users"), ("GET", "/api/v1/seedvis/keys")):
        assert client.request(method, path).status_code == 403
    capacity = client.get("/api/v1/seedvis/capacity")
    assert capacity.status_code == 200 and set(capacity.json()) == {"keys", "total_slots", "active", "waiting"}
    assert client.put("/api/v1/planner-credential", json={"api_key": "member-cannot-set"}).status_code == 403
    assert client.put("/api/v1/provider-credential?provider=poyo", json={"api_key": "member-cannot-set"}).status_code == 403
    register(client, ADMIN)
    assert client.get("/api/v1/me").json()["is_admin"] is True
    overview = client.get("/api/v1/admin/overview").json()
    assert overview["users"] == 2 and len(overview["daily"]) == 14


def test_admin_sees_every_video_cancels_one_and_manages_accounts(client, monkeypatch):
    monkeypatch.setenv("ADMIN_EMAILS", ADMIN)
    register(client, MEMBER)
    member = user_id(client, MEMBER)
    now = time.time()
    with client.platform.engine.begin() as db:
        db.execute(insert(executions).values(
            generation_id="scene-1", fingerprint="f", provider="seedvis", status="GENERATING",
            payload={"spec": {"request": {"user_id": member}}}, provider_task_id="task", result={"credential": "keya"},
            attempts=0, cancel_requested=False, created_at=now, run_started_at=now, next_run_at=now + 60, lease_until=0,
        ))
        for batch, status, error in (("a" * 32, "GENERATING", None), ("b" * 32, "FAILED", "native_speech_quality_failed: Cảnh 2")):
            db.execute(insert(story_batches).values(
                id=batch, user_id=member, title="Bộ đồ hè cho bé", draft_id=None, ratio="9:16",
                generation_ids=["scene-1"] if status == "GENERATING" else [], status=status, error_code=error,
                audio_mode="native", created=now,
            ))
    register(client, ADMIN)
    active = client.get("/api/v1/admin/videos").json()
    assert [video["id"] for video in active] == ["a" * 32]
    assert active[0]["user"] == MEMBER and active[0]["scene_status"] == "GENERATING" and active[0]["credential"] == "keya"
    queue = client.get("/api/v1/admin/queue").json()
    assert queue[0]["user"] == MEMBER and queue[0]["credential"] == "keya"
    overview = client.get("/api/v1/admin/overview").json()
    assert overview["failed_week"] == 1 and overview["top_errors"] == [{"code": "native_speech_quality_failed", "count": 1}]
    assert client.post(f"/api/v1/admin/videos/{'a' * 32}/cancel").json()["status"] == "CANCELLED"
    assert client.get("/api/v1/admin/videos").json() == []
    assert client.platform.store.get("scene-1")["cancel_requested"]
    assert client.post(f"/api/v1/admin/videos/{'b' * 32}/cancel").status_code == 409

    admin = user_id(client, ADMIN)
    assert client.patch(f"/api/v1/admin/users/{admin}", json={"disabled": True}).status_code == 409
    row = client.patch(f"/api/v1/admin/users/{member}", json={"is_admin": True}).json()
    assert row["is_admin"] and not row["admin_from_env"] and row["projects"] == 0
    assert client.patch(f"/api/v1/admin/users/{member}", json={"disabled": True}).json()["disabled"] is True
    assert login(client, MEMBER).status_code == 403
    assert client.patch(f"/api/v1/admin/users/{member}", json={"disabled": False}).json()["disabled"] is False
    assert login(client, MEMBER).status_code == 200
    assert client.get("/api/v1/me").json()["is_admin"] is True


def test_existing_database_gains_admin_columns_without_losing_accounts(tmp_path):
    import sqlite3

    from app.services.platform import Platform

    path = tmp_path / "old.sqlite"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE platform_users (id VARCHAR PRIMARY KEY, email VARCHAR UNIQUE, password VARCHAR)")
        db.execute("INSERT INTO platform_users VALUES ('u1', 'old@example.com', 'x:y')")
    platform = Platform("sqlite:///" + str(path), tmp_path / "assets", "http://example.test", "test-key")
    try:
        with platform.engine.connect() as db:
            row = db.execute(select(users).where(users.c.id == "u1")).mappings().one()
        assert row["email"] == "old@example.com" and row["is_admin"] == 0 and row["disabled"] == 0
    finally:
        platform.http.close()
        platform.engine.dispose()


def test_sign_in_response_says_whether_to_open_the_admin_console(client, monkeypatch):
    monkeypatch.setenv("ADMIN_EMAILS", ADMIN)
    member = register(client, MEMBER)
    assert member.json() == {"id": user_id(client, MEMBER), "email": MEMBER, "is_admin": False}
    assert register(client, ADMIN).json()["is_admin"] is True
    assert login(client, MEMBER).json()["is_admin"] is False
    assert login(client, ADMIN).json() == {"id": user_id(client, ADMIN), "email": ADMIN, "is_admin": True}


def test_admin_downloads_a_consistent_database_backup(client, monkeypatch, tmp_path):
    import sqlite3

    monkeypatch.setenv("ADMIN_EMAILS", ADMIN)
    register(client, MEMBER)
    assert client.get("/api/v1/admin/backup").status_code == 403
    register(client, ADMIN)
    response = client.get("/api/v1/admin/backup")
    assert response.status_code == 200
    assert "vo3-backup-" in response.headers["content-disposition"]
    copy = tmp_path / "copy.sqlite3"
    copy.write_bytes(response.content)
    with sqlite3.connect(copy) as db:
        emails = {row[0] for row in db.execute("select email from platform_users")}
    assert emails == {MEMBER, ADMIN}
    # The temporary snapshot is removed once it has been sent.
    assert not list((client.platform.storage / "backups").glob("snapshot-*"))


def test_only_admins_create_accounts_and_reset_passwords(client, monkeypatch):
    monkeypatch.setenv("ADMIN_EMAILS", ADMIN)
    credentials = {"email": MEMBER, "password": "test-password-123"}
    closed = client.post("/api/v1/auth/register", json=credentials)
    assert closed.status_code == 403 and "quản trị viên" in closed.json()["detail"]
    # A listed admin can still sign up, so a new install can get its first admin.
    assert client.post("/api/v1/auth/register", json={"email": ADMIN, "password": "test-password-123"}).status_code == 200
    created = client.post("/api/v1/admin/users", json=credentials)
    assert created.status_code == 201 and created.json()["email"] == MEMBER and created.json()["credits"] == 100
    assert client.post("/api/v1/admin/users", json=credentials).status_code == 409
    member = created.json()["id"]
    assert login(client, MEMBER).json()["is_admin"] is False
    # Members cannot create accounts or reset passwords.
    assert client.post("/api/v1/admin/users", json={"email": "x@example.com", "password": "test-password-123"}).status_code == 403
    assert client.put(f"/api/v1/admin/users/{member}/password", json={"password": "new-password-456"}).status_code == 403
    login(client, ADMIN)
    assert client.put(f"/api/v1/admin/users/{member}/password", json={"password": "new-password-456"}).status_code == 200
    assert login(client, MEMBER).status_code == 401
    assert client.post("/api/v1/auth/login", json={"email": MEMBER, "password": "new-password-456"}).status_code == 200
