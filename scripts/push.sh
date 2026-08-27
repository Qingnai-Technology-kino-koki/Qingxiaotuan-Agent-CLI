#!/usr/bin/env bash
# 一键推送到 GitHub (Qingnai-Technology/Qingxiaotuan-Agent)
# 用法:
#   1. 先在 GitHub 网页创建空仓库 (Public, 不要勾选 README/.gitignore)
#   2. 生成 Personal Access Token (勾 repo 权限): GitHub -> Settings -> Developer settings -> PAT
#   3. 运行:  bash scripts/push.sh
#      用户名填你的 GitHub 账号, 密码处粘贴 token
#
# 安全说明: 本脚本不关闭任何 TLS 校验。Windows 下若遇
# CRYPT_E_NO_REVOCATION_CHECK (schannel 吊销检查怪象), 推送失败时
# 会给出手动绕过提示, 由你自行判断网络环境后决定是否使用。
set -e
cd "$(dirname "$0")/.."

REMOTE="https://github.com/Qingnai-Technology/Qingxiaotuan-Agent.git"

# 幂等设置 origin: 不存在则添加, 指向不同则更新, 不做破坏性删除重建
CURRENT="$(git remote get-url origin 2>/dev/null || true)"
if [ -z "$CURRENT" ]; then
    git remote add origin "$REMOTE"
elif [ "$CURRENT" != "$REMOTE" ]; then
    git remote set-url origin "$REMOTE"
fi

echo "准备推送 main 分支到 $REMOTE"
echo "提示: 若用 HTTPS, 用户名=GitHub 账号, 密码=Personal Access Token (非登录密码)"

if ! git push -u origin main; then
    echo ""
    echo "❌ 推送失败。"
    echo "   若错误为 CRYPT_E_NO_REVOCATION_CHECK (Windows schannel 吊销检查失败),"
    echo "   可在确认网络环境可信后手动重试:"
    echo "     git -c http.schannelCheckRevoke=false push -u origin main"
    exit 1
fi

echo ""
echo "✅ 推送完成。访问 https://github.com/Qingnai-Technology/Qingxiaotuan-Agent 查看。"
