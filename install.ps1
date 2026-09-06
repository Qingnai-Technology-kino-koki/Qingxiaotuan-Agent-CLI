# 青小团 (qxt) Windows 一键安装脚本
# 用法 (PowerShell):
#   cd 到项目目录后执行:  .\install.ps1
# 脚本会: 建 venv -> 安装依赖 -> 把 qxt 所在目录加入用户 PATH -> 完成后告诉你怎么用。
#
# 设计原则: 不修改系统 Python, 全部隔离在项目 .venv 内; PATH 只加用户级, 不动系统变量。

$ErrorActionPreference = "Stop"

$ProjectDir = $PSScriptRoot
$VenvDir   = Join-Path $ProjectDir ".venv"
$ScriptsDir = Join-Path $VenvDir "Scripts"
$QxtExe    = Join-Path $ScriptsDir "qxt.exe"
$PyExe     = Join-Path $ScriptsDir "python.exe"

Write-Host "==> 青小团安装开始: $ProjectDir" -ForegroundColor Cyan

# 0. 前置检查: 找一个「带 pip」的 python。
#    注意: 某些发行版 / uv 管理的解释器虽能被 `python` 解析, 但其 venv 不含 pip,
#    会导致后续 `pip install` 静默崩溃。故优先探测真正能用 pip 的解释器。
function Find-UsablePython {
    $candidates = @("py", "python3", "python")
    foreach ($c in $candidates) {
        try {
            & $c --version 2>&1 | Out-Null
            if ($LASTEXITCODE -ne 0) { continue }
            & $c -m pip --version 2>&1 | Out-Null
            if ($LASTEXITCODE -eq 0) { return $c }
        } catch { }
    }
    return $null
}

$PyCmd = Find-UsablePython
if (-not $PyCmd) {
    Write-Host "!! 未找到带 pip 的 python。" -ForegroundColor Red
    Write-Host "   请安装标准 Python 3.10+ (https://www.python.org) 并勾选 'Add to PATH' 后重试。" -ForegroundColor Yellow
    Write-Host "   (某些 uv 管理的解释器 venv 不含 pip, 不被本脚本支持。)" -ForegroundColor DarkGray
    exit 1
}
Write-Host "==> 使用 python: $(& $PyCmd --version 2>&1)" -ForegroundColor DarkGray

# 1. 虚拟环境
if (-not (Test-Path $VenvDir)) {
    Write-Host "==> 创建虚拟环境 .venv ..." -ForegroundColor Cyan
    & $PyCmd -m venv $VenvDir
    if (-not (Test-Path $PyExe)) {
        Write-Host "!! 创建虚拟环境失败。" -ForegroundColor Red
        exit 1
    }
} else {
    Write-Host "==> .venv 已存在, 跳过创建" -ForegroundColor DarkGray
}

# 1b. 确保 venv 内有 pip (极少数发行版 venv 缺 pip, 用 ensurepip 引导)
& "$PyExe" -m pip --version 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "==> venv 内未检测到 pip, 尝试 ensurepip 引导..." -ForegroundColor Yellow
    & "$PyExe" -m ensurepip --upgrade 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "!! 无法在 venv 中引导 pip。请改用标准 Python 3.10+ (含 pip) 后重试。" -ForegroundColor Red
        exit 1
    }
}

# 2. 安装 (editable)
Write-Host "==> 安装青小团 (pip install -e .) ..." -ForegroundColor Cyan
& "$PyExe" -m pip install --quiet --upgrade pip setuptools wheel
& "$PyExe" -m pip install -e . --no-build-isolation
if (-not (Test-Path $QxtExe)) {
    Write-Host "!! 安装似乎失败: 找不到 $QxtExe" -ForegroundColor Red
    exit 1
}

# 3. 加入用户 PATH (仅当前用户, 不影响系统) —— 并验证真正写进去了
$UserPath = [Environment]::GetEnvironmentVariable("Path", "User")
$PathEntries = @($UserPath -split ';' | Where-Object { $_ -and $_.Trim() })
$AlreadyPresent = $PathEntries | Where-Object { $_.TrimEnd('\') -ieq $ScriptsDir.TrimEnd('\') }
if (-not $AlreadyPresent) {
    Write-Host "==> 把 $ScriptsDir 加入用户 PATH ..." -ForegroundColor Cyan
    $PathEntries += $ScriptsDir
    [Environment]::SetEnvironmentVariable("Path", ($PathEntries -join ';'), "User")
}
# 校验: 注册表写完后重新读取确认
$SavedPath = [Environment]::GetEnvironmentVariable("Path", "User")
if (-not (@($SavedPath -split ';') | Where-Object { $_.TrimEnd('\') -ieq $ScriptsDir.TrimEnd('\') })) {
    Write-Host "!! 写入用户 PATH 失败, 请手动把下面这行加入系统环境变量 Path:" -ForegroundColor Red
    Write-Host "   $ScriptsDir" -ForegroundColor Yellow
} else {
    Write-Host "    PATH 已就绪。" -ForegroundColor Green
}
# 当前进程立即生效 (本次安装脚本内可验证)
$CurrentEntries = @($env:Path -split ';' | Where-Object { $_ -and $_.Trim() })
if (-not ($CurrentEntries | Where-Object { $_.TrimEnd('\') -ieq $ScriptsDir.TrimEnd('\') })) {
    $env:Path = (($CurrentEntries + $ScriptsDir) -join ';')
}

# 4. 收尾提示
Write-Host ""
Write-Host "==> 安装完成!" -ForegroundColor Green
$resolved = Get-Command qxt -ErrorAction SilentlyContinue
if ($resolved) {
    Write-Host "    验证: 当前环境已能解析 qxt -> $($resolved.Source)" -ForegroundColor Green
} else {
    Write-Host "    注意: 用户 PATH 已写入；已有终端不会自动读取新环境变量。" -ForegroundColor Yellow
    Write-Host "    请关闭并重新打开 PowerShell/Windows Terminal 后运行 qxt。" -ForegroundColor Yellow
}
Write-Host ""
Write-Host "  接下来 (请在一个『真实终端』里操作, 不要在本软件/IDE 内嵌命令行里跑):" -ForegroundColor Cyan
Write-Host "    1) 打开 命令提示符 / Windows Terminal" -ForegroundColor White
Write-Host "    2) 输入:  qxt              # 直接进交互界面, 就能打字了" -ForegroundColor White
Write-Host "    3) 首次使用先配模型:  qxt models   (开箱支持多家平台)" -ForegroundColor White
Write-Host "    4) 无限制模式:  qxt --yolo        # 危险操作自动批准" -ForegroundColor White
Write-Host ""
Write-Host "  也可以直接双击项目里的 qxt.bat 启动。" -ForegroundColor DarkGray
