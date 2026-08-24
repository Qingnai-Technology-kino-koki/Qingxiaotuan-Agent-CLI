@echo off
REM 青小团 (qxt) 启动器 —— 双击即用, 无需记命令。
REM 作用: 在项目虚拟环境里启动 qxt 交互界面。
REM 若已把 .venv\Scripts 加入 PATH, 也可直接在任意终端敲 qxt。

setlocal
set "DIR=%~dp0"
set "VENV=%DIR%.venv\Scripts"

if not exist "%VENV%\qxt.exe" (
    echo [青小团] 未找到 .venv\Scripts\qxt.exe
    echo 请先运行 install.ps1 完成安装 (在项目目录里执行: .\install.ps1)
    pause
    exit /b 1
)

REM 把 venv 的 Scripts 临时放到 PATH 前面, 确保 qxt 可被找到
set "PATH=%VENV%;%PATH%"

if "%1"=="" (
    "%VENV%\qxt.exe"
) else (
    "%VENV%\qxt.exe" %*
)
endlocal
