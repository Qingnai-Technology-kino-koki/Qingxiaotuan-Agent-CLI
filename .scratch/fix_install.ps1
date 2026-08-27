# 青小团 Python 全栈修复安装脚本
# 运行: powershell -ExecutionPolicy Bypass -File fix_install.ps1

Write-Host "=== 青小团 Python 全栈修复安装 ===" -ForegroundColor Cyan

# 进入项目目录
Set-Location "E:\Qingxiaotuan Agent CLI"

Write-Host "[1/4] 清理损坏的 pip 残留 (~*.dist-info 中断安装产物)..." -ForegroundColor Yellow
Get-ChildItem ".venv\Lib\site-packages" -Directory -Filter "~*.dist-info" -ErrorAction SilentlyContinue |
    ForEach-Object { Remove-Item -Recurse -Force $_.FullName; Write-Host "  已移除 $($_.Name)" -ForegroundColor DarkGray }

Write-Host "[2/4] 重新安装项目到 .venv..." -ForegroundColor Yellow
.\ .venv\Scripts\pip.exe install -e . --upgrade
if ($LASTEXITCODE -ne 0) {
    Write-Host "pip install -e . 失败, 尝试直接安装依赖..." -ForegroundColor Red
}

Write-Host "[3/4] 确保关键依赖已安装..." -ForegroundColor Yellow
.\ .venv\Scripts\pip.exe install pyyaml cryptography openai rich prompt_toolkit httpx --quiet
if ($LASTEXITCODE -eq 0) {
    Write-Host "  依赖安装成功" -ForegroundColor Green
} else {
    Write-Host "  部分依赖可能缺失, 请手动运行: pip install pyyaml cryptography openai rich prompt_toolkit httpx" -ForegroundColor Red
}

Write-Host "[4/4] 测试 qxt 能否启动..." -ForegroundColor Yellow
.\ .venv\Scripts\python.exe -c "import yaml; print('yaml OK')"
.\ .venv\Scripts\python.exe -c "from qingxiaotuan.tools.external import ExternalToolsPlugin; print('ExternalToolsPlugin OK')"

Write-Host ""
Write-Host "=== 完成! 现在可以运行: qxt ===" -ForegroundColor Green
