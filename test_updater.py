"""一鍵更新的離線測試（不連網路）。"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import updater

fails = 0


def check(name, ok, info=""):
    global fails
    print(("PASS  " if ok else "FAIL  ") + name + ("  " + str(info) if info else ""))
    if not ok:
        fails += 1


check("版本標籤：v12 → 12", updater.parse_tag("v12") == 12 and updater.parse_tag("V3") == 3)
check("看不懂的標籤當作 0", updater.parse_tag("beta") == 0 and updater.parse_tag(None) == 0)
rel = {"tag_name": "v20", "body": "伴奏不爆", "assets": [{"name": "PaPaVoiceFx.exe", "browser_download_url": "https://x/PaPaVoiceFx.exe"}]}
u = updater.pick_update(rel, 19)
check("有比較新的版本：要更新", u is not None and u["version"] == 20 and u["url"].endswith("PaPaVoiceFx.exe") and u["notes"] == "伴奏不爆")
check("一樣的版本：不用更新", updater.pick_update(rel, 20) is None)
check("比較舊的版本：不會倒退", updater.pick_update(rel, 25) is None)
check("沒有 exe 檔：不更新", updater.pick_update({"tag_name": "v30", "assets": []}, 1) is None)
bat = updater.make_script(1234, r"C:\Temp\new.exe", r"C:\Users\A\Downloads\PaPaVoiceFx-exe\PaPaVoiceFx.exe")
check("換檔程式：等 PAPA 關掉才換", 'PID eq 1234' in bat and bat.index("PID eq 1234") < bat.index("copy /Y"))
check("換檔程式：換好自動重新打開新版", 'start "" "C:\\Users\\A\\Downloads\\PaPaVoiceFx-exe\\PaPaVoiceFx.exe"' in bat)
check("換檔程式：換不了也會把原本的打開（不會讓你沒得用）", ":fail" in bat and bat.count('start ""') == 2)
check("換檔程式：不用 timeout（背景執行時會失效）", "timeout" not in bat)
d = tempfile.mkdtemp()
bad = os.path.join(d, "bad.exe")
open(bad, "wb").write(b"<html>not found</html>")
try:
    updater.check_exe(bad)
    ok = False
except RuntimeError:
    ok = True
check("下載到錯誤網頁：不會拿去換", ok)
good = os.path.join(d, "good.exe")
open(good, "wb").write(b"MZ" + b"\0" * 6_000_000)
try:
    updater.check_exe(good)
    ok = True
except RuntimeError:
    ok = False
check("完整的程式檔：可以換", ok)
env = updater.clean_env({"_PYI_APPLICATION_HOME_DIR": "C:\\Temp\\_MEI123", "_MEIPASS2": "C:\\Temp\\_MEI123",
                          "_PYI_PARENT_PROCESS_LEVEL": "1", "PATH": "C:\\Windows", "USERNAME": "papa"})
check("新版 PAPA 拿到乾淨的環境（不會去舊暫存資料夾找 python312.dll）",
      not any(k.startswith("_PYI") or k.startswith("_MEI") for k in env) and env["PYINSTALLER_RESET_ENVIRONMENT"] == "1"
      and env["USERNAME"] == "papa" and env["PATH"] == "C:\\Windows")
check("用原始碼跑時不會自動更新", not updater.can_self_update())
print("\n全部通過" if not fails else f"\n{fails} 項失敗")
sys.exit(1 if fails else 0)
