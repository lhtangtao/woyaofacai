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
import re
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
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
    crash_dir: Path | None = None
    context_logs: tuple[Path, ...] = ()
    keep_crashes: int = 30
    health_url: str | None = None
    health_interval: float = 30.0
    health_grace: float = 45.0
    health_failures: int = 3
    keepalive_url_regex: str | None = None
    keepalive_path: str = "/"


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


def _probe_health_url(url: str, timeout: float = 3.0, *, bypass_proxy: bool = True) -> bool:
    """探测目标 URL 是否返回 2xx；本地探活默认绕过代理，公网保活默认按系统路由穿透探测。"""
    opener = (
        urllib.request.build_opener(urllib.request.ProxyHandler({}))
        if bypass_proxy
        else urllib.request.build_opener()
    )
    req = urllib.request.Request(url, method="GET")
    try:
        with opener.open(req, timeout=timeout) as resp:
            return 200 <= getattr(resp, "status", 0) < 300
    except Exception:
        return False


def _extract_latest_url_since(log_file: Path | None, start_offset: int, pattern: str | None) -> str | None:
    """从子进程本次启动后的日志片段提取最新匹配的公网 URL。"""
    if not pattern or log_file is None or not log_file.is_file():
        return None
    try:
        size = log_file.stat().st_size
        offset = start_offset if 0 <= start_offset <= size else 0
        with log_file.open("rb") as fh:
            fh.seek(offset)
            text = fh.read().decode("utf-8", errors="replace")
        matches = re.findall(pattern, text)
        return matches[-1] if matches else None
    except Exception:
        return None


def _wait_child_or_stop(
    proc: subprocess.Popen[bytes] | subprocess.Popen[str],
    stop_event: threading.Event,
    config: WatchdogConfig,
    *,
    also_stderr: bool,
    log_start_offset: int = 0,
) -> tuple[int, bool]:
    """等待子进程退出；支持停止信号、本地健康检查及公网端到端双向保活探活。"""
    start_at = time.monotonic()
    next_probe_at = start_at + config.health_grace
    consecutive_failures = 0

    while True:
        code = proc.poll()
        if code is not None:
            return code, False
        if stop_event.is_set():
            _terminate_child(proc)
            return (proc.poll() if proc.poll() is not None else 0), False

        if config.health_url or config.keepalive_url_regex:
            now = time.monotonic()
            if now >= next_probe_at:
                next_probe_at = now + config.health_interval
                failed_target: str | None = None

                if config.health_url and not _probe_health_url(config.health_url, timeout=3.0, bypass_proxy=True):
                    failed_target = config.health_url
                elif config.keepalive_url_regex:
                    public_base = _extract_latest_url_since(
                        config.log_file,
                        log_start_offset,
                        config.keepalive_url_regex,
                    )
                    if public_base:
                        suffix = config.keepalive_path if config.keepalive_path.startswith("/") else f"/{config.keepalive_path}"
                        probe_url = f"{public_base.rstrip('/')}{suffix}"
                        if not _probe_health_url(probe_url, timeout=8.0, bypass_proxy=False):
                            failed_target = probe_url
                    elif not config.health_url:
                        failed_target = "公网地址未就绪"

                if failed_target is None:
                    consecutive_failures = 0
                else:
                    consecutive_failures += 1
                    _write_log(
                        config.log_file,
                        config.name,
                        f"健康/保活探测未通过 ({consecutive_failures}/{config.health_failures}): {failed_target}",
                        also_stderr=also_stderr,
                    )
                    if consecutive_failures >= config.health_failures:
                        _write_log(
                            config.log_file,
                            config.name,
                            f"健康检查连续失败 {consecutive_failures} 次，判定进程假死，正在自动终止并重启恢复...",
                            also_stderr=also_stderr,
                        )
                        _terminate_child(proc)
                        final_code = proc.poll()
                        return (final_code if final_code is not None else -signal.SIGTERM), True
        time.sleep(0.1)


def _snapshot_file_offsets(paths: Sequence[Path]) -> dict[Path, int]:
    """在启动子进程前记录各关联日志的当前字节偏移量。"""
    offsets: dict[Path, int] = {}
    for p in paths:
        try:
            offsets[p] = p.stat().st_size if p.is_file() else 0
        except OSError:
            offsets[p] = 0
    return offsets


