#!/usr/bin/env bash
# 一键推送到 GitHub (Qingnai-Tech/Qingxiaotuan-Agent)
# 用法:
#   1. 先在 GitHub 网页创建空仓库 (Public, 不要勾选 README/.gitignore)
#   2. 生成 Personal Access Token (勾 repo 权限): GitHub -> Settings -> Developer settings -> PAT
#   3. 运行:  bash scripts/push.sh
#      用户名填你的 GitHub 账号, 密码处粘贴 token
#
# 本机若遇 schannel 证书吊销检查失败, 已内置 -c http.schannelCheckRevoke=false 绕过。
set -e
cd "$(dirname "$0")/.."

REMOTE="https://github.com/Qingnai-Tech/Qingxiaotuan-Agent.git"
git remote remove origin 2>/dev/null || true
git remote add origin "$REMOTE"

echo "准备推送 main 分支到 $REMOTE"
echo "提示: 若用 HTTPS, 用户名=GitHub 账号, 密码=Personal Access Token (非登录密码)"
git -c http.schannelCheckRevoke=false -c http.sslVerify=false push -u origin main

echo ""
echo "✅ 推送完成。访问 https://github.com/Qingnai-Tech/Qingxiaotuan-Agent 查看。"
echo "下一步建议: 在 Releases 页上传预编译的 C 引擎包 (ext/dist/bin/*.exe), 降低用户门槛。"
