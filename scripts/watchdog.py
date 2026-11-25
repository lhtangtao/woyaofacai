#!/usr/bin/env python3
"""Money3 通用进程崩溃自动重启守护脚本（Watchdog）。

用途：
- 守护桌面端 GUI（默认仅非 0 / 信号崩溃时重启，用户主动关闭退出码为 0 则直接退出）。
- 守护后台 Web 服务与 cloudflared 隧道（支持 `--restart-on-zero` 意外退出均自动拉起）。
- 内置指数退避与“短时间连续快速崩溃熔断”机制，避免故障时陷入无限重启死循环。
- 收到 SIGTERM / SIGINT 时先优雅终止被守护子进程，再退出自身并清理 PID 文件。
"""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import threading
import time
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True)
class WatchdogConfig:
    """守护进程运行配置。"""

    name: str
    command: tuple[str, ...]
    pid_file: Path | None = None
    log_file: Path | None = None
    redirect_child_output: bool = False
    restart_on_zero: bool = False
    max_rapid_restarts: int = 5
    window_sec: float = 120.0
    stable_sec: float = 60.0
    backoff_base: float = 2.0
    backoff_max: float = 15.0
    notify: bool = False


def format_exit_reason(returncode: int) -> str:
    """将子进程退出码格式化为可读中文说明。"""
    if returncode < 0:
        sig_num = -returncode
        try:
            sig_name = signal.Signals(sig_num).name
        except ValueError:
            sig_name = f"SIG{sig_num}"
        return f"被信号 {sig_name} (-{sig_num}) 终止"
    return f"退出码 {returncode}"


