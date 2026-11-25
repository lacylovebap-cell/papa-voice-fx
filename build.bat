@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 正在建立環境並打包成 exe（需要網路，約 3～8 分鐘）...
if not exist venv (
  python -m venv venv || goto :err
)
call venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt pyinstaller || goto :err
pyinstaller --noconfirm --clean --onefile --windowed --name PaPaVoiceFx ^
  --collect-all sounddevice ^
  --hidden-import pynput.keyboard._win32 --hidden-import pynput.mouse._win32 ^
  --exclude-module matplotlib --exclude-module pandas --exclude-module PIL --exclude-module IPython ^
  app.py || goto :err
echo.
echo 完成！程式在：%~dp0dist\PaPaVoiceFx.exe
echo 可以把它複製到桌面使用。
pause
goto :eof
:err
echo.
echo 打包失敗，請把上面的錯誤訊息截圖給我。
pause
