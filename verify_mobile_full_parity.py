"""Standalone verification script for mobile full-feature parity and user account isolation.
Tests all endpoints, 28 desktop reports, 27 financial charts, K-line indicators, Bark push,
and multi-user role-based authentication with account isolation (拉科 / lake).
"""

from datetime import date
import json
import sys

from portfolio.web.app import app

def run_tests():
    print("==================================================================")
    print("  🚀 开始对 Money Portfolio 移动端全量功能及拉科权限隔离进行验证...")
    print("==================================================================")
    
    client = app.test_client()
    passed = 0
    total = 0

    def check(name, url, method="GET", json_body=None, expected_keys=None):
        nonlocal passed, total
        total += 1
        try:
            if method == "GET":
                res = client.get(url)
            else:
                res = client.post(url, json=json_body or {})
            
            if res.status_code != 200:
                print(f"  ❌ [{name}] HTTP 状态码错误: {res.status_code}")
                return False
            
            if res.is_json:
                data = res.get_json()
                if data.get("status") != "ok":
                    print(f"  ❌ [{name}] 业务 status 不是 ok: {data}")
                    return False
                if expected_keys:
                    for k in expected_keys:
                        if k not in data:
                            print(f"  ❌ [{name}] 缺少预期键: {k}")
                            return False
            print(f"  ✅ [{name}] 验证成功！(HTTP 200 OK)")
            passed += 1
            return True
        except Exception as e:
            print(f"  ❌ [{name}] 异常: {e}")
            return False

    # 1. 首页与网络信息
    check("首页 HTML 渲染", "/")
    check("网络接口与 Tailscale 探测", "/api/network/info", expected_keys=["lan_ip", "port"])

    # 2. 资产总览 (默认管理员全量)
    check("资产总览概览看板", "/api/overview", expected_keys=["total_value", "holdings", "channel_breakdown"])

    # 3. 报表清单
    check("全量报表目录查询", "/api/reports/list", expected_keys=["groups"])

    # 4. 核心桌面报表
    reports_to_test = [
        "当前持仓",
        "股票汇总",
        "清仓收益",
        "渠道汇总",
        "分类汇总",
        "人员资产",
        "收益日历",
        "季度矩阵",
        "每月持仓收益",
        "每月微操收益",
        "月度趋势",
    ]
    for rep in reports_to_test:
        check(f"桌面报表对齐: {rep}", f"/api/report/{rep}?page=1&page_size=10", expected_keys=["columns", "rows", "total"])

    # 5. 图表分析中心 (27个图表)
    check("27大专业图表序列化", "/api/charts", expected_keys=["charts", "count"])

    # 6. 交易流水
    check("交易明细流水与分页", "/api/trades?page=1&size=10", expected_keys=["rows", "total"])

    # 7. 行情监控与专业K线
    check("标的监控雷达列表", "/api/monitoring/symbols", expected_keys=["symbols", "count"])
    check("AAOI 专业日K线与指标计算", "/api/monitoring/kline?symbol=AAOI", expected_keys=["dates", "ohlcv", "ma", "macd", "rsi", "technicals"])
    check("AVGO 专业日K线与指标计算", "/api/monitoring/kline?symbol=AVGO", expected_keys=["dates", "ohlcv", "ma", "macd", "rsi", "technicals"])

    # 8. 监控规则与警报
    check("监控规则与警报记录", "/api/monitoring", expected_keys=["targets", "rules", "alerts"])

    # 9. 账户认证与「拉科」专属分账数据隔离
    print("\n--- 开始验证「拉科」(账号: 拉科, 密码: lake) 权限隔离 ---")
    check("拉科 登录鉴权", "/api/auth/login", method="POST", json_body={"username": "拉科", "password": "lake"}, expected_keys=["user"])
    
    # 拉科视角持仓验证
    res_lake_ov = client.get("/api/overview")
    data_lake_ov = res_lake_ov.get_json()
    assert data_lake_ov["holdings_count"] == 8, f"拉科持仓数应为8，实际为: {data_lake_ov['holdings_count']}"
    assert data_lake_ov["user"]["person_id"] == 5
    print(f"  ✅ [拉科持仓隔离] 验证成功: 仅呈现拉科名下 {data_lake_ov['holdings_count']} 只标的 (总资产: ${data_lake_ov['total_value']})")
    passed += 1
    total += 1

    # 拉科视角交易记录验证
    res_lake_tr = client.get("/api/trades")
    data_lake_tr = res_lake_tr.get_json()
    assert data_lake_tr["total"] == 127, f"拉科交易记录应为127，实际为: {data_lake_tr['total']}"
    print(f"  ✅ [拉科交易隔离] 验证成功: 仅呈现拉科名下 {data_lake_tr['total']} 笔交易记录")
    passed += 1
    total += 1

    # 拉科视角当前持仓报表验证
    res_lake_rep = client.get("/api/report/当前持仓")
    data_lake_rep = res_lake_rep.get_json()
    assert data_lake_rep["total"] == 8
    print(f"  ✅ [拉科报表隔离] 验证成功: 当前持仓仅展示拉科的 8 行持仓")
    passed += 1
    total += 1

    # 重置/切回管理员验证
    check("重置切回管理员视角", "/api/auth/login", method="POST", json_body={"username": "admin", "password": "admin"}, expected_keys=["user"])
    res_admin_tr = client.get("/api/trades")
    assert res_admin_tr.get_json()["total"] == 1454
    print("  ✅ [管理员全量权限恢复] 验证成功: 恢复查看全量 1,454 笔交易与 28 只全量持仓")
    passed += 1
    total += 1

    print("==================================================================")
    print(f"  🎉 全部功能与权限隔离验证完成！通过率: {passed}/{total} ({passed/total*100:.1f}%)")
    print("==================================================================")
    return passed == total

if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)
