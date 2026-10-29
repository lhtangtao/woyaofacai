"""财报日历子进程：在独立进程中调用长桥 CalendarContext.finance_calendar。

背景：长桥 Python SDK（PyO3/Rust）在阻塞等待网络响应时不会释放 GIL，
若在桌面端进程内直接调用，网络一慢就会冻住整个界面（macOS 彩虹圈）。
放到子进程后，子进程拥有独立的 GIL，主进程只需等待 JSON 结果，并可超时终止。

输入（stdin JSON）：{"path": 凭证路径, "year": 年, "month": 月, "markets": [...]}
输出（stdout JSON）：{"events": [...], "warnings": [...]} 或 {"error": "..."}
"""
import json
import sys


def main():
    request = json.loads(sys.stdin.read() or '{}')
    from portfolio.services.earnings_service import fetch_calendar_month_inprocess, event_to_dict
    try:
        events, warnings = fetch_calendar_month_inprocess(
            request['path'], int(request['year']), int(request['month']), list(request.get('markets') or []))
        payload = {'events': [event_to_dict(e) for e in events], 'warnings': sorted(warnings)}
    except Exception as exc:  # 子进程内错误通过 JSON 回传，由主进程记录日志
        payload = {'error': f'{type(exc).__name__}: {exc}'}
    sys.stdout.write(json.dumps(payload, ensure_ascii=False))
    sys.stdout.flush()


if __name__ == '__main__':
    main()
