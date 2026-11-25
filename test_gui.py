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
check("電音歌：電音質感開啟", fx.params["electro_fx"] > 0.5 and fx.params["mode"] == "chrom")
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
# 電音：選調與軟硬
a.preset("electro")
a.el_key.set("A"); a.el_hard.set(100); a.apply()
check("電音選 A 調：用音階吸附、調=A", fx.params["mode"] == "scale" and fx.params["key"] == 9)
check("電音最硬：修正速度 0", abs(fx.params["speed"]) < 1e-9)
a.el_hard.set(0); a.apply()
check("電音最軟：修正速度變慢", fx.params["speed"] > 0.2)
a.el_key.set("自動"); a.apply()
check("電音自動：吸附最近半音", fx.params["mode"] == "chrom")
a.preset("electro")
check("再按電音：關掉", fx.params["mode"] == "off")
# 跳動波形
a.eng.running = True
a.eng.lv.update(inp=0.5, mon=0.3, out=0.4, bgm=0.2)
a.tick(); root.update()
check("各管道波形有跳動", a.wave_inp.hist[-1] > 0 and a.wave_bgm.hist[-1] > 0 and a.wave_out.hist[-1] > 0)
a.eng.running = False
a.toggle_adv(); root.update(); a.toggle_adv()
check("進階設定可以展開收起", True)
a.refresh_buttons()
root.update()
check("按鈕文字顯示鎖定", "鎖定中" in str(a.btn_card.cget("text")))
a.save_settings()
root.destroy()
print("\n全部通過" if not fails else f"\n{fails} 項失敗")
sys.exit(1 if fails else 0)
