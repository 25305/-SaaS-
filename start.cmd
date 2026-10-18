@echo off
chcp 65001 >nul
setlocal
title SmartQA 一键启动

rem ===========================================================================
rem  智能问答 SaaS 平台 · 一键启动
rem
rem  用法：双击本文件，或在本目录执行  start.cmd
rem  作用：检查环境 → 按需安装依赖 → 启动后端(8000) → 启动前端(5173) → 打开浏览器
rem  停止：关闭弹出的「后端」与「前端」两个窗口即可
rem
rem  唯一后端位于 问答平台\backend，前端位于 frontend（仓库根目录）。
rem ===========================================================================

cd /d "%~dp0"
set "ROOT=%~dp0"
set "BACKEND=%ROOT%问答平台\backend"
set "FRONTEND=%ROOT%frontend"

echo.
echo ============================================================
echo   智能问答 SaaS 平台 · 一键启动
echo ============================================================
echo.

rem ---------------------- 1. 定位 Python ----------------------
set "PY=python"
where python >nul 2>nul
if not errorlevel 1 goto :have_py
where py >nul 2>nul
if errorlevel 1 goto :no_python
set "PY=py -3"

:have_py
for /f "delims=" %%v in ('%PY% --version 2^>^&1') do echo [环境] %%v

rem ---------------------- 2. 定位 Node ----------------------
where node >nul 2>nul
if errorlevel 1 goto :no_node
for /f "delims=" %%v in ('node --version 2^>^&1') do echo [环境] Node %%v

rem ---------------------- 3. 检查目录 ----------------------
if not exist "%BACKEND%\app\main.py" goto :no_backend
if not exist "%FRONTEND%\package.json" goto :no_frontend

rem ---------------------- 4. 后端依赖（按需安装） ----------------------
pushd "%BACKEND%"
%PY% -c "import fastapi, sqlalchemy, uvicorn, pydantic, dotenv" >nul 2>nul
if errorlevel 1 (
  echo [安装] 后端依赖缺失，正在执行 pip install -r requirements.txt ...
  %PY% -m pip install -r requirements.txt
  if errorlevel 1 (
    popd
    goto :pip_fail
  )
) else (
  echo [跳过] 后端依赖已就绪
)
popd

rem ---------------------- 5. 前端依赖（按需安装） ----------------------
if exist "%FRONTEND%\node_modules" (
  echo [跳过] 前端依赖已就绪
) else (
  echo [安装] 前端依赖缺失，正在执行 npm install ...
  pushd "%FRONTEND%"
  call npm install
  if errorlevel 1 (
    popd
    goto :npm_fail
  )
  popd
)

rem ---------------------- 6. 端口与数据库预检 ----------------------
rem 只匹配 LISTENING 行，且端口后紧跟空格 —— 否则会误命中 IPv6 地址里的 ":8000" 片段
netstat -ano -p TCP | findstr LISTENING | findstr /c:":8000 " >nul 2>nul
if not errorlevel 1 echo [警告] 端口 8000 已被占用，后端可能启动失败，请先关闭占用进程。
netstat -ano -p TCP | findstr LISTENING | findstr /c:":5173 " >nul 2>nul
if not errorlevel 1 echo [警告] 端口 5173 已被占用，前端可能启动失败，请先关闭占用进程。

rem .env 指向 MySQL 时先确认 3306 在监听，否则后端会在建表阶段直接失败
set "DB_KIND=sqlite"
if exist "%BACKEND%\.env" (
  findstr /i /c:"mysql" "%BACKEND%\.env" >nul 2>nul
  if not errorlevel 1 set "DB_KIND=mysql"
)
if /i "%DB_KIND%"=="mysql" (
  netstat -ano -p TCP | findstr LISTENING | findstr /c:":3306 " >nul 2>nul
  if errorlevel 1 (
    echo [警告] .env 指定 MySQL，但 3306 未在监听：请先启动 MySQL 服务，否则后端启动会失败。
  )
)

rem ---------------------- 7. 启动两个服务 ----------------------
echo.
echo [启动] 后端 http://127.0.0.1:8000/docs
start "SmartQA 后端 :8000" /D "%BACKEND%" cmd /k "%PY% -m uvicorn app.main:app --host 127.0.0.1 --port 8000"

echo [启动] 前端 http://localhost:5173
start "SmartQA 前端 :5173" /D "%FRONTEND%" cmd /k "npm run dev"

rem ---------------------- 8. 等待就绪并打开浏览器 ----------------------
echo.
echo [等待] 服务启动中，约 8 秒后打开浏览器 ...
rem 用 ping 代替 timeout：timeout 在 stdin 被重定向时会直接报错，ping 不受影响
ping -n 9 127.0.0.1 >nul
start "" http://localhost:5173

echo.
echo ============================================================
echo   已启动。
echo   后端接口文档： http://127.0.0.1:8000/docs
echo   前端管理台　： http://localhost:5173
echo   数据库　　　： %DB_KIND%
echo   演示账号　　： platform_admin / Platform@123
echo   停止服务　　： 关闭「后端」与「前端」两个窗口
echo ============================================================
echo.
pause
exit /b 0

rem ---------------------- 错误分支 ----------------------
:no_python
echo [错误] 未找到 python 命令。请安装 Python 3.10+ 并勾选 "Add Python to PATH"。
echo        下载地址：https://www.python.org/downloads/
goto :fail

:no_node
echo [错误] 未找到 node 命令。请安装 Node.js 18+ 并加入 PATH。
echo        下载地址：https://nodejs.org/
goto :fail

:no_backend
echo [错误] 未找到 "%BACKEND%\app\main.py"。
echo        请确认本文件位于仓库根目录（与 问答平台、frontend 同级）。
goto :fail

:no_frontend
echo [错误] 未找到 "%FRONTEND%\package.json"。
echo        请确认本文件位于仓库根目录（与 问答平台、frontend 同级）。
goto :fail

:pip_fail
echo [错误] 后端依赖安装失败。请检查网络后手动执行：
echo        cd "%BACKEND%" ^&^& %PY% -m pip install -r requirements.txt
goto :fail

:npm_fail
echo [错误] 前端依赖安装失败。请检查网络后手动执行：
echo        cd "%FRONTEND%" ^&^& npm install
goto :fail

:fail
echo.
echo 启动已中止。
pause
exit /b 1
