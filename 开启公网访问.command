#!/bin/zsh
set -e
APP_DIR="${0:A:h}"
cd "$APP_DIR"
export MONEY3_HOME="$APP_DIR"

NGROK_BIN="$APP_DIR/tools/ngrok"
NGROK_LOG="/tmp/money_ngrok.log"
MOBILE_LOG="/tmp/money_mobile.log"

print "=================================================================="
print "  🌐 Money Portfolio 手机公网访问直连启动器 (免装App/浏览器秒开)"
print "=================================================================="

# 1. 确保手机端 Web 服务已在运行 (端口 8888)
if ! lsof -ti tcp:8888 -sTCP:LISTEN &>/dev/null; then
    print "  🚀 正在启动后台 Web 服务 (端口 8888)..."
    nohup "$APP_DIR/.venv/bin/python" -m portfolio.web.app >> "$MOBILE_LOG" 2>&1 &
    for i in {1..25}; do
        if lsof -ti tcp:8888 -sTCP:LISTEN &>/dev/null; then
            break
        fi
        sleep 0.2
    done
fi

# 2. 检查 ngrok 工具与 Token 授权
if [[ ! -x "$NGROK_BIN" ]]; then
    print "❌ 未找到 ngrok 工具，请确认 tools/ngrok 存在。"
    exit 1
fi

if ! "$NGROK_BIN" config check &>/dev/null; then
    print ""
    print "【首次配置向导：仅需 1 分钟绑定免费 Authtoken】"
    print "为了给您分配一个专属、免费且永久固定的公网网址，只需授权一次："
    print "  1. 打开浏览器访问: https://dashboard.ngrok.com/signup"
    print "     (推荐直接点击「Continue with Google」或「Continue with GitHub」1 秒登录)"
    print "  2. 登录后在页面顶部或左侧复制您的「Your Authtoken」(一串英文数字字符串)"
    print "------------------------------------------------------------------"
    read -r "?请在此粘贴您的 Authtoken 并按回车: " user_token
    user_token="${user_token// /}"

    if [[ -z "$user_token" ]]; then
        print "❌ Token 不能为空，启动已取消。"
        exit 1
    fi

    "$NGROK_BIN" config add-authtoken "$user_token"
    print "✅ Authtoken 配置成功！\n"
fi

# 3. 杀死旧的 ngrok 进程 (若有)
pkill -f "ngrok http 8888" 2>/dev/null || true
sleep 0.5

# 4. 启动 ngrok 隧道
print "  🔗 正在建立安全加密公网隧道..."
"$NGROK_BIN" http 8888 --log=stdout > "$NGROK_LOG" 2>&1 &
NGROK_PID=$!

PUBLIC_URL=""
for i in {1..35}; do
    sleep 0.3
    # 从 ngrok 本地 API 提取分配的公网 URL
    PUBLIC_URL=$("$APP_DIR/.venv/bin/python" -c "
import urllib.request, json
try:
    with urllib.request.urlopen('http://127.0.0.1:4040/api/tunnels', timeout=1) as resp:
        data = json.loads(resp.read().decode())
        tunnels = data.get('tunnels', [])
        for t in tunnels:
            if t.get('proto') == 'https':
                print(t.get('public_url', ''))
                break
        else:
            if tunnels:
                print(tunnels[0].get('public_url', ''))
except Exception:
    pass
" 2>/dev/null || true)

    if [[ -n "$PUBLIC_URL" ]]; then
        break
    fi
done

if [[ -z "$PUBLIC_URL" ]]; then
    print "\n❌ 获取公网链接超时，请检查网络或日志: $NGROK_LOG"
    cat "$NGROK_LOG" | tail -n 10
    kill "$NGROK_PID" 2>/dev/null || true
    exit 1
fi

# 自动复制公网地址到系统剪贴板
echo "$PUBLIC_URL" | pbcopy 2>/dev/null || true

print ""
print "=================================================================="
print "  🎉 公网直连隧道已成功就绪！"
print "=================================================================="
print "  👉 手机公网访问网址 (已自动复制到 Mac 剪贴板！)："
print "     $PUBLIC_URL"
print ""
print "  👤【拉科专属登录信息】"
print "     • 用户名: 拉科"
print "     • 密  码: lake123"
print "     (手机浏览器打开上述网址后，在安全登录框输入即可秒级进入)"
print ""
print "  📱【使用说明】"
print "     1. 您可直接在微信/信息中通过 Cmd+V 粘贴网址发给拉科；"
print "     2. 手机端完全无需下载任何 App 或 VPN，自带浏览器直接打开；"
print "     3. 进入后点击 Safari 底部「分享」->「添加到主屏幕」即可像 App 一样用；"
print "     4. 保持此终端窗口开启即可持续提供公网访问。若需关闭，按 Ctrl + C 即可。"
print "=================================================================="

trap "kill $NGROK_PID 2>/dev/null || true; print '\nℹ️ 公网隧道已安全关闭。'; exit 0" INT TERM

wait "$NGROK_PID"
