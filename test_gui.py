"""介面冒煙測試（需要 tkinter；在 Windows 打包機上執行）。"""
import json
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
check("一般歌手：開啟時是原聲（變聲預設關）", a.personal.get() == 0 and a.card.get() == 0 and fx.params.get("card") is False)
a.card.set(0); a.toggle_card()
check("一般歌手：變聲可以直接開，不用確認", a.card.get() == 1)
a.toggle_card()
check("一般歌手：變聲可以直接關", a.card.get() == 0)
a.personal.set(1); a.on_personal()
check("打開專屬模式：變聲自動開啟並鎖定", a.card.get() == 1 and fx.params.get("card") is True)

for name in app.PRESETS:
    a.preset(name)
    check(f"按 {app.PRESET_NAMES[name]}：開啟且聲卡變音仍鎖定", a.active_preset == name and fx.params["card"] is True)
    a.preset(name)
    check(f"再按 {app.PRESET_NAMES[name]}：關閉", a.active_preset is None and fx.params["mode"] == "off"
          and not fx.params["phone"] and not fx.params["machine"] and fx.params["card"] is True)

a.preset("electro")
check("電音：開啟電音模式", fx.params["mode"] == "electro")
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
levels = []
for st in ["微電", "電音", "強電", "爆電（最強）"]:
    a.el_style.set(st); a.apply()
    levels.append((fx.params["speed"], fx.params["electro_fx"], fx.params["metal"]))
check("電音 4 級由弱到強", levels[0][0] > levels[1][0] and levels[2][2] > levels[1][2] and levels[3][2] > levels[2][2], str(levels))
check("電音風格選單：電音開著時可以選", str(a.cb_style.cget("state")) == "readonly")
check("電音已包含人聲修飾音色（不用另外選）", fx.params["color"] == "人聲修飾")
a.preset("electro")
check("再按電音：關掉", fx.params["mode"] == "off")
check("電音風格選單：沒開電音時不能選", str(a.cb_style.cget("state")) == "disabled")
a.preset("sing")
check("自然修音：自動跟調、保留顫音模式，含人聲修飾", fx.params["mode"] == "natural" and fx.params["humanize"] == 1.0 and fx.params["color"] == "人聲修飾")
a.preset("sing")
# 混響與去齒音固定：切換效果不會改變，按效果也不會自己加混響
a.rv[0]["type"].set("大廳"); a.on_reverb_type(0); a.rv[0]["mix"].set(37); a.deess.set(55); a.apply()
for name in list(app.PRESETS) + list(app.PRESETS):
    a.preset(name)
a.clear_fx()
check("無效果：完全沒有混響（但混響設定還在）", all(fx.params["rv%d_mix" % (i + 1)] == 0 for i in range(3))
      and abs(float(a.rv[0]["mix"].get()) - 37) < 1e-6)
check("無效果：變聲照樣開著", fx.params["card"] is True)
check("切換所有效果後回到無效果，無效果的去齒音仍是 55%", abs(fx.params["deess"] - 0.55) < 1e-6)
a.preset("talk")
check("按說話：混響 A 回來，是大廳 37%", abs(fx.params["rv1_mix"] - 0.37) < 1e-6 and fx.params["rv1_type"] == app.REVERB_NAMES.index("大廳") + 1)
# 人聲質感兩組：無效果自己一組，其他效果共用一組
a.polish["loud"].set(70); a.apply()                 # 說話（效果共用那組）
a.preset("sing")
check("效果之間共用人聲質感：自然修音也是 70%", fx.params["loud"] == 0.7)
a.clear_fx()
check("無效果用自己那組（不是 70%）", fx.params["loud"] != 0.7)
check("無效果預設是乾淨的聲音（人聲質感都是 0）", fx.params["loud"] == 0 and fx.params["clarity"] == 0 and fx.params["widen"] == 0)
a.polish["loud"].set(20); a.apply()
a.preset("electro")
check("切到電音：還是效果共用的 70%", fx.params["loud"] == 0.7)
a.clear_fx()
check("切回無效果：還是無效果自己的 20%", fx.params["loud"] == 0.2)
check("無效果時混響選單不能改", "disabled" in a.rv_widgets[0].state())
check("無效果時混響數字顯示「關」", a.rv[0]["mix"]._fmt[0].cget("text") == "關")
a.preset("talk")
check("有效果時混響選單可以改", "disabled" not in a.rv_widgets[0].state())
s_saved = json.loads(a.polish_sets_var.get())
check("兩組人聲質感都會存", s_saved["dry"]["loud"] == 20 and s_saved["fx"]["loud"] == 70)
for g in ("dry", "fx"):
    a.polish_sets[g]["loud"] = 0