def _read_context_excerpt(
    path: Path,
    start_offset: int,
    *,
    max_bytes: int = 16 * 1024,
    tail_lines: int = 60,
) -> tuple[str, str]:
    """读取日志摘要：优先抓取本次运行期间新增内容，否则回退读取末尾行。"""
    if not path.is_file():
        return "", "文件不存在"
    try:
        size = path.stat().st_size
        if size > start_offset >= 0:
            read_from = max(start_offset, size - max_bytes)
            truncated = read_from > start_offset
            with path.open("rb") as fh:
                fh.seek(read_from)
                raw = fh.read()
            text = raw.decode("utf-8", errors="replace").strip()
            mode = "本次运行期间新增（已截断尾部）" if truncated else "本次运行期间新增"
            return text, mode
        with path.open("rb") as fh:
            if size > max_bytes * 2:
                fh.seek(size - max_bytes * 2)
            raw = fh.read()
        lines = raw.decode("utf-8", errors="replace").splitlines()
        excerpt = "\n".join(lines[-tail_lines:]).strip()
        if len(excerpt.encode("utf-8")) > max_bytes:
            excerpt = excerpt.encode("utf-8")[-max_bytes:].decode("utf-8", errors="replace").strip()
        return excerpt, ("日志文件为空" if not excerpt else f"末尾最新 {min(tail_lines, len(lines))} 行")
    except OSError as exc:
        return "", f"读取失败: {exc}"


def _prune_crash_snapshots(crash_dir: Path, keep_crashes: int) -> None:
    """仅保留最新 keep_crashes 份崩溃快照文件，清理过旧记录。"""
    if keep_crashes <= 0 or not crash_dir.is_dir():
        return
    try:
        files = sorted(
            (p for p in crash_dir.glob("crash_*.log") if p.is_file()),
            key=lambda p: (p.stat().st_mtime_ns, p.name),
            reverse=True,
        )
        for old_file in files[keep_crashes:]:
            try:
                old_file.unlink()
            except OSError:
                pass
    except OSError:
        pass


def _write_crash_snapshot(
    config: WatchdogConfig,
    *,
    reason: str,
    elapsed: float,
    crash_count: int,
    circuit_broken: bool,
    stderr_tail: Sequence[str],
    start_offsets: dict[Path, int],
) -> Path | None:
    """将崩溃摘要、子进程 stderr 尾部与关联日志上下文打包为独立现场快照。"""
    if config.crash_dir is None:
        return None
    try:
        config.crash_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_name = "".join(ch if (ch.isalnum() or ch in ("-", "_")) else "_" for ch in config.name) or "app"
        target = config.crash_dir / f"crash_{stamp}_{safe_name}.log"
        seq = 1
        while target.exists():
            seq += 1
            target = config.crash_dir / f"crash_{stamp}_{seq}_{safe_name}.log"

        sections: list[str] = [
            "========================================================================",
            f"Money3 崩溃现场报告 ({config.name})",
            "========================================================================",
            f"记录时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"守护进程 PID: {os.getpid()}",
            f"启动命令: {' '.join(config.command)}",
            f"退出原因: {reason}",
            f"运行时长: {elapsed:.2f}s",
            f"窗口内崩溃次数: {crash_count}/{config.max_rapid_restarts}"
            + ("（已触发熔断停止重启）" if circuit_broken else "（正在准备自动重启）"),
        ]

        if stderr_tail:
            sections.extend(
                [
                    "",
                    f"--- 子进程崩溃前 stderr 尾部（共 {len(stderr_tail)} 行）---",
                    "\n".join(stderr_tail),
                ]
            )

        for ctx_path in config.context_logs:
            offset = start_offsets.get(ctx_path, 0)
            content, mode = _read_context_excerpt(ctx_path, offset)
            sections.append("")
            sections.append(f"--- 关联日志: {ctx_path} [{mode}] ---")
            sections.append(content if content else "(无内容)")

        target.write_text("\n".join(sections) + "\n", encoding="utf-8")
        _prune_crash_snapshots(config.crash_dir, config.keep_crashes)
        return target
    except Exception:
        return None