def send_macos_notification(title: str, message: str) -> None:
    """在 macOS 上发送桌面通知，失败时静默忽略以免阻塞恢复流程。"""
    if sys.platform != "darwin":
        return
    try:
        subprocess.run(
            [
                "osascript",
                "-e",
                "on run argv\ndisplay notification (item 1 of argv) with title (item 2 of argv)\nend run",
                message,
                title,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=3,
            check=False,
        )
    except Exception:
        pass


def _write_log(log_file: Path | None, name: str, message: str, *, also_stderr: bool) -> None:
    """追加写入守护日志，并按需同步输出到标准错误。"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] [watchdog:{name}] {message}\n"
    if log_file is not None:
        try:
            log_file.parent.mkdir(parents=True, exist_ok=True)
            with log_file.open("a", encoding="utf-8") as fh:
                fh.write(line)
        except Exception:
            pass
    if also_stderr:
        try:
            sys.stderr.write(line)
            sys.stderr.flush()
        except Exception:
            pass


def _write_pid_file(pid_file: Path | None) -> None:
    """写入当前守护进程 PID 到指定文件。"""
    if pid_file is None:
        return
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    pid_file.write_text(f"{os.getpid()}\n", encoding="utf-8")


def _cleanup_pid_file(pid_file: Path | None) -> None:
    """仅当 PID 文件内容与当前进程一致时删除，避免误删新实例 PID。"""
    if pid_file is None:
        return
    try:
        if pid_file.is_file() and pid_file.read_text(encoding="utf-8").strip() == str(os.getpid()):
            pid_file.unlink()
    except Exception:
        pass


def _terminate_child(proc: subprocess.Popen[bytes] | subprocess.Popen[str], grace_sec: float = 5.0) -> None:
    """向子进程发送 SIGTERM，超时后使用 SIGKILL 兜底清理。"""
    if proc.poll() is not None:
        return
    try:
        proc.terminate()
    except Exception:
        return
    deadline = time.monotonic() + grace_sec
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return
        time.sleep(0.1)
    if proc.poll() is None:
        try:
            proc.kill()
            proc.wait(timeout=2.0)
        except Exception:
            pass


def _wait_child_or_stop(proc: subprocess.Popen[bytes] | subprocess.Popen[str], stop_event: threading.Event) -> int:
    """等待子进程退出；若收到守护停止信号，立即优雅终止子进程。"""
    while True:
        code = proc.poll()
        if code is not None:
            return code
        if stop_event.is_set():
            _terminate_child(proc)
            return proc.poll() if proc.poll() is not None else 0
        time.sleep(0.1)


def run_watchdog(config: WatchdogConfig) -> int:
    """运行进程守护主循环，返回守护进程最终退出码。"""
    if not config.command:
        raise ValueError("守护命令不能为空")

    also_stderr = not (config.redirect_child_output and config.log_file is not None)
    stop_event = threading.Event()
    recent_crashes: deque[float] = deque()

    def _handle_signal(_signum: int, _frame: object) -> None:
        stop_event.set()

    prev_sigterm = signal.getsignal(signal.SIGTERM)
    prev_sigint = signal.getsignal(signal.SIGINT)
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    _write_pid_file(config.pid_file)
    _write_log(
        config.log_file,
        config.name,
        f"守护进程已启动 (PID={os.getpid()}，重启零退出={'是' if config.restart_on_zero else '否'})",
        also_stderr=also_stderr,
    )

    try:
        attempt = 0
        while not stop_event.is_set():
            attempt += 1
            started_at = time.monotonic()
            child_log_stream = None
            try:
                if config.redirect_child_output and config.log_file is not None:
                    config.log_file.parent.mkdir(parents=True, exist_ok=True)
                    child_log_stream = config.log_file.open("a", encoding="utf-8")
                    proc = subprocess.Popen(
                        list(config.command),
                        stdout=child_log_stream,
                        stderr=subprocess.STDOUT,
                    )
                else:
                    proc = subprocess.Popen(list(config.command))
            except OSError as exc:
                _write_log(
                    config.log_file,
                    config.name,
                    f"启动子进程失败: {exc}",
                    also_stderr=also_stderr,
                )
                return 127
            finally:
                if child_log_stream is not None:
                    try:
                        child_log_stream.close()
                    except Exception:
                        pass

            returncode = _wait_child_or_stop(proc, stop_event)
            elapsed = time.monotonic() - started_at

            if stop_event.is_set():
                _write_log(
                    config.log_file,
                    config.name,
                    f"收到停止信号，已安全结束子进程（运行 {elapsed:.1f}s），守护退出。",
                    also_stderr=also_stderr,
                )
                return 0

            if returncode == 0 and not config.restart_on_zero:
                _write_log(
                    config.log_file,
                    config.name,
                    f"程序正常退出 (退出码 0，运行 {elapsed:.1f}s)，无需重启。",
                    also_stderr=also_stderr,
                )
                return 0

            # 稳定运行超过阈值后重置连续快速崩溃窗口，避免偶发旧故障累积触发熔断。
            if elapsed >= config.stable_sec:
                recent_crashes.clear()

            now = time.monotonic()
            recent_crashes.append(now)
            cutoff = now - config.window_sec
            while recent_crashes and recent_crashes[0] < cutoff:
                recent_crashes.popleft()

            reason = format_exit_reason(returncode)
            crash_count = len(recent_crashes)

            if crash_count >= config.max_rapid_restarts:
                alert = (
                    f"触发熔断保护：{config.window_sec:.0f}s 内连续异常退出 {crash_count} 次"
                    f"（最近一次 {reason}），已停止自动重启，请检查 logs/money3.log 与 logs/crash_fault.log。"
                )
                _write_log(config.log_file, config.name, alert, also_stderr=also_stderr)
                if config.notify:
                    send_macos_notification(
                        "我要发财 · 自动重启已熔断",
                        f"{config.name} 短时间内连续崩溃 {crash_count} 次，请查看日志排查。",
                    )
                return returncode if returncode != 0 else 1

            delay = min(config.backoff_max, config.backoff_base * (2 ** (crash_count - 1)))
            _write_log(
                config.log_file,
                config.name,
                f"检测到异常退出（{reason}，运行时长 {elapsed:.1f}s，窗口内第 {crash_count}/{config.max_rapid_restarts} 次），"
                f"将在 {delay:.2f}s 后自动重启（第 {attempt + 1} 次启动）...",
                also_stderr=also_stderr,
            )
            if config.notify:
                send_macos_notification(
                    "我要发财 · 检测到异常退出",
                    f"{config.name} {reason}，正在自动重启恢复…",
                )

            if stop_event.wait(delay):
                _write_log(
                    config.log_file,
                    config.name,
                    "在重启等待期间收到停止信号，守护进程安全退出。",
                    also_stderr=also_stderr,
                )
                return 0

        return 0
    finally:
        signal.signal(signal.SIGTERM, prev_sigterm)
        signal.signal(signal.SIGINT, prev_sigint)
        _cleanup_pid_file(config.pid_file)


def parse_args(argv: Sequence[str] | None = None) -> WatchdogConfig:
    """解析命令行参数并构建 WatchdogConfig。"""
    parser = argparse.ArgumentParser(description="Money3 进程崩溃自动重启守护脚本")
    parser.add_argument("--name", required=True, help="被守护组件名称（如 desktop/web/tunnel）")
    parser.add_argument("--pid-file", type=Path, help="写入当前守护进程 PID 的文件路径")
    parser.add_argument("--log-file", type=Path, help="记录守护事件日志的文件路径")
    parser.add_argument(
        "--redirect-child-output",
        action="store_true",
        help="将子进程 stdout/stderr 追加写入 --log-file（适用于后台服务）",
    )
    parser.add_argument(
        "--restart-on-zero",
        action="store_true",
        help="子进程返回 0 时也自动重启（适用于常驻后台服务）",
    )
    parser.add_argument("--max-rapid-restarts", type=int, default=5, help="时间窗口内最大快速崩溃重启次数")
    parser.add_argument("--window-sec", type=float, default=120.0, help="快速崩溃统计时间窗口（秒）")
    parser.add_argument("--stable-sec", type=float, default=60.0, help="判定为稳定运行的最小持续时长（秒）")
    parser.add_argument("--backoff-base", type=float, default=2.0, help="指数退避基础等待秒数")
    parser.add_argument("--backoff-max", type=float, default=15.0, help="指数退避最大等待秒数")
    parser.add_argument("--notify", action="store_true", help="异常重启或熔断时发送 macOS 通知")
    parser.add_argument("command", nargs=argparse.REMAINDER, help="待执行的目标命令（建议在前面加 --）")

    ns = parser.parse_args(list(argv) if argv is not None else None)
    cmd = list(ns.command)
    if cmd and cmd[0] == "--":
        cmd = cmd[1:]
    if not cmd:
        parser.error("必须通过 `-- <命令> [参数...]` 指定要守护的目标命令")

    return WatchdogConfig(
        name=ns.name,
        command=tuple(cmd),
        pid_file=ns.pid_file,
        log_file=ns.log_file,
        redirect_child_output=ns.redirect_child_output,
        restart_on_zero=ns.restart_on_zero,
        max_rapid_restarts=max(1, ns.max_rapid_restarts),
        window_sec=max(0.1, ns.window_sec),
        stable_sec=max(0.0, ns.stable_sec),
        backoff_base=max(0.01, ns.backoff_base),
        backoff_max=max(0.01, ns.backoff_max),
        notify=ns.notify,
    )


def main(argv: Sequence[str] | None = None) -> int:
    """CLI 入口。"""
    config = parse_args(argv)
    return run_watchdog(config)


if __name__ == "__main__":
    raise SystemExit(main())
