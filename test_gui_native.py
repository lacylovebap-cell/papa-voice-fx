"""畫面＋C++ 底層引擎：真的開始處理（空裝置＋測試音），確認畫面上的波形、音高、延遲都有更新。"""
import sys
import time
import tkinter as tk
from tkinter import messagebox
import app

fails = 0


def check(name, ok, d=""):
    global fails
    fails += (not ok)
    print(("PASS  " if ok else "FAIL  ") + name + ("  " + d if d else ""))


check("畫面使用 C++ 底層引擎", app.NATIVE)
eng_mod = app.engine
eng_mod.LIB.papa_use_null_backend(1)
eng_mod.LIB.papa_set(b"test_tone_hz", 220.0)
root = tk.Tk()
root.withdraw()
a = app.App(root)
a.refresh_devices(True)
messagebox.showerror = lambda *x, **k: print("錯誤視窗：", x)
a.start_engine(0)
for _ in range(40):
    root.update()
    time.sleep(0.05)
check("按開始後引擎運作中", a.eng.running, a.msg.cget("text"))
check("麥克風、耳機有在跳", a.wave_inp.hist[-1] > 0.05 and a.wave_mon.hist[-1] > 0.05,
      "%.2f %.2f" % (a.wave_inp.hist[-1], a.wave_mon.hist[-1]))
check("狀態面板顯示唱的音", a.st_in.cget("text") not in ("—", ""), a.st_in.cget("text"))
check("顯示延遲數字", "延遲" in a.status.cget("text"), a.status.cget("text").replace("\n", " | "))
a.preset("electro")
for _ in range(10):
    root.update(); time.sleep(0.05)
check("切到電音後有修正目標音", a.eng.info[2] is not None)
a.toggle()   # 停止
check("按停止後停止", not a.eng.running)
a.on_close()
print("\n全部通過" if not fails else "\n%d 項失敗" % fails)
sys.exit(1 if fails else 0)
