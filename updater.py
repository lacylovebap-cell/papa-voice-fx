"""一鍵更新：到 GitHub 看有沒有新版，有的話下載新的 PaPaVoiceFx.exe，
關掉自己 → 換上新檔 → 自動重新打開。設定檔放在別的地方，所以不會不見。

新版是 GitHub Actions 每次推到 main 自動打包、發佈成 Release（標籤 v<打包編號>）。"""
import json
import os
import subprocess
import sys
import tempfile
import urllib.request

REPO = "lacylovebap-cell/papa-voice-fx"
API_LATEST = "https://api.github.com/repos/%s/releases/latest" % REPO
EXE_NAME = "PaPaVoiceFx.exe"

try:
    from version import VERSION     # 打包時自動產生（打包編號）
except Exception:                    # 自己用原始碼跑的開發版
    VERSION = 0


def parse_tag(tag):
    """「v12」→ 12；看不懂就回傳 0。"""
    t = str(tag or "").strip().lstrip("vV")
    return int(t) if t.isdigit() else 0


def check(timeout=8):
    """回傳 dict(version, url, notes) 或 None（沒有新版）。連不上會丟出例外。"""
    req = urllib.request.Request(API_LATEST, headers={"Accept": "application/vnd.github+json",
                                                      "User-Agent": "PAPA-voice-fx"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        info = json.loads(r.read().decode("utf8"))
    return pick_update(info, VERSION)


def pick_update(info, current):
    v = parse_tag(info.get("tag_name"))
    url = None
    for a in info.get("assets", []):
        if a.get("name", "").lower() == EXE_NAME.lower():
            url = a.get("browser_download_url")
    if not url or v <= current:
        return None
    return {"version": v, "url": url, "notes": (info.get("body") or "").strip()}


def download(url, progress=None, timeout=30):
    """下載到暫存資料夾，回傳檔案路徑。progress(已下載 bytes, 全部 bytes)。"""
    fd, path = tempfile.mkstemp(prefix="PaPaVoiceFx_new_", suffix=".exe")
    req = urllib.request.Request(url, headers={"User-Agent": "PAPA-voice-fx"})
    with urllib.request.urlopen(req, timeout=timeout) as r, os.fdopen(fd, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        got = 0
        while True:
            chunk = r.read(1 << 16)
            if not chunk:
                break
            f.write(chunk)
            got += len(chunk)
            if progress:
                progress(got, total)
    check_exe(path)
    return path


def check_exe(path):
    """確認下載下來的是完整的 Windows 程式（不是錯誤網頁）。"""
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        head = f.read(2)
    if head != b"MZ" or size < 5_000_000:
        raise RuntimeError("下載的檔案不完整（%d bytes），請再試一次" % size)


def can_self_update():
    return bool(getattr(sys, "frozen", False)) and sys.executable.lower().endswith(".exe")


def make_script(pid, new_exe, target_exe):
    """產生換檔用的批次檔內容（用 ping 等待，背景執行也能等）：等目前的 PAPA 關掉 → 用新檔蓋掉舊檔（多試幾次）→ 重新打開。"""
    return "\r\n".join([
        "@echo off",
        "chcp 65001 >nul",
        ":wait",
        'tasklist /FI "PID eq %d" 2>nul | find "%d" >nul && (ping -n 2 127.0.0.1 >nul & goto wait)' % (pid, pid),
        "set n=0",
        ":copy",
        'copy /Y "%s" "%s" >nul && goto done' % (new_exe, target_exe),
        "set /a n+=1",
        "if %n% GEQ 20 goto fail",
        "ping -n 2 127.0.0.1 >nul",
        "goto copy",
        ":done",
        'del "%s" >nul 2>&1' % new_exe,
        'start "" "%s"' % target_exe,
        'del "%~f0" >nul 2>&1',
        "exit /b 0",
        ":fail",
        'start "" "%s"' % target_exe,
        'del "%~f0" >nul 2>&1',
        "",
    ])


def launch_swap(new_exe):
    """啟動換檔程式（在背景等 PAPA 關掉）。呼叫完要馬上關掉 PAPA。"""
    target = sys.executable
    bat = os.path.join(tempfile.gettempdir(), "papa_update_%d.bat" % os.getpid())
    with open(bat, "w", encoding="utf8", newline="") as f:
        f.write(make_script(os.getpid(), new_exe, target))
    flags = 0x08000000                  # CREATE_NO_WINDOW：背景執行，不跳黑色視窗
    subprocess.Popen(["cmd", "/c", bat], creationflags=flags, close_fds=True)
