#!/bin/zsh
set -e
APP_DIR="${0:A:h}"
cd "$APP_DIR"
export MONEY3_HOME="$APP_DIR"

echo "=================================================================="
echo "  🚀 正在启动 Money Portfolio 移动端全量工作站 (iPhone / iPad)..."
echo "=================================================================="
echo ""

# Find LAN IP address
WIFI_IP=$(ifconfig | grep "inet " | grep -v 127.0.0.1 | grep -v "198.18." | grep -v "100." | awk '{print $2}' | head -n 1)

# Find Tailscale IP address if active
TAILSCALE_IP=$(ifconfig | grep "inet 100\." | awk '{print $2}' | head -n 1)
if [ -z "$TAILSCALE_IP" ]; then
    if which tailscale &>/dev/null; then
        TAILSCALE_IP=$(tailscale ip -4 2>/dev/null | head -n 1)
    fi
fi

if [ -z "$WIFI_IP" ]; then
    WIFI_IP="127.0.0.1"
fi

echo "  ✅ 手机全量工作站已就绪！"
echo ""
echo "  【1. 家中同一 WiFi 局域网访问】"
echo "  打开 iPhone Safari 浏览器输入："
echo "  👉 http://${WIFI_IP}:8888"
echo ""

if [ -n "$TAILSCALE_IP" ]; then
    echo "  【2. 户外公网 5G 远程访问 (Tailscale 已连接)】"
    echo "  打开 iPhone Safari 浏览器输入："
    echo "  👉 http://${TAILSCALE_IP}:8888"
    echo ""
else
    echo "  【2. 户外公网 5G 远程访问 (Tailscale)】"
    echo "  如需在户外通过 5G 随时访问家中 Mac："
    echo "  • Mac 与 iPhone 均下载 Tailscale (App Store 免费) 并登录同账号"
    echo "  • 连接后即可通过专属 Tailscale 网址随时直连！"
    echo ""
fi

echo "  【3. 一键添加为 iPhone 原生全屏桌面 App】"
echo "  1. 用 iPhone 打开上述网址；"
echo "  2. 点击 Safari 底部「分享」按钮（方框向上箭头图标）；"
echo "  3. 点击「添加到主屏幕」，即可免浏览器全屏流畅运行！"
echo ""
echo "=================================================================="
echo "  （提示：按 Ctrl + C 可退出服务）"
echo "=================================================================="
echo ""

./.venv/bin/python -m portfolio.web.app
