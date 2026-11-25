"""测试崩溃自动重启守护脚本 scripts/watchdog.py 与原生崩溃堆栈日志启用。"""

from __future__ import annotations

import faulthandler
import importlib.util
import signal
import subprocess
import sys
import time
from pathlib import Path

from portfolio import logging_config


def _load_watchdog_module():
    """直接按路径加载 scripts/watchdog.py，避免依赖 scripts 是否为包。"""
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "watchdog.py"
    spec = importlib.util.spec_from_file_location("money3_watchdog", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_watchdog_normal_exit_without_restart(tmp_path: Path) -> None:
    """桌面端正常退出（退出码 0 且未开启 restart_on_zero）应直接退出且不重启。"""
    watchdog = _load_watchdog_module()
    count_file = tmp_path / "runs.txt"
    pid_file = tmp_path / "desktop.pid"
    log_file = tmp_path / "watchdog.log"

    child_code = (
        f"from pathlib import Path; "
        f"p = Path({str(count_file)!r}); "
        f"p.open('a', encoding='utf-8').write('1\\n')"
    )
    cfg = watchdog.WatchdogConfig(
        name="desktop",
        command=(sys.executable, "-c", child_code),
        pid_file=pid_file,
        log_file=log_file,
        restart_on_zero=False,
        backoff_base=0.05,
        backoff_max=0.1,
    )

    code = watchdog.run_watchdog(cfg)
    assert code == 0
    assert count_file.read_text(encoding="utf-8").splitlines() == ["1"]
    assert not pid_file.exists()
    assert "无需重启" in log_file.read_text(encoding="utf-8")


def test_watchdog_auto_restarts_after_crash_and_recovers(tmp_path: Path) -> None:
    """子进程连续崩溃 2 次后第 3 次恢复正常退出，守护应自动拉起并最终返回 0。"""
    watchdog = _load_watchdog_module()
    count_file = tmp_path / "runs.txt"
    pid_file = tmp_path / "desktop.pid"
    log_file = tmp_path / "watchdog.log"

    child_code = (
        f"import sys; from pathlib import Path; "
        f"p = Path({str(count_file)!r}); "
        f"p.open('a', encoding='utf-8').write('x\\n'); "
        f"n = len(p.read_text(encoding='utf-8').splitlines()); "
        f"sys.exit(1 if n < 3 else 0)"
    )
    cfg = watchdog.WatchdogConfig(
        name="desktop",
        command=(sys.executable, "-c", child_code),
        pid_file=pid_file,
        log_file=log_file,
        restart_on_zero=False,
        max_rapid_restarts=5,
        window_sec=10.0,
        backoff_base=0.03,
        backoff_max=0.06,
    )

    code = watchdog.run_watchdog(cfg)
    assert code == 0
    assert len(count_file.read_text(encoding="utf-8").splitlines()) == 3
    log_text = log_file.read_text(encoding="utf-8")
    assert "自动重启" in log_text
    assert "无需重启" in log_text
    assert not pid_file.exists()


def test_watchdog_circuit_breaker_on_rapid_crashes(tmp_path: Path) -> None:
    """短时间内连续崩溃达到熔断上限后，应停止重启并返回非零退出码。"""
    watchdog = _load_watchdog_module()
    count_file = tmp_path / "runs.txt"
    log_file = tmp_path / "watchdog.log"

    child_code = (
        f"import sys; from pathlib import Path; "
        f"p = Path({str(count_file)!r}); "
        f"p.open('a', encoding='utf-8').write('boom\\n'); "
        f"sys.exit(2)"
    )
    cfg = watchdog.WatchdogConfig(
        name="desktop",
        command=(sys.executable, "-c", child_code),
        log_file=log_file,
        max_rapid_restarts=3,
        window_sec=10.0,
        backoff_base=0.02,
        backoff_max=0.04,
    )

    code = watchdog.run_watchdog(cfg)
    assert code == 2
    assert len(count_file.read_text(encoding="utf-8").splitlines()) == 3
    assert "触发熔断保护" in log_file.read_text(encoding="utf-8")


def test_watchdog_restart_on_zero_and_sigterm_graceful_stop(tmp_path: Path) -> None:
    """开启 restart_on_zero 时零退出也会自动拉起；收到 SIGTERM 时应安全终止子进程并清理 PID 文件。"""
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "watchdog.py"
    count_file = tmp_path / "runs.txt"
    pid_file = tmp_path / "web_watchdog.pid"
    log_file = tmp_path / "web.log"

    # 第 1 次立即返回 0（触发重启）；第 2 次持续等待直到被守护发送 SIGTERM 终止。
    child_code = (
        f"import sys, time; from pathlib import Path; "
        f"p = Path({str(count_file)!r}); "
        f"p.open('a', encoding='utf-8').write('run\\n'); "
        f"n = len(p.read_text(encoding='utf-8').splitlines()); "
        f"time.sleep(30 if n >= 2 else 0); "
        f"sys.exit(0)"
    )
    proc = subprocess.Popen(
        [
            sys.executable,
            str(script_path),
            "--name",
            "web",
            "--restart-on-zero",
            "--redirect-child-output",
            "--pid-file",
            str(pid_file),
            "--log-file",
            str(log_file),
            "--backoff-base",
            "0.05",
            "--backoff-max",
            "0.05",
            "--",
            sys.executable,
            "-c",
            child_code,
        ]
    )
    try:
        deadline = time.monotonic() + 8.0
        while time.monotonic() < deadline:
            if count_file.exists() and len(count_file.read_text(encoding="utf-8").splitlines()) >= 2:
                break
            time.sleep(0.05)
        assert count_file.exists()
        assert len(count_file.read_text(encoding="utf-8").splitlines()) >= 2
        assert pid_file.exists()

        proc.send_signal(signal.SIGTERM)
        ret = proc.wait(timeout=8.0)
        assert ret == 0
        assert not pid_file.exists()
        assert "收到停止信号" in log_file.read_text(encoding="utf-8")
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=3.0)


def test_enable_fault_handler_creates_log_file(tmp_path: Path) -> None:
    """验证 enable_fault_handler 会初始化 crash_fault.log 并开启 faulthandler。"""
    was_enabled = faulthandler.is_enabled()
    try:
        target = logging_config.enable_fault_handler(logs_dir=tmp_path)
        assert target == tmp_path / "crash_fault.log"
        assert target.is_file()
        assert faulthandler.is_enabled() is True
        assert "启用原生崩溃堆栈采集" in target.read_text(encoding="utf-8")
    finally:
        if logging_config._FAULT_HANDLER_STREAM is not None:
            try:
                logging_config._FAULT_HANDLER_STREAM.close()
            except Exception:
                pass
            logging_config._FAULT_HANDLER_STREAM = None
        if not was_enabled and faulthandler.is_enabled():
            faulthandler.disable()
