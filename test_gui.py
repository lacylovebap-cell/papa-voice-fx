"""介面冒煙測試（需要 tkinter；在 Windows 打包機上執行）。"""
import sys
import tkinter as tk
from tkinter import messagebox
import app

fails = 0


def check(name, ok, detail=""):
    global fails
    if not ok:
        fails += 1
    print(("PASS  " if ok else "FAIL  ") + name + ("  " + detail if detail else ""))


root = tk.Tk()
root.withdraw()
a = app.App(root)
root.update()
fx = a.eng
check("開啟時聲卡變音預設鎖定", a.card.get() == 1 and fx.params.get("card") is True)

for name in app.PRESETS:
    a.preset(name)
    check(f"按 {app.PRESET_NAMES[name]}：開啟且聲卡變音仍鎖定", a.active_preset == name and fx.params["card"] is True)
    a.preset(name)
    check(f"再按 {app.PRESET_NAMES[name]}：關閉", a.active_preset is None and fx.params["mode"] == "off"
          and not fx.params["phone"] and not fx.params["machine"] and fx.params["card"] is True)

a.preset("electro")
check("電音：電音質感開啟", fx.params["electro_fx"] > 0.1 and fx.params["mode"] == "electro")
a.preset("phone")
check("換成電話音：電音質感關掉", fx.params["electro_fx"] == 0)
check("切換到另一個效果：前一個被取代", a.active_preset == "phone" and fx.params["mode"] == "off" and fx.params["phone"])
a.preset("phone")

a.set_mode("chrom")
check("電音模式按下：開啟", fx.params["mode"] == "chrom")
a.set_mode("chrom")
check("電音模式再按：關閉", fx.params["mode"] == "off")

messagebox.askyesno = lambda *x, **k: False
a.toggle_card()
check("關閉聲卡變音時按「否」：維持鎖定", a.card.get() == 1)
messagebox.askyesno = lambda *x, **k: True
a.toggle_card()
check("確認後才關閉聲卡變音", a.card.get() == 0 and fx.params["card"] is False)
a.toggle_card()
check("再按一次：重新鎖定", a.card.get() == 1 and fx.params["card"] is True)

b0 = a.eng.bgm_on
a.toggle_bgm()
check("伴奏開關：再按一次切換", a.eng.bgm_on != b0)
a.toggle_bgm()
check("伴奏開關：恢復", a.eng.bgm_on == b0)
check("伴奏來源清單可讀取", isinstance(list(a.cb_bgm["values"]), list))
# 電音與自然修音：自動判斷調，不用選調
a.preset("electro")
check("電音：自動跟調模式", fx.params["mode"] == "electro")
a.el_style.set("說唱（最電）"); a.apply()
s_hard = fx.params["speed"]
a.el_style.set("流行網感（微微電音）"); a.apply()
check("電音風格：說唱比流行網感更硬", s_hard < fx.params["speed"] and fx.params["humanize"] > 0.5)
check("電音已包含人聲修飾音色（不用另外選）", fx.params["color"] == "人聲修飾")
a.preset("electro")
check("再按電音：關掉", fx.params["mode"] == "off")
a.preset("sing")
check("自然修音：自動跟調、保留顫音模式，含人聲修飾", fx.params["mode"] == "natural" and fx.params["humanize"] == 1.0 and fx.params["color"] == "人聲修飾")
a.preset("sing")
# 混響與去齒音固定：切換效果不會改變
a.verb.set(37); a.deess.set(55); a.apply()
for name in list(app.PRESETS) + list(app.PRESETS):
    a.preset(name)
a.clear_fx()
check("切換所有效果後，混響仍是 37%", abs(fx.params["verb"] - 0.37) < 1e-6 and abs(float(a.verb.get()) - 37) < 1e-6)
check("切換所有效果後，去齒音仍是 55%", abs(fx.params["deess"] - 0.55) < 1e-6)
# 跳動波形
a.eng.running = True
a.eng.lv.update(inp=0.5, mon=0.3, out=0.4, bgm=0.2)
a.tick(); root.update()
check("狀態面板：顯示目前功能", "聲卡變音" in a.st_funcs.cget("text"))
check("各管道波形有跳動", a.wave_inp.hist[-1] > 0 and a.wave_bgm.hist[-1] > 0 and a.wave_out.hist[-1] > 0)
a.eng.running = False
a.toggle_adv(); root.update(); a.toggle_adv()
check("進階設定可以展開收起", True)
a.preset("electro"); a.clear_fx()
check("無效果：一鍵清掉所有效果", a.active_preset is None and fx.params["mode"] == "off")
check("狀態文字：聲卡變音＋無效果", "聲卡變音" in a.now_label.cget("text") and "無效果" in a.now_label.cget("text"))
check("超低延遲預設關閉", a.ultra.get() == 0)
# 自動防爆音：緩衝不足一直增加 → 區塊加大
import types
a.eng.running = True
starts = []
a.start_engine = lambda u: starts.append(a.block_now)
a.eng.stop = lambda: None
ur = [0]
type(a.eng).underruns = property(lambda self: ur[0])
a._ur_hist = []; a.block_now = 256; a._started_at = 0
a.auto_protect(); ur[0] = 6; a.auto_protect()
check("爆音時自動加大緩衝", a.block_now == 512 and starts == [512], str(starts))
a.eng.running = False
# 單一執行個體：第二個不會開起來
srv1 = app.already_running()
srv2 = app.already_running()
check("只能開一個調音台", (srv1 is None) or (srv2 is None))
for sv in (srv1, srv2):
    try:
        sv and sv.close()
    except Exception:
        pass
a.refresh_buttons()
root.update()
check("按鈕文字顯示鎖定", "開著" in str(a.btn_card.cget("text")))
a.save_settings()
root.destroy()
print("\n全部通過" if not fails else f"\n{fails} 項失敗")
sys.exit(1 if fails else 0)