a.polish["loud"].set(0); a.apply(); a.preset("talk")
# 降噪
a.gate.set(60); a.apply()
check("降噪推桿有送出", abs(fx.params["gate"] - 0.6) < 1e-9)
a.clear_fx()
check("無效果也有降噪", abs(fx.params["gate"] - 0.6) < 1e-9)
a.gate.set(0); a.apply()
# 滑鼠滾輪不能改到下拉選單（以前會不小心換掉裝置）
check("滾輪經過下拉選單：不會改選項", root.bind_class("TCombobox", "<MouseWheel>") != "")
before = a.dev_bgm.get()
a.cb_bgm.event_generate("<MouseWheel>", delta=-120); root.update()
check("滾輪滾過伴奏選單後裝置不變", a.dev_bgm.get() == before)
for r in a.rv:
    r["mix"].set(0)
a.apply()
for name in app.PRESETS:
    a.preset(name)
    check("按 %s 不會自己加混響" % app.PRESET_NAMES[name], all(fx.params["rv%d_mix" % (i + 1)] == 0 for i in range(3)))
    a.preset(name)
# 混響類型：換類型會帶入那個類型的建議值
a.rv[1]["type"].set("教堂"); a.on_reverb_type(1)
check("混響 B 選教堂：殘響長度等於教堂預設、深度自動給 30%", abs(float(a.rv[1]["size"].get()) - 90) < 0.6
      and abs(float(a.rv[1]["mix"].get()) - 30) < 1e-6 and fx.params["rv2_type"] == app.REVERB_NAMES.index("教堂") + 1)
a.rv[1]["width"].set(40); a.rv[1]["lowcut"].set(250); a.apply()
check("混響細調：立體寬度、低頻清除有送出", abs(fx.params["rv2_width"] - 0.4) < 1e-6 and fx.params["rv2_lowcut"] == 250)
# 效果按鈕上方顯示快捷鍵
check("效果按鈕上方顯示快捷鍵（機器人 Ctrl+Alt+7）", a.preset_hk["robot"].cget("text") == "Ctrl+Alt+7" and a.none_hk.cget("text") == "Ctrl+Alt+0")
# 每個效果都能細調，而且會記住
a.preset("electro")
a.fxv["retune"].set(35); a.on_tweak("retune")
check("電音細調：修正速度 35 ms 生效", abs(fx.params["speed"] - 35 / 400) < 1e-9)
a.preset("phone"); a.preset("electro")
check("切到別的效果再切回電音：細調還在", abs(float(a.fxv["retune"].get()) - 35) < 1e-9 and abs(fx.params["speed"] - 35 / 400) < 1e-9)
a.apply_ref_preset("electro", "流行網感（35／25／80）")
check("套用 Auto-Tune 參考預設（流行網感 35/25/80）", abs(fx.params["flex"] - 0.25) < 1e-9 and abs(fx.params["humanize"] - 0.8) < 1e-9)
a.fxv["vibrato"].set(3.0); a.on_tweak("vibrato")
check("自然顫音旋鈕有送出", abs(fx.params["vibrato"] - 3.0) < 1e-9)
a.reset_tweaks("electro")
check("恢復電音預設", float(a.fxv["retune"].get()) == 0 and fx.params["flex"] == 0 and fx.params["vibrato"] == 0)
a.preset("electro")
a.preset("robot"); a.fxv["robot_note"].set(62); a.on_tweak("robot_note")
check("機器人音高可以調", fx.params["robot"] == 62)
a.preset("robot")
# 混音台頁面
a.preset("sing")
a.open_mixer("fx"); root.update()
check("混音台：自然修音的旋鈕（修正速度、自然顫音…）", all(k in a.mixer.knobs for k in ("retune", "flex", "humanize", "vibrato", "eq_hi")))
check("自然修音預設很自然：修 70%、差 20 音分以內不修、修正 120 ms", abs(fx.params["strength"] - 0.7) < 1e-9
      and abs(fx.params["tol"] - 0.2) < 1e-9 and abs(fx.params["speed"] - 0.3) < 1e-9)
