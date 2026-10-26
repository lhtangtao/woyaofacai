#!/bin/zsh
APP_DIR="${0:A:h}"
cd "$APP_DIR"
export MONEY3_HOME="$APP_DIR"

print "=================================================================="
print "  🔐 Money Portfolio 本地安全账户管理工具"
print "=================================================================="
print ""

"$APP_DIR/.venv/bin/python" -m portfolio.web.security.cli list-users
print ""
print "操作菜单:"
print "  1. 修改/设置管理员 (admin) 密码"
print "  2. 修改/设置用户 (拉科) 密码"
print "  3. 创建新账户"
print "  4. 退出"
print ""
read -r "?请选择操作 [1-4]: " choice

case "$choice" in
    1)
        read -s -r "?请输入 admin 新密码: " p1
        print ""
        read -s -r "?请再次确认新密码: " p2
        print ""
        if [[ "$p1" != "$p2" || -z "$p1" ]]; then
            print "❌ 两次输入的密码不一致或为空！"
            exit 1
        fi
        "$APP_DIR/.venv/bin/python" -m portfolio.web.security.cli set-password --username admin --password "$p1"
        ;;
    2)
        read -s -r "?请输入 拉科 新密码: " p1
        print ""
        read -s -r "?请再次确认新密码: " p2
        print ""
        if [[ "$p1" != "$p2" || -z "$p1" ]]; then
            print "❌ 两次输入的密码不一致或为空！"
            exit 1
        fi
        "$APP_DIR/.venv/bin/python" -m portfolio.web.security.cli set-password --username "拉科" --password "$p1"
        ;;
    3)
        read -r "?请输入新用户名: " uname
        read -s -r "?请输入密码: " p1
        print ""
        read -r "?请输入归属人员 ID (普通用户填对应数字如 5，管理员留空): " pid
        if [[ -n "$pid" ]]; then
            "$APP_DIR/.venv/bin/python" -m portfolio.web.security.cli create-user --username "$uname" --password "$p1" --role user --person-id "$pid"
        else
            "$APP_DIR/.venv/bin/python" -m portfolio.web.security.cli create-user --username "$uname" --password "$p1" --role admin
        fi
        ;;
    *)
        print "已退出。"
        exit 0
        ;;
esac

print ""
print "按回车键退出..."
read -r
