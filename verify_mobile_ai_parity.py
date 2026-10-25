#!/usr/bin/env python3
"""
Verification Script for Mobile AI Assistant Parity with Desktop Assistant.
Tests:
1. Codex CLI connection status endpoint (/api/ai/status)
2. Filters endpoint (/api/ai/filters) with multi-user isolation (admin vs 拉科)
3. Live AI execution endpoint (/api/ai/chat) with context injection and scope tags
4. Conversation history endpoint (/api/ai/history) and user isolation
5. Clear session endpoint (/api/ai/clear)
"""
import sys
import json
from pathlib import Path

# Set up project path
money3_root = Path(__file__).resolve().parent
sys.path.insert(0, str(money3_root / "src"))

from portfolio.web.app import app, _ai_chat_histories, load_all_users

def run_tests():
    print("==================================================")
    print("🚀 Starting Mobile AI Assistant Full Parity Tests")
    print("==================================================")
    
    client = app.test_client()
    
    # 1. Test /api/ai/status
    print("\n[1/5] Testing /api/ai/status...")
    res = client.get("/api/ai/status")
    assert res.status_code == 200, f"Expected 200, got {res.status_code}"
    status_data = res.get_json()
    print(f"  -> Codex Status: installed={status_data.get('installed')}, logged_in={status_data.get('logged_in')}, account={status_data.get('account_type')}")
    assert status_data.get("installed") is True
    assert status_data.get("logged_in") is True
    print("  ✓ /api/ai/status passed successfully!")

    # 2. Test /api/ai/filters for admin
    print("\n[2/5] Testing /api/ai/filters for Admin...")
    # Login as admin
    client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
    res_admin_filters = client.get("/api/ai/filters")
    assert res_admin_filters.status_code == 200
    admin_filters = res_admin_filters.get_json()
    assert admin_filters["status"] == "ok"
    assert len(admin_filters["persons"]) > 1, "Admin should see multiple persons"
    assert not admin_filters["persons"][0].get("locked"), "Admin person should not be locked"
    assert "全部渠道" in admin_filters["channels"]
    assert "全部股票" in admin_filters["symbols"]
    print(f"  -> Admin persons: {len(admin_filters['persons'])}, channels: {len(admin_filters['channels'])}, symbols: {len(admin_filters['symbols'])}")
    print("  ✓ Admin filters test passed!")

    # 3. Test /api/ai/filters for 拉科 (lake)
    print("\n[3/5] Testing /api/ai/filters for 拉科 (lake) isolation...")
    client.post("/api/auth/login", json={"username": "拉科", "password": "lake"})
    res_lake_filters = client.get("/api/ai/filters")
    assert res_lake_filters.status_code == 200
    lake_filters = res_lake_filters.get_json()
    assert lake_filters["status"] == "ok"
    assert len(lake_filters["persons"]) == 1, "Lake should only see Lake"
    assert lake_filters["persons"][0]["name"] == "拉科"
    assert lake_filters["persons"][0]["locked"] is True, "Lake person must be locked"
    assert "TQQQ" in lake_filters["symbols"], "TQQQ should be in Lake holdings"
    print(f"  -> Lake persons: {lake_filters['persons']}, channels: {lake_filters['channels']}, symbols count: {len(lake_filters['symbols'])}")
    print("  ✓ Lake filters isolation test passed!")

    # 4. Test /api/ai/chat with live Codex CLI and context injection
    print("\n[4/5] Testing /api/ai/chat with Lake context (TQQQ)...")
    chat_payload = {
        "message": "请用简短一句话说明我目前持有的 TQQQ 成本均价是多少？",
        "symbol": "TQQQ",
        "channel": "全部渠道",
        "person": "拉科",
        "include_context": True
    }
    chat_res = client.post("/api/ai/chat", json=chat_payload)
    assert chat_res.status_code == 200, f"Expected 200, got {chat_res.status_code}: {chat_res.data}"
    chat_data = chat_res.get_json()
    assert chat_data["status"] == "ok"
    assert chat_data["symbol"] == "TQQQ"
    assert "拉科" in chat_data["scope_tag"]
    assert "TQQQ" in chat_data["scope_tag"]
    print(f"  -> Scope Tag: {chat_data['scope_tag']}")
    print(f"  -> Live AI Reply Preview: {chat_data['reply'][:120]}...")
    print("  ✓ /api/ai/chat live execution with context passed!")

    # 5. Test History & Clear for Lake
    print("\n[5/5] Testing /api/ai/history and /api/ai/clear...")
    hist_res = client.get("/api/ai/history")
    assert hist_res.status_code == 200
    hist_data = hist_res.get_json()
    assert hist_data["username"] == "拉科"
    assert len(hist_data["history"]) >= 2, "Should contain user message and ai message"
    print(f"  -> Found {len(hist_data['history'])} messages in Lake history")
    
    # Test clear
    clear_res = client.post("/api/ai/clear")
    assert clear_res.status_code == 200
    hist_after = client.get("/api/ai/history").get_json()
    assert len(hist_after["history"]) == 0, "History should be empty after clear"
    print("  ✓ History and Clear test passed!")

    print("\n==================================================")
    print("🎉 ALL MOBILE AI PARITY TESTS PASSED SUCCESSFULLY!")
    print("==================================================")

if __name__ == "__main__":
    run_tests()