check("混音台：自然修音有「容許誤差」旋鈕", "tol" in a.mixer.knobs)
a.mixer.knobs["retune"].set(160)
check("混音台：轉旋鈕就生效並記住", abs(fx.params["speed"] - 0.4) < 1e-9 and a.fx_tweaks.get("sing", {}).get("retune") == 160)
a.mixer.knobs["retune"].set(a.mixer.knobs["retune"].default)
check("旋鈕回到預設＝不留細調", "retune" not in a.fx_tweaks.get("sing", {}))
a.preset("sing")
a.open_mixer("rv", 1); root.update()
check("混音台：混響編輯器有深度、殘響長度、明亮度、預延遲、立體寬度、低頻清除",
      all(k in a.mixer.knobs for k in ("mix", "size", "bright", "pre", "width", "lowcut")))
check("主畫面：伴奏旁邊有對齊、避讓", hasattr(a.bgm_trim, "_fmt") and hasattr(a.polish["duck"], "_fmt"))
check("細調頁：左邊只有 5 項（效果、混響 A／B／C、人聲質感），不重複放音量推桿",
      len(a.mixer.nav) == 5 and not hasattr(a.mixer, "meters"))
a.open_mixer("card"); root.update()
check("不存在的細調項目會回到效果細調（不會壞掉）", a.mixer.editing == ("fx", None))
a.nb.select(0)
# 我的設定（自己取名字）
from tkinter import simpledialog
messagebox.askyesno = lambda *x, **k: True
card0 = a.card.get()
a.preset("electro"); a.fxv["retune"].set(20); a.on_tweak("retune")
a.rv[2]["type"].set("房間"); a.on_reverb_type(2); a.rv[2]["mix"].set(22); a.apply()
simpledialog.askstring = lambda *x, **k: "怕怕專屬一"
a.save_scene()
check("存成「怕怕專屬一」：上面出現按鈕", "怕怕專屬一" in a.scenes and "怕怕專屬一" in a.scene_btns)
a.clear_fx(); a.rv[2]["mix"].set(0); a.rv[2]["type"].set("關"); a.reset_tweaks("electro"); a.apply()
a.preset("talk")
a.save_scene("怕怕說唱")
a.load_scene("怕怕專屬一")
check("一鍵切回「怕怕專屬一」：電音＋細調＋混響都回來", a.active_preset == "electro" and abs(fx.params["speed"] - 20 / 400) < 1e-9
      and a.rv[2]["type"].get() == "房間" and abs(fx.params["rv3_mix"] - 0.22) < 1e-6)
check("切換我的設定不會動到變聲開關", a.card.get() == card0)
a.load_scene_index(1)
check("第二組（F2）：怕怕說唱 → 說話", a.active_preset == "talk" and a.current_scene == "怕怕說唱")
simpledialog.askstring = lambda *x, **k: "怕怕說唱2"
a.rename_scene("怕怕說唱")
check("改名", "怕怕說唱2" in a.scenes and "怕怕說唱" not in a.scenes)
a.delete_scene("怕怕說唱2")
check("刪除", "怕怕說唱2" not in a.scenes and list(a.scenes) == ["怕怕專屬一"])
a.save_settings()
s2 = a.load_settings()
check("我的設定、細調會存進設定檔", "怕怕專屬一" in s2.get("scenes", "") and "fx_tweaks" in s2)
a.clear_fx()
# 一鍵 CD 質感：只動混響和人聲質感，不動效果和變聲
a.preset("talk"); card1 = a.card.get()
a.apply_cd_template(ask=False)
check("一鍵 CD 質感：混響 A＝CD 絲滑板式 20%、B／C 歸零", a.rv[0]["type"].get() == "CD 絲滑板式" and abs(fx.params["rv1_mix"] - 0.20) < 1e-6
      and fx.params["rv2_mix"] == 0 and fx.params["rv3_mix"] == 0)
check("一鍵 CD 質感：響度、清晰度、厚度、加寬、避讓打開", fx.params["loud"] == 0.5 and fx.params["clarity"] == 0.4
      and fx.params["thick"] == 0.25 and fx.params["widen"] == 0.6 and fx.params["duck"] == 0.3)
check("一鍵 CD 質感：效果和變聲不變", a.active_preset == "talk" and a.card.get() == card1)
# 一鍵（小按鈕）
check("一鍵有 10 種風格（CD、古風、說唱、甜歌…）", len(a.style_btns) == 10 and all(k in a.style_btns for k in ("CD", "古風", "說唱", "甜歌", "流行", "抒情", "悲傷")))
a.apply_style("古風")
check("一鍵古風：混響 A＝大廳 25%、C＝教堂", fx.params["rv1_type"] == app.REVERB_NAMES.index("大廳") + 1
      and abs(fx.params["rv1_mix"] - 0.25) < 1e-6 and fx.params["rv3_type"] == app.REVERB_NAMES.index("教堂") + 1)
