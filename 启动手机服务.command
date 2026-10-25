#!/bin/zsh
set -e
APP_DIR="${0:A:h}"
cd "$APP_DIR"
export MONEY3_HOME="$APP_DIR"

echo "=================================================================="
echo "  📱 正在启动 Money Portfolio 移动端 / iPhone Web 服务..."
echo "=================================================================="
echo ""

# Find LAN IP address
WIFI_IP=$(ifconfig | grep "inet " | grep -v 127.0.0.1 | grep -v "198.18." | awk '{print $2}' | head -n 1)

if [ -z "$WIFI_IP" ]; then
    WIFI_IP="127.0.0.1"
fi

echo "  ✅ 服务启动成功！"
echo ""
echo "  【同 WiFi 局域网访问】"
echo "  在 iPhone 的 Safari 浏览器打开："
echo "  👉 http://${WIFI_IP}:8888"
echo ""
echo "  【添加为手机桌面 App】"
echo "  1. 用 iPhone 打开上述网址；"
echo "  2. 点击 Safari 底部「分享」按钮（向上箭头图标）；"
echo "  3. 点击「添加到主屏幕」即可全屏免浏览器运行！"
echo ""
echo "=================================================================="
echo "  （按 Ctrl + C 可停止服务）"
echo "=================================================================="
echo ""

./.venv/bin/python -m portfolio.web.app
