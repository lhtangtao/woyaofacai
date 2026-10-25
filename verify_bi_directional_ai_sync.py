#!/usr/bin/env python3
"""
Verification Script for:
1. Left Drawer Navigation Consistency with Desktop Sidebar
2. AI Stock Analysis (AI 股票分析) Endpoint & Shared Disk Persistence
3. AI Investment Advisor (AI 投资问答) Multi-Session Shared Disk Persistence
4. Bi-Directional History Sync between Desktop and Mobile (AAOX and other symbols)
"""
import sys
import json
from pathlib import Path

money3_root = Path(__file__).resolve().parent
sys.path.insert(0, str(money3_root / "src"))

from portfolio.web.app import (
    app,
    get_ai_data_dir,
    load_ai_chat_sessions,
    save_ai_chat_sessions,
    load_ai_stock_sessions,
    save_ai_stock_sessions,
)

def run_tests():
    print("==================================================")
    print("🚀 Starting Bi-Directional AI Sync & Drawer Tests")
    print("==================================================")
    
    client = app.test_client()

    # 1. Verify Shared Session Files on Disk
    print("\n[1/5] Verifying Shared Disk Paths...")
    ai_dir = get_ai_data_dir()
    chat_file = ai_dir / "ai_chat_sessions.json"
    stock_file = ai_dir / "ai_stock_analysis_sessions.json"
    print(f"  -> Shared AI Data Dir: {ai_dir}")
    print(f"  -> Chat file exists: {chat_file.exists()} (size={chat_file.stat().st_size if chat_file.exists() else 0} bytes)")
    print(f"  -> Stock file exists: {stock_file.exists()} (size={stock_file.stat().st_size if stock_file.exists() else 0} bytes)")
    assert chat_file.exists(), "ai_chat_sessions.json must exist"
    assert stock_file.exists(), "ai_stock_analysis_sessions.json must exist"
    print("  ✓ Shared disk paths verified!")

    # 2. Test Reading Desktop's Previous AAOX Consultations in AI 投资问答
    print("\n[2/5] Testing Reading Desktop's AAOX Consultations via /api/ai/history...")
    # Test for 拉科
    res_lake = client.get("/api/ai/history?person=拉科&channel=全部渠道&symbol=AAOX")
    assert res_lake.status_code == 200
    data_lake = res_lake.get_json()
    assert data_lake["status"] == "ok"
    assert data_lake["session_key"] == "拉科___全部渠道___AAOX"
    assert len(data_lake["history"]) >= 4, f"Expected at least 4 messages from desktop, got {len(data_lake['history'])}"
    print(f"  -> Successfully loaded {len(data_lake['history'])} messages for 拉科___全部渠道___AAOX!")
    print(f"  -> First message: [{data_lake['history'][0]['role']}] {data_lake['history'][0]['content']}")

    # Test for 个人
    res_person = client.get("/api/ai/history?person=个人&channel=全部渠道&symbol=AAOX")
    assert res_person.status_code == 200
    data_person = res_person.get_json()
    assert data_person["session_key"] == "个人___全部渠道___AAOX"
    assert len(data_person["history"]) >= 3
    print(f"  -> Successfully loaded {len(data_person['history'])} messages for 个人___全部渠道___AAOX!")
    print("  ✓ Reading desktop's previous consultations verified!")

    # 3. Test AI 股票分析 (Pure Stock Analysis) Endpoints
    print("\n[3/5] Testing AI 股票分析 (ai_stock_analysis_sessions.json)...")
    # Load INTC (existing in desktop file)
    res_intc = client.get("/api/ai/stock/history?symbol=INTC")
    assert res_intc.status_code == 200
    data_intc = res_intc.get_json()
    assert data_intc["status"] == "ok"
    assert len(data_intc["history"]) >= 8, f"Expected 8 desktop INTC msgs, got {len(data_intc['history'])}"
    print(f"  -> Successfully loaded {len(data_intc['history'])} desktop messages for INTC!")

    # Query AAOX in stock analysis
    res_aaox = client.get("/api/ai/stock/history?symbol=AAOX")
    assert res_aaox.status_code == 200
    data_aaox = res_aaox.get_json()
    assert data_aaox["status"] == "ok"
    print(f"  -> AAOX stock history loaded: {len(data_aaox['history'])} messages (welcome message ready: {bool(data_aaox.get('welcome_message'))})")
    print("  ✓ AI 股票分析 reading from disk verified!")

    # 4. Test Bi-Directional Sync: Mobile writes message, desktop file reflects it
    print("\n[4/5] Testing Bi-Directional Disk Writing Sync...")
    # Add a mock test question into stock analysis for AAOX
    test_symbol = "AAOX"
    stock_sessions = load_ai_stock_sessions()
    orig_count = len(stock_sessions.get(test_symbol, []))

    # Post via API
    chat_post = client.post("/api/ai/stock/chat", json={
        "symbol": test_symbol,
        "message": "请用极简一句话总结 AAOX 对应标的 AAOI 的核心业务。"
    })
    assert chat_post.status_code == 200
    chat_resp = chat_post.get_json()
    assert chat_resp["status"] == "ok"
    print(f"  -> AI Stock Chat response: {chat_resp['reply'][:80]}...")

    # Verify directly from disk file (desktop view)
    refreshed_stock_sessions = load_ai_stock_sessions()
    new_count = len(refreshed_stock_sessions.get(test_symbol, []))
    assert new_count == orig_count + 2, f"Expected {orig_count + 2} messages on disk, got {new_count}"
    print(f"  -> Verified on disk: {orig_count} -> {new_count} messages saved directly into {stock_file.name}!")
    print("  ✓ Bi-directional disk sync verified!")

    # 5. Verify Drawer Menu Structure matches Desktop Sidebar
    print("\n[5/5] Verifying Left Drawer & Navigation Structure in mobile.html...")
    template_path = money3_root / "src/portfolio/web/templates/mobile.html"
    content = template_path.read_text(encoding="utf-8")
    
    # Check 5 desktop groups
    assert "资产管理" in content
    assert "交易与收益" in content
    assert "AI 助手" in content
    assert "图表分析" in content
    assert "系统管理" in content

    # Check key desktop items
    assert "今日盈亏" in content
    assert "当前持仓" in content
    assert "股票汇总" in content
    assert "人员资产" in content
    assert "券商资产" in content
    assert "股票归属" in content
    assert "持仓财报日历" in content
    assert "AI 股票分析" in content
    assert "view-ai-stock" in content
    assert "tabNavAiStock" in content
    print("  -> Drawer contains all 5 desktop groups and corresponding routes!")
    print("  ✓ Drawer structure alignment verified!")

    print("\n==================================================")
    print("🎉 ALL BI-DIRECTIONAL AI SYNC TESTS PASSED!")
    print("==================================================")

if __name__ == "__main__":
    run_tests()