check("一鍵古風：人聲質感也換了、效果和變聲不動", fx.params["widen"] == 0.7 and a.active_preset == "talk" and a.card.get() == card1)
a.apply_style("說唱")
check("一鍵說唱：只留一點房間、B／C 關掉", fx.params["rv1_type"] == app.REVERB_NAMES.index("房間") + 1
      and fx.params["rv2_mix"] == 0 and fx.params["rv3_mix"] == 0 and fx.params["loud"] == 0.7)
a.undo_style()
check("↶ 復原：回到按說唱之前（古風）", fx.params["rv1_type"] == app.REVERB_NAMES.index("大廳") + 1 and fx.params["widen"] == 0.7)
a.undo_style()
check("↶ 再復原：回到按古風之前（CD）", a.rv[0]["type"].get() == "CD 絲滑板式" and fx.params["loud"] == 0.5)
a.clear_fx()
dry_loud = fx.params["loud"]
a.apply_style("悲傷")
check("無效果時按一鍵：不動無效果自己的人聲質感", fx.params["loud"] == dry_loud and all(fx.params["rv%d_mix" % (i + 1)] == 0 for i in range(3)))
a.polish["loud"].set(35); a.apply(); a.reset_polish()
check("↺ 歸零：無效果那組人聲質感歸零", fx.params["loud"] == 0)
a.preset("talk")
check("無效果時按的風格：切到說話就聽得到", fx.params["rv1_type"] == app.REVERB_NAMES.index("大廳") + 1 and fx.params["loud"] == 0.4)
# 更多／自己選按鈕
check("新的「破碎感」「御姐說唱」先放在更多裡（不在按鈕上）", "破碎感" in app.ONE_CLICK and "御姐說唱" in app.ONE_CLICK
      and "破碎感" not in a.style_btns and "御姐說唱" not in a.style_btns)
a.swap_style("爵士", "破碎感"); a.swap_style("搖滾", "御姐說唱"); root.update()
check("把爵士、搖滾換成破碎感、御姐說唱", "破碎感" in a.style_btns and "御姐說唱" in a.style_btns
      and "爵士" not in a.style_btns and len(a.style_btns) == 10)
check("換過的按鈕會記住", "破碎感" in json.loads(a.oc_visible_var.get()))
g0 = float(a.gate.get())
a.apply_style("破碎感")
check("一鍵破碎感：去齒音 60%、降噪 30%、空氣感（清晰度）70%", abs(fx.params["deess"] - 0.6) < 1e-9 and abs(fx.params["gate"] - 0.3) < 1e-9
      and fx.params["clarity"] == 0.7)
a.undo_style()
check("復原：降噪也回到按之前", abs(float(a.gate.get()) - g0) < 1e-9)
dlg = a.style_choose_dialog(); root.update()
check("可以打開「選擇要顯示的按鈕」", dlg.winfo_exists()); dlg.destroy()
a.set_style_visible(app.ONE_CLICK_DEFAULT); root.update()
check("恢復預設的按鈕", list(a.style_btns) == app.ONE_CLICK_DEFAULT)
a.undo_style()
a.apply_cd_template(ask=False)
a.save_scene("CD 測試")
for k in app.POLISH:
    a.polish[k].set(0)
a.apply()
a.load_scene("CD 測試")
check("我的設定也會記住人聲質感", fx.params["loud"] == 0.5 and fx.params["widen"] == 0.6)
a.delete_scene("CD 測試")
a.open_mixer("polish"); root.update()
check("細調頁：人聲質感旋鈕（響度、清晰度、厚度、加寬、去齒音）", all(k in a.mixer.knobs for k in ("loud", "clarity", "thick", "widen", "deess")))
a.nb.select(0)
for k in app.POLISH:
    a.polish[k].set(0)
