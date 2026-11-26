"""测试 Web 端静态资源强缓存、Gzip 压缩传输与动态 JSON 压缩中间件。"""

from __future__ import annotations

import gzip
import json
from pathlib import Path

from portfolio.web import app as web_app
from portfolio.web.security.auth_context import AuthContext


def test_hashed_asset_serves_gzip_immutable_cache_and_304(tmp_path: Path, monkeypatch) -> None:
    """带哈希的 /assets/* 应支持 gzip 压缩、长期 immutable 缓存、304 协商且不带 Set-Cookie。"""
    assets_dir = tmp_path / "assets"
    assets_dir.mkdir(parents=True)
    raw_js = ("console.log('money3-bundle-chunk');\n" * 120).encode("utf-8")
    (assets_dir / "index-test123.js").write_bytes(raw_js)

    monkeypatch.setattr(web_app, "STATIC_DIST_DIR", tmp_path)
    web_app._COMPRESSED_ASSET_CACHE.clear()

    client = web_app.app.test_client()

    # 1. 带 Accept-Encoding: gzip 请求 -> 返回压缩体积 + immutable + 无 Set-Cookie
    resp = client.get("/assets/index-test123.js", headers={"Accept-Encoding": "gzip, deflate"})
    assert resp.status_code == 200
    assert resp.headers.get("Content-Encoding") == "gzip"
    assert "immutable" in resp.headers.get("Cache-Control", "")
    assert "Accept-Encoding" in resp.headers.get("Vary", "")
    assert "Set-Cookie" not in resp.headers
    assert len(resp.data) < len(raw_js)
    assert gzip.decompress(resp.data) == raw_js

    # 2. 携带 ETag 再次请求 -> 304 Not Modified
    etag = resp.headers.get("ETag")
    assert etag
    resp_304 = client.get(
        "/assets/index-test123.js",
        headers={"Accept-Encoding": "gzip", "If-None-Match": etag},
    )
    assert resp_304.status_code == 304
    assert "immutable" in resp_304.headers.get("Cache-Control", "")

    # 3. 不带 Accept-Encoding 时返回未压缩原文，同样带 immutable
    resp_plain = client.get("/assets/index-test123.js")
    assert resp_plain.status_code == 200
    assert "Content-Encoding" not in resp_plain.headers
    assert "immutable" in resp_plain.headers.get("Cache-Control", "")
    assert resp_plain.data == raw_js


def test_hashed_asset_rejects_path_traversal(tmp_path: Path, monkeypatch) -> None:
    """静态资源路径穿越探测应被拦截并返回 404。"""
    assets_dir = tmp_path / "assets"
    assets_dir.mkdir(parents=True)
    secret_file = tmp_path / "secret.txt"
    secret_file.write_text("top-secret", encoding="utf-8")

    monkeypatch.setattr(web_app, "STATIC_DIST_DIR", tmp_path)
    client = web_app.app.test_client()

    resp = client.get("/assets/../secret.txt", headers={"Accept-Encoding": "gzip"})
    assert resp.status_code == 404


def test_api_json_response_auto_gzip_when_above_threshold(monkeypatch) -> None:
    """动态 JSON 响应体超过 1KB 且客户端支持 gzip 时应自动压缩返回。"""
    big_items = [f"item-{idx}-padding-payload" for idx in range(100)]

    def _fake_to_dict(self):
        return {"items": big_items, "is_authenticated": False}

    monkeypatch.setattr(AuthContext, "to_dict", _fake_to_dict)
    client = web_app.app.test_client()

    resp_gzip = client.get("/api/auth/me", headers={"Accept-Encoding": "gzip"})
    assert resp_gzip.status_code == 200
    assert resp_gzip.headers.get("Content-Encoding") == "gzip"
    assert "Accept-Encoding" in resp_gzip.headers.get("Vary", "")
    decoded = json.loads(gzip.decompress(resp_gzip.data).decode("utf-8"))
    assert decoded["user"]["items"] == big_items

    resp_plain = client.get("/api/auth/me")
    assert resp_plain.status_code == 200
    assert "Content-Encoding" not in resp_plain.headers
    assert resp_plain.get_json()["user"]["items"] == big_items
