"""
Verification script for Unified Launcher (Desktop + Mobile)
Checks:
1. Stopping mobile service via launcher flag
2. Starting mobile service via launcher
3. Port 8888 API endpoints responsiveness
4. Desktop module imports and entry point validity
5. Network IP detection accuracy (LAN IP: 192.168.100.27)
6. Help message verification
"""
import os
import sys
import time
import subprocess
import urllib.request
import json

APP_DIR = "/Users/tangtao/money3"

def run_cmd(cmd_list):
    res = subprocess.run(cmd_list, cwd=APP_DIR, capture_output=True, text=True)
    return res.returncode, res.stdout, res.stderr

def test_stop_mobile():
    print("[1/6] Testing stopping mobile service via launcher...")
    code, stdout, stderr = run_cmd(["/bin/zsh", "./启动Money3.command", "--stop-mobile"])
    print("  Output:", stdout.strip())
    assert code == 0, f"Expected code 0, got {code}"
    time.sleep(1)
    
    # Check port 8888 is now free
    code2, stdout2, _ = run_cmd(["lsof", "-ti", "tcp:8888", "-sTCP:LISTEN"])
    assert stdout2.strip() == "", f"Port 8888 should be free, but found PID {stdout2.strip()}"
    print("  -> Port 8888 is confirmed FREE.")

def test_start_mobile():
    print("[2/6] Testing starting mobile service via launcher...")
    # Start via detached launcher
    proc = subprocess.Popen(
        ["/bin/zsh", "./启动Money3.command", "--only-mobile"],
        cwd=APP_DIR,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )
    time.sleep(2)
    
    # Check port 8888 is active
    code, stdout, _ = run_cmd(["lsof", "-ti", "tcp:8888", "-sTCP:LISTEN"])
    pid = stdout.strip()
    assert pid != "", "Port 8888 should be in LISTEN state"
    print(f"  -> Mobile service is running with PID {pid}")
    
    # Check endpoints
    endpoints = [
        "http://127.0.0.1:8888/api/overview",
        "http://127.0.0.1:8888/api/ai/status",
        "http://127.0.0.1:8888/api/ai/filters",
        "http://127.0.0.1:8888/api/ai/stock/history?symbol=AAOX"
    ]
    for ep in endpoints:
        resp = urllib.request.urlopen(ep, timeout=5)
        assert resp.status == 200, f"Endpoint {ep} failed with status {resp.status}"
        data = json.loads(resp.read().decode('utf-8'))
        print(f"  -> Endpoint {ep} OK")

def test_help_option():
    print("[3/6] Testing --help flag...")
    code, stdout, _ = run_cmd(["/bin/zsh", "./启动Money3.command", "--help"])
    assert code == 0, f"Expected code 0, got {code}"
    assert "双端联合启动器" in stdout or "常规启动选项" in stdout
    print("  -> Help flag outputs correct launcher usage.")

def test_desktop_module_integrity():
    print("[4/6] Testing desktop app entrypoint & PySide6 integrity...")
    venv_py = os.path.join(APP_DIR, ".venv", "bin", "python")
    code, stdout, stderr = run_cmd([
        venv_py, "-c",
        "import portfolio; from portfolio.ui.main_window import MainWindow; print('Desktop module & MainWindow import OK')"
    ])
    assert code == 0, f"Desktop import failed: {stderr}"
    print(f"  -> {stdout.strip()}")

def test_ip_detection():
    print("[5/6] Testing network IP detection...")
    code, stdout, _ = run_cmd([
        "/bin/zsh", "-c",
        r"ifconfig | grep -E '^\s*inet ' | awk '{print $2}' | grep -v '^127\.' | grep -v '^198\.18\.' | grep -v '^100\.' | head -n 1"
    ])
    lan_ip = stdout.strip()
    assert lan_ip == "192.168.100.27", f"Expected 192.168.100.27, got {lan_ip}"
    print(f"  -> LAN IP successfully detected: {lan_ip}")

def test_mobile_command_alias():
    print("[6/6] Testing 启动手机服务.command alias...")
    code, stdout, _ = run_cmd(["/bin/zsh", "./启动手机服务.command", "--help"])
    assert code == 0, f"Expected code 0, got {code}"
    assert "启动选项" in stdout or "Money Portfolio" in stdout
    print("  -> 启动手机服务.command correctly forwards to unified launcher.")

if __name__ == "__main__":
    test_stop_mobile()
    test_start_mobile()
    test_help_option()
    test_desktop_module_integrity()
    test_ip_detection()
    test_mobile_command_alias()
    print("\n✅ All 6 Unified Launcher tests passed successfully!")