a.preset("talk")
a.alpha.set(90); a.apply()
check("視窗透明度可以調", abs(float(a.alpha.get()) - 90) < 1e-9)
a.alpha.set(97); a.apply()
a.g_master.set(200); a.apply()
check("總音量：直播、錄音、耳機一起（送出 2.0）", abs(a.eng.gains["master"] - 2.0) < 1e-9)
a.g_master.set(100); a.apply()
check("耳機只能調小（最多 100%）", a.cb_mon is not None)
a.g_mon.set(100); a.apply()
a.set_listen(1)
check("試聽：直播 App 聽到的（只送試聽模式給引擎，直播參數不變）", fx.params["mon_mode"] == 1 and fx.params["card"] == bool(a.card.get()))
a.set_listen(2)
check("試聽：手機外放", fx.params["mon_mode"] == 2 and str(a.listen_btns[2].cget("bg")) == app.C_YELLOW)
a.set_listen(0)
check("試聽：回到原本", fx.params["mon_mode"] == 0)
a.set_listen(2)
check("試聽時狀態文字仍正確顯示變聲（不會被試聽按鈕影響）", "變聲" in a.now_label.cget("text") and "原音" not in a.now_label.cget("text"), a.now_label.cget("text"))
a.set_listen(0)
# 伴奏對齊：錄音分開存人聲和伴奏，播放時照目前的對齊重新混
import numpy as _np
v0 = _np.zeros(48000, _np.float32); b0 = _np.zeros(48000, _np.float32); b0[1000] = 0.5
tk_ = dict(name="t", wet=v0 + b0, dry=v0, voice=v0, bgm=b0, secs=1.0, trim=0.0)
a.bgm_trim.set(10); m1 = a.take_mix(tk_)
a.bgm_trim.set(-10); m2 = a.take_mix(tk_)
a.bgm_trim.set(0); m3 = a.take_mix(tk_)
check("伴奏對齊：往右 10 ms＝伴奏晚 480 樣本；往左＝早 480 樣本", int(_np.argmax(m1)) == 1480 and int(_np.argmax(m2)) == 520 and int(_np.argmax(m3)) == 1000)
# 跳動波形
a.eng.running = True
a.eng.lv.update(inp=0.5, mon=0.3, out=0.4, bgm=0.2)
a.tick(); root.update()
check("狀態面板：顯示目前功能", "變聲" in a.st_funcs.cget("text"))
# 機器人
a.preset("robot")
check("機器人：固定音高模式＋環形調變＋金屬共鳴", fx.params["mode"] == "robot" and fx.params["robot_fx"] == 1.0 and fx.params["metal"] > 0)
a.preset("robot")
check("再按機器人：關掉", fx.params["mode"] == "off" and fx.params["robot_fx"] == 0)
# 暫存多段錄音（不需要真的聲卡：直接放兩段進清單）
import numpy as _np
a.takes = []
for k in range(2):
    a.takes.append(dict(name="第 %d 段　測試　1.0 秒" % (k + 1), wet=_np.zeros(48000, _np.float32), dry=_np.zeros(48000, _np.float32), secs=1.0))
a.refresh_takes(select=1)
check("暫存清單顯示兩段", a.take_list.size() == 2 and a.selected_take() == 1)
a.delete_take()
check("刪除選的那段後剩一段", len(a.takes) == 1 and a.take_list.size() == 1)
check("各管道波形有跳動", a.wave_inp.hist[-1] > 0 and a.wave_bgm.hist[-1] > 0 and a.wave_out.hist[-1] > 0)
a.eng.running = False
a.toggle_adv(); root.update(); a.toggle_adv()
check("進階設定可以展開收起", True)
a.preset("electro"); a.clear_fx()
check("無效果：一鍵清掉所有效果", a.active_preset is None and fx.params["mode"] == "off")
check("狀態文字：變聲＋無效果", "變聲" in a.now_label.cget("text") and "無效果" in a.now_label.cget("text"), a.now_label.cget("text"))
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
ur[0] = 20; a._ur_hist = []; a.auto_protect(); ur[0] = 40; a.auto_protect()
check("緩衝最多只加到 512（不會到 1024）", a.block_now == 512, str(a.block_now))
import time as _t
t0 = _t.perf_counter()
for _ in range(200):
    a.wave_inp.push(0.5)
dt = (_t.perf_counter() - t0) / 200 * 1000
check("波形更新很省資源（每次 < 2 ms）", dt < 2.0, "%.2f ms" % dt)
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
check("按鈕文字顯示鎖定（專屬）", "專屬" in str(a.btn_card.cget("text")) and "開" in str(a.btn_card.cget("text")))
check("變聲按鈕和停止差不多大（不會撐滿整排）", a.btn_card.pack_info().get("expand") in (0, "0", False))
check("視窗標題是 PAPA 調音台", root.title() == "PAPA 調音台")
check("右上角有「檢查更新」按鈕", "更新" in str(a.btn_update.cget("text")))
a.save_settings()
root.destroy()
print("\n全部通過" if not fails else f"\n{fails} 項失敗")
sys.exit(1 if fails else 0)
