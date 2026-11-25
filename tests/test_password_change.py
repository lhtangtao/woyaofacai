"""Web 端密码修改与管理员重置密码安全测试。"""
import pytest

from portfolio.web import app as web_app_module
from portfolio.web.security import credentials, sessions


@pytest.fixture
def isolated_security_db(tmp_path, monkeypatch):
    db_file = tmp_path / "security.sqlite3"
    monkeypatch.setattr(credentials, "get_security_db_path", lambda: db_file)
    credentials.init_security_db()
    for uname, pwd, role, pid, pname in (
        ("admin", "admin123", "admin", None, "管理员"),
        ("拉科", "lake123", "user", 5, "拉科"),
        ("张三", "zhangsan123", "user", 6, "张三"),
    ):
        if credentials.get_user_by_username(uname):
            credentials.set_user_password(uname, pwd)
        else:
            credentials.create_user(uname, pwd, role=role, person_id=pid, person_name=pname)
    return db_file


def _login_client(client, username: str, password: str):
    csrf_resp = client.get("/api/auth/csrf")
    csrf_token = csrf_resp.get_json()["csrf_token"]
    resp = client.post(
        "/api/auth/login",
        json={"username": username, "password": password},
        headers={"X-CSRF-Token": csrf_token},
    )
    cookie = client.get_cookie("money_csrf_token")
    latest_csrf = cookie.value if cookie else csrf_token
    return resp, latest_csrf


def test_user_can_change_own_password_with_old_password_verification(isolated_security_db):
    """拉科本人可修改自己密码，必须验证原密码，且改密后旧会话立即失效。"""
    client = web_app_module.app.test_client()
    login_resp, csrf = _login_client(client, "拉科", "lake123")
    assert login_resp.status_code == 200

    # 1. 原密码错误 -> 400
    bad_old = client.post(
        "/api/auth/users/password",
        json={"username": "拉科", "current_password": "wrong_old_password", "password": "newpass999"},
        headers={"X-CSRF-Token": csrf},
    )
    assert bad_old.status_code == 400
    assert "当前密码不正确" in bad_old.get_json()["message"]

    # 2. 新密码与原密码相同 -> 400
    same_pwd = client.post(
        "/api/auth/users/password",
        json={"username": "拉科", "current_password": "lake123", "password": "lake123"},
        headers={"X-CSRF-Token": csrf},
    )
    assert same_pwd.status_code == 400

    # 3. 越权修改他人密码 -> 403
    forbidden = client.post(
        "/api/auth/users/password",
        json={"username": "张三", "current_password": "lake123", "password": "newpass999"},
        headers={"X-CSRF-Token": csrf},
    )
    assert forbidden.status_code == 403

    # 4. 正确原密码修改自己密码 -> 200，要求重新登录，且旧会话失效
    ok = client.post(
        "/api/auth/users/password",
        json={"username": "拉科", "current_password": "lake123", "password": "new_lake_888"},
        headers={"X-CSRF-Token": csrf},
    )
    assert ok.status_code == 200
    assert ok.get_json()["relogin_required"] is True

    # 旧密码不能再登录，新密码可以登录
    old_login, _ = _login_client(client, "拉科", "lake123")
    assert old_login.status_code == 401
    new_login, _ = _login_client(client, "拉科", "new_lake_888")
    assert new_login.status_code == 200


def test_admin_can_reset_other_user_password_without_old_password(isolated_security_db):
    """管理员可免原密码重置成员密码并吊销对方旧会话；管理员改自己密码仍需原密码。"""
    lake_user = credentials.get_user_by_username("拉科")
    old_token = sessions.create_session(lake_user["id"], "拉科", "user", 5, "拉科")
    assert sessions.validate_session(old_token) is not None

    admin_client = web_app_module.app.test_client()
    login_resp, csrf = _login_client(admin_client, "admin", "admin123")
    assert login_resp.status_code == 200

    # 管理员重置拉科密码
    reset_resp = admin_client.post(
        "/api/auth/users/password",
        json={"username": "拉科", "password": "reset_by_admin_666"},
        headers={"X-CSRF-Token": csrf},
    )
    assert reset_resp.status_code == 200
    assert reset_resp.get_json()["relogin_required"] is False
    # 拉科旧会话应已被吊销
    assert sessions.validate_session(old_token) is None

    # 管理员修改自己密码必须提供原密码
    self_no_old = admin_client.post(
        "/api/auth/users/password",
        json={"username": "admin", "password": "admin_new_999"},
        headers={"X-CSRF-Token": csrf},
    )
    assert self_no_old.status_code == 400
