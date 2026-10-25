#!/bin/zsh
cd "$(dirname "$0")"

echo "========================================================"
echo "  正在推送 Money3 到 GitHub (lhtangtao/woyaofacai)..."
echo "========================================================"
echo ""

git remote set-url origin https://github.com/lhtangtao/woyaofacai.git
git push -u origin main

if [ $? -eq 0 ]; then
    echo ""
    echo "========================================================"
    echo "  🎉 推送成功！刷新 GitHub 网页即可看到 money3 仓库！"
    echo "========================================================"
else
    echo ""
    echo "⚠️ 推送未完成，请确认："
    echo "1. 是否已经在网页端点击了「Create repository」创建 money3？"
    echo "2. 若提示输入密码，请输入 GitHub Personal Access Token (经典令牌)。"
fi

echo ""
echo "按 Enter 键退出..."
read