def _pump_stderr(pipe, stderr_tail: deque[str]) -> None:
    """持续读取子进程 stderr，原样回显到终端并保留最后若干行用于崩溃快照。"""
    try:
        for raw_line in iter(pipe.readline, b""):
            try:
                buf = getattr(sys.stderr, "buffer", None)
                if buf is not None:
                    buf.write(raw_line)
                    buf.flush()
                else:
                    sys.stderr.write(raw_line.decode("utf-8", errors="replace"))
                    sys.stderr.flush()
            except Exception:
                pass
            stderr_tail.append(raw_line.decode("utf-8", errors="replace").rstrip("\r\n"))
    except Exception:
        pass
    finally:
        try:
            pipe.close()
        except Exception:
            pass


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
            start_offsets = _snapshot_file_offsets(config.context_logs)
            log_start_offset = (
                config.log_file.stat().st_size
                if (config.log_file is not None and config.log_file.is_file())
                else 0
            )
            stderr_tail: deque[str] = deque(maxlen=120)
            stderr_thread: threading.Thread | None = None
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
                    proc = subprocess.Popen(list(config.command), stderr=subprocess.PIPE)
                    if proc.stderr is not None:
                        stderr_thread = threading.Thread(
                            target=_pump_stderr,
                            args=(proc.stderr, stderr_tail),
                            name=f"watchdog-stderr-{config.name}",
                            daemon=True,
                        )
                        stderr_thread.start()
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

            returncode, health_killed = _wait_child_or_stop(
                proc,
                stop_event,
                config,
                also_stderr=also_stderr,
                log_start_offset=log_start_offset,
            )
            if stderr_thread is not None:
                stderr_thread.join(timeout=1.5)
            elapsed = time.monotonic() - started_at

            if stop_event.is_set():
                _write_log(
                    config.log_file,
                    config.name,
                    f"收到停止信号，已安全结束子进程（运行 {elapsed:.1f}s），守护退出。",
                    also_stderr=also_stderr,
                )
                return 0

            if not health_killed and returncode == 0 and not config.restart_on_zero:
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

            reason = (
                f"健康检查连续失败 {config.health_failures} 次判定假死"
                if health_killed
                else format_exit_reason(returncode)
            )
            crash_count = len(recent_crashes)
            circuit_broken = crash_count >= config.max_rapid_restarts

            snapshot_path = _write_crash_snapshot(
                config,
                reason=reason,
                elapsed=elapsed,
                crash_count=crash_count,
                circuit_broken=circuit_broken,
                stderr_tail=list(stderr_tail),
                start_offsets=start_offsets,
            )
            if snapshot_path is not None:
                _write_log(
                    config.log_file,
                    config.name,
                    f"已保存崩溃现场报告: {snapshot_path}",
                    also_stderr=also_stderr,
                )

            if circuit_broken:
                alert = (
                    f"触发熔断保护：{config.window_sec:.0f}s 内连续异常退出 {crash_count} 次"
                    f"（最近一次 {reason}），已停止自动重启，请检查 logs/money3.log、logs/crash.log 与 logs/crash_fault.log。"
                )
                _write_log(config.log_file, config.name, alert, also_stderr=also_stderr)
                if config.notify:
                    send_macos_notification(
                        "我要发财 · 自动重启已熔断",
                        f"{config.name} 短时间内连续崩溃 {crash_count} 次，已保存崩溃日志请排查。",
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
                msg = f"{config.name} {reason}，正在自动重启恢复…"
                if snapshot_path is not None:
                    msg = f"{config.name} {reason}，已保存崩溃日志并正在自动重启…"
                send_macos_notification("我要发财 · 检测到异常退出", msg)

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
    parser.add_argument("--crash-dir", type=Path, help="崩溃现场快照保存目录")
    parser.add_argument(
        "--context-log",
        dest="context_logs",
        action="append",
        type=Path,
        default=[],
        help="崩溃时需要打包进现场快照的关联日志路径（可多次传入）",
    )
    parser.add_argument("--keep-crashes", type=int, default=30, help="崩溃快照目录最多保留的文件数")
    parser.add_argument("--health-url", help="周期探测的本机健康检查 URL（如 http://127.0.0.1:20241/ready）")
    parser.add_argument("--health-interval", type=float, default=30.0, help="健康检查探测间隔（秒）")
    parser.add_argument("--health-grace", type=float, default=45.0, help="启动后跳过健康检查的宽限时长（秒）")
    parser.add_argument("--health-failures", type=int, default=3, help="连续健康检查失败多少次后判定假死并重启")
    parser.add_argument("--keepalive-url-regex", help="从 --log-file 提取最新公网保活探测基准 URL 的正则")
    parser.add_argument("--keepalive-path", default="/", help="公网保活探测附加路径（默认 /）")
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
        crash_dir=ns.crash_dir,
        context_logs=tuple(ns.context_logs),
        keep_crashes=max(1, ns.keep_crashes),
        health_url=ns.health_url,
        health_interval=max(0.1, ns.health_interval),
        health_grace=max(0.0, ns.health_grace),
        health_failures=max(1, ns.health_failures),
        keepalive_url_regex=ns.keepalive_url_regex,
        keepalive_path=ns.keepalive_path or "/",
    )


def main(argv: Sequence[str] | None = None) -> int:
    """CLI 入口。"""
    config = parse_args(argv)
    return run_watchdog(config)


if __name__ == "__main__":
    raise SystemExit(main())
