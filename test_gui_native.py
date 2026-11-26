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
a.preset("electro")
def mon_level(m):
    a.set_listen(m)
    vals = []
    for _ in range(16):
        root.update(); time.sleep(0.05)
        vals.append(a.wave_mon.hist[-1])
    return sum(vals[6:]) / len(vals[6:])
l0, l2 = mon_level(0), mon_level(2)
check("試聽「手機外放」：低音（220 Hz 測試音）被削弱", l2 < l0 * 0.7, "%.2f → %.2f" % (l0, l2))
a.set_listen(0)
a.preset("electro")
# 錄兩段，暫存在清單
for k in range(2):
    a.toggle_rec()
    for _ in range(12):
        root.update(); time.sleep(0.05)
    a.toggle_rec()
check("錄兩段都暫存在清單", len(a.takes) == 2 and a.take_list.size() == 2, str([t["name"] for t in a.takes]))
check("每段都有錄到聲音", all(t["secs"] > 0.3 and abs(t["wet"]).max() > 0.05 for t in a.takes))
check("錄音有分開存人聲和伴奏（事後可以對齊）", all(t["voice"] is not None and t["bgm"] is not None and len(t["voice"]) == len(t["wet"]) for t in a.takes))
a.bgm_trim.set(20)
check("播放錄音時改對齊：重新混好的長度正確", len(a.take_mix(a.takes[0])) == len(a.takes[0]["wet"]))
a.bgm_trim.set(0)
a.take_list.selection_clear(0, "end"); a.take_list.selection_set(0)
a.play("wet")
for _ in range(4):
    root.update(); time.sleep(0.05)
check("可以播放第 1 段", a.eng.playing)
a.bgm_trim.set(15); a.on_trim()
for _ in range(6):
    root.update(); time.sleep(0.05)
check("播放中拉對齊：繼續播放", a.eng.playing)
a.bgm_trim.set(0)
# 多層混響＋立體寬度在真的引擎裡跑
a.rv[0]["type"].set("大廳"); a.on_reverb_type(0); a.rv[0]["mix"].set(50)
a.rv[1]["type"].set("人聲板"); a.on_reverb_type(1)
for _ in range(10):
    root.update(); time.sleep(0.05)
check("開兩層混響後仍正常運作", a.eng.running and a.wave_mon.hist[-1] > 0.05)
a.play("wet")
a.toggle()   # 停止
check("按停止後停止", not a.eng.running)
a.on_close()
print("\n全部通過" if not fails else "\n%d 項失敗" % fails)
sys.exit(1 if fails else 0)
