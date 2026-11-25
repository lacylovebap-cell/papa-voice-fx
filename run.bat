@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist venv (
  echo 第一次執行，正在建立環境並安裝套件（需要網路，約 1～3 分鐘）...
  python -m venv venv || goto :err
  call venv\Scripts\activate
  python -m pip install --upgrade pip
  pip install -r requirements.txt || goto :err
) else (
  call venv\Scripts\activate
)
python app.py
goto :eof
:err
echo.
echo 安裝失敗。請確認已安裝 Python（安裝時要勾選 Add Python to PATH），且電腦有連網。
pause
