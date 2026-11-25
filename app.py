"""怕怕調音台 - 桌面版（Windows）簡潔版
執行：python app.py    打包：build.bat
"""
import collections
import datetime
import json
import os
import traceback
import tkinter as tk
from tkinter import messagebox, ttk

import engine
from dsp import note_name, KEY_NAMES, VOICE_COLORS

APP = "PaPaVoiceFx"
NONE = "（不使用）"

# 效果：再按一次同一顆＝關閉。聲卡變音是獨立的鎖，不受效果影響。
NEUTRAL = dict(electro_fx=0, mode="off", phone=0, machine=0, strength=100, speed=0, shift=0, comp=40, eq_lo=0, eq_mid=0, eq_hi=0)
PRESET_NAMES = {"talk": "🗣 說話", "sing": "🎤 自然修音", "electro": "⚡ 電音", "phone": "📞 電話", "machine": "📼 答錄機"}
PRESETS = {
    "talk": dict(electro_fx=0, mode="off", phone=0, machine=0, strength=100, speed=0, shift=0, comp=45, eq_lo=0, eq_mid=2, eq_hi=2),
    "sing": dict(electro_fx=0, mode="natural", phone=0, machine=0, strength=85, speed=10, shift=0, comp=40, eq_lo=1, eq_mid=1, eq_hi=2),
    "electro": dict(electro_fx=60, mode="electro", phone=0, machine=0, strength=100, speed=0, shift=0, comp=50, eq_lo=0, eq_mid=1, eq_hi=2),
    "phone": dict(electro_fx=0, mode="off", phone=1, machine=0, strength=100, speed=0, shift=0, comp=60, eq_lo=0, eq_mid=0, eq_hi=0),
    "machine": dict(electro_fx=0, mode="off", phone=0, machine=1, strength=100, speed=0, shift=0, comp=60, eq_lo=0, eq_mid=0, eq_hi=0),
}

BGM_HELP = """伴奏怎麼接（只要設定一次）：

① 在 Windows 搜尋「音量混音程式」並打開
   （或：設定 → 系統 → 音效 → 音量混音程式）

② 找到「Google Chrome」，把它的「輸出裝置」
   改成「Realtek Digital Output」
   （這個裝置沒接喇叭，所以伴奏不會從喇叭放出來）

③ 回到調音台，「伴奏」那一行選「Realtek Digital Output」，
   按「開始」，在 Chrome 播伴奏，伴奏那一行就會跳動

之後只要開調音台、在 Chrome 播歌就好，不用再設定。
伴奏不會被變音，會跟你的人聲一起送到耳機和直播。"""

C_BG, C_CARD, C_TXT, C_SUB = "#16131f", "#221e30", "#ece9f7", "#9a94b8"
C_PINK, C_GREEN, C_YELLOW, C_CYAN = "#ff7eb6", "#7dffb0", "#ffd84d", "#6fe3ff"
C_BTN = "#2e2944"
FONT = "Microsoft JhengHei"


def data_dir():
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    d = os.path.join(base, APP)
    os.makedirs(d, exist_ok=True)
    return d


def rec_dir():
    d = os.path.join(os.path.expanduser("~"), "Documents", APP, "recordings")
    os.makedirs(d, exist_ok=True)
    return d


def log_error(text):
    try:
        with open(os.path.join(data_dir(), "error.log"), "a", encoding="utf8") as f:
            f.write("[%s]\n%s\n" % (datetime.datetime.now().isoformat(timespec="seconds"), text))
    except Exception:
        pass


# 電音風格（參數來自 Auto-Tune 常用設定：修正速度 ms、Flex-Tune、Humanize、電音質感）
EL_STYLES = {
    "流行網感（微微電音）": (30, 25, 60, 30),
    "抒情": (20, 20, 20, 20),
    "現代流行": (40, 0, 20, 40),
    "說唱（最電）": (10, 5, 10, 60),
}
# 自然修音（Auto-Tune「自然流行」：修正速度 60、Flex 20、Humanize 100）
NATURAL = (60, 20, 100)


class Wave(tk.Canvas):
    """會跳動的聲音波形：最近約 3 秒的音量，一根一根往左捲。"""

    def __init__(self, parent, color, w=210, h=34):
        super().__init__(parent, width=w, height=h, bg="#0e0c16", highlightthickness=0)
        self.w, self.h, self.color = w, h, color
        n = w // 3
        self.hist = collections.deque([0.0] * n, maxlen=n)
        self.lines = [self.create_line(i * 3 + 1, h / 2 - 1, i * 3 + 1, h / 2 + 1, fill="#3a3550", width=2) for i in range(n)]

    def push(self, level):
        self.hist.append(min(1.0, max(0.0, level)))
        mid = self.h / 2
        for line, v, i in zip(self.lines, self.hist, range(len(self.lines))):
            a = max(1.0, v ** 0.5 * (mid - 2))   # 開根號讓小聲也看得見
            x = i * 3 + 1
            self.coords(line, x, mid - a, x, mid + a)
            self.itemconfig(line, fill=self.color if v > 0.003 else "#3a3550")


class App:
    def __init__(self, root):
        self.root = root
        root.title("怕怕調音台")
        root.configure(bg=C_BG)
        self.eng = engine.Engine()
        self.settings = self.load_settings()
        self.vars = {}
        self.in_map, self.out_map = {}, {}
        self._save_job = None
        self._play_which = None
        self.active_preset = None
        self._init_vars()
        self._build()
        self.refresh_devices()
        self.apply()
        self.setup_hotkeys()
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.tick()

    # ------------------------------------------------------------ 設定
    def load_settings(self):
        try:
            with open(os.path.join(data_dir(), "settings.json"), encoding="utf8") as f:
                return json.load(f)
        except Exception:
            return {}

    def save_settings(self):
        self._save_job = None
        s = {k: v.get() for k, v in self.vars.items()}
        try:
            with open(os.path.join(data_dir(), "settings.json"), "w", encoding="utf8") as f:
                json.dump(s, f, ensure_ascii=False, indent=1)
        except Exception:
            pass

    def schedule_save(self):
        if self._save_job is None:
            self._save_job = self.root.after(800, self.save_settings)

    def var(self, name, default, kind=tk.StringVar, remember=True):
        val = self.settings.get(name, default) if remember else default
        try:
            v = kind(value=val)
            v.get()
        except Exception:
            v = kind(value=default)
        self.vars[name] = v
        return v

    def _init_vars(self):
        D = tk.DoubleVar
        self.dev_in, self.dev_mon = self.var("dev_in", ""), self.var("dev_mon", "")
        self.dev_out, self.dev_bgm = self.var("dev_out", ""), self.var("dev_bgm", NONE)
        self.g_in, self.g_mon = self.var("g_in", 100, D), self.var("g_mon", 100, D)
        self.g_out, self.g_bgm = self.var("g_out", 100, D), self.var("g_bgm", 80, D)
        self.bgm_on = self.var("bgm_on", 1, tk.IntVar)
        self.card = self.var("card", 1, tk.IntVar, remember=False)  # 每次開啟一律鎖定
        self.ultra = self.var("ultra_v2", 0, tk.IntVar)
        self.block_now = 256
        self._ur_hist = []
        self.bgm_trim = self.var("bgm_trim", 0, D)
        self.verb = self.var("verb", 15, D)
        self.deess = self.var("deess", 40, D)
        self.g_dry = self.var("g_dry", 0, D)
        self.el_style = self.var("el_style", "流行網感（微微電音）")
        if self.el_style.get() not in EL_STYLES:
            self.el_style.set("流行網感（微微電音）")
        self.color = tk.StringVar(value="原本")   # 音色跟著效果走（不另外選）
        # 效果內部參數（不顯示在畫面上，由效果按鈕設定）
        for k, v in NEUTRAL.items():
            setattr(self, k, self.var(k, v, tk.StringVar if k == "mode" else D, remember=False))

    # ------------------------------------------------------------ 介面
    def label(self, parent, text, size=10, color=C_TXT, bold=False, **kw):
        return tk.Label(parent, text=text, bg=parent["bg"], fg=color, font=(FONT, size, "bold" if bold else "normal"), **kw)

    def button(self, parent, text, cmd, color=C_BTN, fg=C_TXT, size=11, **kw):
        return tk.Button(parent, text=text, command=cmd, bg=color, fg=fg, activebackground=color, activeforeground=fg,
                         relief="flat", font=(FONT, size, "bold"), cursor="hand2", bd=0, padx=10, pady=6, **kw)

    def _build(self):
        st = ttk.Style()
        try:
            st.theme_use("clam")
        except Exception:
            pass
        wrap = tk.Frame(self.root, bg=C_BG, padx=14, pady=12)
        wrap.pack(fill="both", expand=True)
        self.wrap = wrap

        # ---- 頂部：開始＋聲卡變音
        top = tk.Frame(wrap, bg=C_BG)
        top.pack(fill="x")
        self.btn_start = self.button(top, "▶ 開始", self.toggle, color=C_PINK, fg="#2a0a1a", size=16, width=8)
        self.btn_start.pack(side="left")
        self.btn_card = self.button(top, "", self.toggle_card, size=13)
        self.btn_card.pack(side="left", padx=10, fill="x", expand=True)
        self.msg = self.label(wrap, "戴好耳機，按「開始」。", 10, C_SUB, anchor="w")
        self.msg.pack(fill="x", pady=(6, 6))

        # ---- 狀態面板：一眼看懂現在的聲音
        sp = tk.Frame(wrap, bg="#0e0c16", padx=14, pady=10, highlightthickness=1, highlightbackground="#3a3550")
        sp.pack(fill="x", pady=(0, 8))
        r1 = tk.Frame(sp, bg="#0e0c16"); r1.pack(fill="x")
        self.label(r1, "你唱的音", 10, C_SUB).grid(row=0, column=0, sticky="w")
        self.label(r1, "", 10, C_SUB).grid(row=0, column=1, padx=18)
        self.label(r1, "送出去的音", 10, C_SUB).grid(row=0, column=2, sticky="w")
        self.st_in = self.label(r1, "—", 26, C_CYAN, True, width=5, anchor="w")
        self.st_in.grid(row=1, column=0, sticky="w")
        self.label(r1, "→", 22, C_SUB).grid(row=1, column=1, padx=18)
        self.st_out = self.label(r1, "—", 26, C_PINK, True, width=5, anchor="w")
        self.st_out.grid(row=1, column=2, sticky="w")
        self.st_diff = self.label(r1, "", 11, C_SUB, justify="left", anchor="w")
        self.st_diff.grid(row=1, column=3, sticky="w", padx=(18, 0))
        self.st_funcs = self.label(sp, "", 12, C_TXT, True, anchor="w", justify="left")
        self.st_funcs.pack(fill="x", pady=(8, 0))

        # ---- 聲音管道：名稱／裝置／音量／跳動波形
        box = tk.Frame(wrap, bg=C_CARD, padx=12, pady=10)
        box.pack(fill="x")
        self.label(box, "聲音管道（有聲音時，右邊會跳動）", 11, C_PINK, True).grid(row=0, column=0, columnspan=5, sticky="w", pady=(0, 6))
        self.cb_in = self._row(box, 1, "🎙 麥克風", self.dev_in, self.g_in, 200, C_CYAN, "inp")
        self.cb_mon = self._row(box, 2, "🎧 耳機", self.dev_mon, self.g_mon, 150, C_GREEN, "mon")
        self.cb_out = self._row(box, 3, "📡 直播", self.dev_out, self.g_out, 150, C_PINK, "out")
        self.cb_bgm = self._row(box, 4, "🎵 伴奏", self.dev_bgm, self.g_bgm, 150, C_YELLOW, "bgm")
        bgmrow = tk.Frame(box, bg=C_CARD)
        bgmrow.grid(row=5, column=1, columnspan=4, sticky="w", pady=(6, 0))
        self.btn_bgm = self.button(bgmrow, "", self.toggle_bgm, size=10)
        self.btn_bgm.pack(side="left")
        self.button(bgmrow, "❓ 伴奏怎麼接", lambda: messagebox.showinfo("伴奏怎麼接", BGM_HELP), size=10).pack(side="left", padx=8)

        # ---- 效果
        fx = tk.Frame(wrap, bg=C_CARD, padx=12, pady=10)
        fx.pack(fill="x", pady=10)
        self.label(fx, "效果（再按一次＝關掉）", 11, C_PINK, True).pack(anchor="w", pady=(0, 2))
        self.now_label = self.label(fx, "", 1, C_CARD)   # 保留給測試用（不顯示內容）
        row = tk.Frame(fx, bg=C_CARD)
        row.pack(fill="x")
        self.preset_btns = {}
        self.btn_none = self.button(row, "✋ 無效果", self.clear_fx, size=12)
        self.btn_none.pack(side="left", expand=True, fill="x", padx=3)
        for key, txt in PRESET_NAMES.items():
            b = self.button(row, txt, lambda k=key: self.preset(k), size=12)
            b.pack(side="left", expand=True, fill="x", padx=3)
            self.preset_btns[key] = b
        # 混響、去齒音：固定一個值，切換任何效果都不會跑掉
        fixed = tk.Frame(fx, bg=C_CARD)
        fixed.pack(fill="x", pady=(10, 0))
        for col, (txt, v) in enumerate((("🌫 混響", self.verb), ("🦷 去齒音", self.deess))):
            self.label(fixed, txt, 11, C_TXT, True).grid(row=0, column=col * 3, sticky="w", padx=(0 if col == 0 else 24, 6))
            ttk.Scale(fixed, from_=0, to=100, variable=v, orient="horizontal", length=170,
                      command=lambda _v: self.apply()).grid(row=0, column=col * 3 + 1)
            vl = self.label(fixed, "", 10, C_SUB, width=5)
            vl.grid(row=0, column=col * 3 + 2)
            v._fmt = (vl, "{:.0f}%")
        self.label(fx, "↑ 混響和去齒音是固定的，切換任何效果都會沿用。", 9, C_SUB, anchor="w").pack(fill="x")
        el = tk.Frame(fx, bg=C_CARD)
        el.pack(fill="x", pady=(8, 0))
        self.label(el, "電音風格：", 10).pack(side="left")
        c1 = ttk.Combobox(el, textvariable=self.el_style, values=list(EL_STYLES), state="readonly", width=18)
        c1.pack(side="left")
        c1.bind("<<ComboboxSelected>>", lambda e: self.apply())

        self.label(fx, "修音和電音都會跟著你唱的歌自動判斷調，不用選調。\n自然修音＝只把走音拉回來，保留顫音和滑音；電音＝電音人聲（風格由軟到硬可選）。\n兩個都已經包含人聲修飾的音色，不用另外調。",
                   9, C_SUB, justify="left", anchor="w").pack(fill="x", pady=(4, 0))
        self.note_label = self.label(fx, "", 1, C_CARD)

        # ---- 錄音
        rc = tk.Frame(wrap, bg=C_CARD, padx=12, pady=10)
        rc.pack(fill="x")
        self.label(rc, "錄音（錄下直播出去的聲音，自己聽聽看）", 11, C_PINK, True).pack(anchor="w", pady=(0, 6))
        row = tk.Frame(rc, bg=C_CARD)
        row.pack(fill="x")
        self.btn_rec = self.button(row, "● 錄音", self.toggle_rec, size=12)
        self.btn_rec.pack(side="left", expand=True, fill="x", padx=3)
        self.btn_play_wet = self.button(row, "▶ 聽錄音", lambda: self.play("wet"), size=12)
        self.btn_play_wet.pack(side="left", expand=True, fill="x", padx=3)
        self.button(row, "💾 存檔", self.save_rec, size=12).pack(side="left", expand=True, fill="x", padx=3)
        self.rec_label = self.label(rc, "還沒錄音", 10, C_SUB, anchor="w")
        self.rec_label.pack(fill="x", pady=(6, 0))

        # ---- 進階（預設收起來）
        self.adv_open = False
        self.btn_adv = self.button(wrap, "⚙ 進階設定 ▸", self.toggle_adv, color=C_BG, fg=C_SUB, size=10)
        self.btn_adv.pack(anchor="w", pady=(10, 0))
        self.adv = tk.Frame(wrap, bg=C_CARD, padx=12, pady=10)
        tk.Checkbutton(self.adv, text="超低延遲（有爆音就取消勾選）", variable=self.ultra, bg=C_CARD, fg=C_TXT,
                       selectcolor="#13111c", activebackground=C_CARD, font=(FONT, 10)).grid(row=0, column=0, columnspan=3, sticky="w")
        self._slider(self.adv, 1, "直播對齊微調（人聲搶拍往右、拖拍往左）", self.bgm_trim, -80, 80, "{:+.0f} ms")
        self._slider(self.adv, 3, "耳機混一點原聲（抓拍用）", self.g_dry, 0, 100, "{:.0f}%")
        self.button(self.adv, "重新尋找裝置", lambda: self.refresh_devices(True), size=10).grid(row=4, column=0, sticky="w", pady=(8, 0))
        self.status = self.label(self.adv, "", 9, C_SUB, justify="left", anchor="w")
        self.status.grid(row=5, column=0, columnspan=3, sticky="w", pady=(6, 0))
        self.hint = self.label(wrap, "快捷鍵 Ctrl+Alt+：1 說話　2 唱歌　3 電音　4 電話　5 答錄機　6 聲卡變音　B 伴奏　R 錄音",
                               9, C_SUB, anchor="w")
        self.hint.pack(fill="x", pady=(8, 0))

    def _row(self, box, r, name, dev_var, gain_var, gmax, color, key):
        self.label(box, name, 11, C_TXT, True, width=8, anchor="w").grid(row=r, column=0, sticky="w", pady=3)
        cb = ttk.Combobox(box, textvariable=dev_var, state="readonly", width=30)
        cb.grid(row=r, column=1, sticky="we", padx=6)
        cb.bind("<<ComboboxSelected>>", lambda e: self.on_device_change())
        ttk.Scale(box, from_=0, to=gmax, variable=gain_var, orient="horizontal", length=110,
                  command=lambda v: self.apply()).grid(row=r, column=2, padx=4)
        val = self.label(box, "", 9, C_SUB, width=5)
        val.grid(row=r, column=3)
        gain_var._fmt = (val, "{:.0f}%")
        wv = Wave(box, color)
        wv.grid(row=r, column=4, padx=(6, 0))
        setattr(self, "wave_" + key, wv)
        return cb

    def _slider(self, parent, r, text, var, lo, hi, fmt):
        self.label(parent, text, 10).grid(row=r, column=0, sticky="w", pady=2)
        ttk.Scale(parent, from_=lo, to=hi, variable=var, orient="horizontal", length=200,
                  command=lambda v: self.apply()).grid(row=r, column=1, padx=6)
        val = self.label(parent, "", 9, C_SUB, width=7)
        val.grid(row=r, column=2)
        var._fmt = (val, fmt)

    def toggle_adv(self):
        self.adv_open = not self.adv_open
        if self.adv_open:
            self.adv.pack(fill="x", pady=(4, 0), before=self.hint)
            self.btn_adv.config(text="⚙ 進階設定 ▾")
        else:
            self.adv.pack_forget()
            self.btn_adv.config(text="⚙ 進階設定 ▸")

    # ------------------------------------------------------------ 裝置
    def refresh_devices(self, rescan=False):
        if rescan and not self.eng.running:
            engine.rescan()
        try:
            ins, outs = engine.list_devices()
        except Exception as e:
            ins, outs = [], []
            self.msg.config(text="讀取裝置失敗：%s" % e)
        self.in_map = {n: i for i, n in ins}
        self.out_map = {n: i for i, n in outs}
        bg = engine.list_backing_sources()
        self.cb_in["values"] = list(self.in_map)
        self.cb_mon["values"] = list(self.out_map)
        self.cb_out["values"] = [NONE] + list(self.out_map)
        self.cb_bgm["values"] = [NONE] + bg

        def pick(var, names, words, fallback):
            if var.get() in names:
                return
            for w in words:
                hit = next((n for n in names if w in n.lower()), None)
                if hit:
                    var.set(hit)
                    return
            var.set(fallback)

        di, do = engine.default_devices()
        names_in, names_out = list(self.in_map), list(self.out_map)
        pick(self.dev_in, names_in, ["tenlamp", "五星", "soundcard"],
             next((n for n, i in self.in_map.items() if i == di), names_in[0] if names_in else ""))
        pick(self.dev_mon, names_out, ["tenlamp", "soundcard"],
             next((n for n, i in self.out_map.items() if i == do), names_out[0] if names_out else ""))
        pick(self.dev_out, names_out, ["cable input", "cable", "vb-audio"], NONE)
        pick(self.dev_bgm, bg, ["digital output", "realtek digital"], NONE)

    def on_device_change(self):
        self.schedule_save()
        if self.eng.running:
            self.eng.stop()
            self.start_engine(self.ultra.get())

    def toggle(self):
        if self.eng.running:
            self.eng.stop()
            self.btn_start.config(text="▶ 開始")
            self.msg.config(text="已停止。")
            return
        self.start_engine(self.ultra.get())

    def start_engine(self, ultra, retried=False):
        try:
            in_dev = self.in_map[self.dev_in.get()]
            mon_dev = self.out_map[self.dev_mon.get()]
            out_dev = self.out_map.get(self.dev_out.get())
        except KeyError:
            messagebox.showerror("裝置", "請先選好「麥克風」和「耳機」。")
            return
        self.apply()
        bgm = self.dev_bgm.get()
        try:
            if ultra:
                self.block_now = 128
            elif self.block_now < 256:
                self.block_now = 256
            self.eng.start(in_dev, mon_dev, out_dev, block=self.block_now, low_latency=bool(ultra),
                           backing=None if bgm in ("", NONE) else bgm, ultra=bool(ultra))
        except Exception as e:
            log_error(traceback.format_exc())
            if not retried and ("Invalid device" in str(e) or "-9996" in str(e) or "-9997" in str(e)):
                # 裝置編號過期：重新讀取裝置，用名稱重新對應後再試一次
                self.eng.stop()
                self.refresh_devices(True)
                return self.start_engine(ultra, retried=True)
            if ultra:
                self.ultra.set(0)
                return self.start_engine(0, retried)
            self.msg.config(text="啟動失敗：%s" % e)
            messagebox.showerror("啟動失敗", str(e))
            return
        self.btn_start.config(text="■ 停止")
        self._ur_hist = []
        self.msg.config(text="運作中。對著麥克風出聲，看「麥克風」那一行有沒有跳動。")

    # ------------------------------------------------------------ 參數
    def apply(self):
        for v in self.vars.values():
            fm = getattr(v, "_fmt", None)
            if fm:
                fm[0].config(text=fm[1].format(v.get()))
        mode = self.mode.get()
        strength = float(self.strength.get()) / 100
        speed = float(self.speed.get()) / 100
        flex = humanize = 0.0
        electro_fx = float(self.electro_fx.get()) / 100
        if self.active_preset == "electro":
            ms, fl, hu, efx = EL_STYLES[self.el_style.get()]
            strength, speed, flex, humanize, electro_fx = 1.0, ms / 400, fl / 100, hu / 100, efx / 100
        elif self.active_preset == "sing":
            ms, fl, hu = NATURAL
            strength, speed, flex, humanize = 1.0, ms / 400, fl / 100, hu / 100
        color = "人聲修飾" if self.active_preset in ("sing", "electro") else "原本"
        kw = dict(flex=flex, humanize=humanize, color=color,
            mode=mode, key=0, scale="major", strength=strength, speed=speed,
            shift=float(self.shift.get()), robot=57,
            phone=bool(float(self.phone.get())), phone_dist=0.25, machine=bool(float(self.machine.get())),
            eq_lo=float(self.eq_lo.get()), eq_mid=float(self.eq_mid.get()), eq_hi=float(self.eq_hi.get()),
            comp=float(self.comp.get()) / 100, verb=float(self.verb.get()) / 100,
            card=bool(self.card.get()), electro_fx=electro_fx, deess=float(self.deess.get()) / 100,
        )
        self.eng.set_params(**kw)
        for name, v in (("inp", self.g_in), ("mon", self.g_mon), ("dry", self.g_dry), ("out", self.g_out), ("bgm", self.g_bgm)):
            self.eng.set_gain(name, float(v.get()) / 100)
        self.eng.bgm_on = bool(self.bgm_on.get())
        self.eng.bgm_trim_ms = float(self.bgm_trim.get())
        if hasattr(self, "btn_card"):
            self.refresh_buttons()
        self.schedule_save()

    def preset(self, name):
        """效果：再按一次同一顆＝關閉（聲卡變音不受影響）。"""
        p = NEUTRAL if self.active_preset == name else PRESETS[name]
        for k, v in p.items():
            getattr(self, k).set(v)
        self.active_preset = None if p is NEUTRAL else name
        self.apply()

    def clear_fx(self):
        for k, v in NEUTRAL.items():
            getattr(self, k).set(v)
        self.active_preset = None
        self.apply()

    def set_mode(self, val):
        self.mode.set("off" if self.mode.get() == val else val)
        self.active_preset = None
        self.apply()

    def toggle_card(self):
        if self.card.get():
            if not messagebox.askyesno("關閉聲卡變音", "關閉後直播會出現你的原音。\n確定要關閉嗎？"):
                return
            self.card.set(0)
        else:
            self.card.set(1)
        self.apply()

    def toggle_bgm(self):
        self.bgm_on.set(0 if self.bgm_on.get() else 1)
        self.apply()

    def refresh_buttons(self):
        on = bool(self.card.get())
        c = C_PINK if on else C_YELLOW
        self.btn_card.config(text="🔒 聲卡變音：開著（按這裡關）" if on else "⚠ 聲卡變音關了＝原音！（按這裡開）",
                             bg=c, activebackground=c, fg="#2a0a1a" if on else "#3a2a00")
        b = bool(self.bgm_on.get())
        c = C_GREEN if b else C_BTN
        self.btn_bgm.config(text="🎵 伴奏：開" if b else "🔇 伴奏：關", bg=c, activebackground=c,
                            fg="#0b2216" if b else C_TXT)
        none = self.active_preset is None
        c = C_GREEN if none else C_BTN
        self.btn_none.config(bg=c, activebackground=c, fg="#0b2216" if none else C_TXT)
        voice = "聲卡變音" if on else "⚠ 原音"
        fxname = PRESET_NAMES[self.active_preset].split(" ", 1)[1] if self.active_preset else "無效果"
        extra = ""
        if self.active_preset == "electro":
            extra = "（%s）" % self.el_style.get()
        col = ""
        self.now_label.config(text="現在送出去的聲音 ＝ %s ＋ %s%s%s" % (voice, fxname, extra, col))
        mark = lambda ok: "✅" if ok else "⬜"
        kind = {"talk": "說話（只修飾音色，不修音）", "sing": "自然修音（拉準走音、保留顫音）",
                "electro": "電音人聲（%s）" % self.el_style.get(), "phone": "電話音", "machine": "答錄機音"}
        self.st_funcs.config(text="目前功能：　%s 聲卡變音　　%s %s　　%s 伴奏" % (
            mark(on), mark(self.active_preset is not None),
            kind.get(self.active_preset, "沒有修音、沒有電音（一般聲音）"), mark(bool(self.bgm_on.get()))),
            fg=C_TXT if on else C_YELLOW)
        for k, btn in self.preset_btns.items():
            c = C_GREEN if self.active_preset == k else C_BTN
            btn.config(bg=c, activebackground=c, fg="#0b2216" if self.active_preset == k else C_TXT)
        self.btn_play_wet.config(text="■ 停止" if self.eng.playing else "▶ 聽錄音")

    # ------------------------------------------------------------ 錄音
    def toggle_rec(self):
        if not self.eng.running:
            messagebox.showinfo("錄音", "請先按「開始」。")
            return
        if self.eng.recording:
            secs = self.eng.stop_recording()
            self.btn_rec.config(text="● 錄音", bg=C_BTN, activebackground=C_BTN)
            self.rec_label.config(text="錄好了 %.1f 秒，按「聽錄音」聽聽看" % secs)
        else:
            self.eng.start_recording()
            self.btn_rec.config(text="■ 停止錄音", bg="#c2306f", activebackground="#c2306f")

    def play(self, which="wet"):
        if self.eng.playing:
            self.eng.stop_play()
            self._play_which = None
            self.refresh_buttons()
            return
        if self.eng.recording:
            self.toggle_rec()
        if not self.eng.has_recording():
            messagebox.showinfo("聽錄音", "還沒有錄音。")
            return
        if not self.eng.play(which):
            messagebox.showinfo("聽錄音", "請先按「開始」才能在耳機播放。")
            return
        self._play_which = which
        self.refresh_buttons()

    def save_rec(self):
        if not self.eng.has_recording():
            messagebox.showinfo("存檔", "還沒有錄音。")
            return
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        d = rec_dir()
        try:
            self.eng.save_wav("wet", os.path.join(d, stamp + "_處理後.wav"))
            self.eng.save_wav("dry", os.path.join(d, stamp + "_原聲.wav"))
        except Exception as e:
            log_error(traceback.format_exc())
            messagebox.showerror("存檔失敗", str(e))
            return
        self.rec_label.config(text="已存到：" + d)
        try:
            os.startfile(d)
        except Exception:
            pass

    # ------------------------------------------------------------ 快捷鍵
    def setup_hotkeys(self):
        try:
            from pynput import keyboard
        except Exception:
            return

        def ui(fn, *a):
            return lambda: self.root.after(0, fn, *a)

        hk = {"<ctrl>+<alt>+%d" % (i + 1): ui(self.preset, k) for i, k in enumerate(PRESET_NAMES)}
        hk.update({"<ctrl>+<alt>+6": ui(self.toggle_card), "<ctrl>+<alt>+b": ui(self.toggle_bgm),
                   "<ctrl>+<alt>+r": ui(self.toggle_rec)})
        try:
            self._hk = keyboard.GlobalHotKeys(hk)
            self._hk.daemon = True
            self._hk.start()
        except Exception:
            log_error(traceback.format_exc())

    # ------------------------------------------------------------ 畫面更新
    def tick(self):
        e = self.eng
        run = e.running
        for k in ("inp", "mon", "out", "bgm"):
            getattr(self, "wave_" + k).push(e.lv[k] if run else 0.0)
        if run:
            f, det, tgt = e.info
            if det is not None and e.fx is not None:
                outp = det + e.fx.tuner.cur          # 實際送出去的音高 ＝ 唱的音 ＋ 目前移動量
                self.st_in.config(text=note_name(det))
                self.st_out.config(text=note_name(outp))
                k = e.fx.tuner.keyt.key
                ks = ("聽起來是 %s 大調" % KEY_NAMES[k]) if (k is not None and self.mode.get() in ("natural", "electro")) else ""
                d = (outp - det) * 100
                self.st_diff.config(text="（%s %d 音分）%s" % ("升高" if d >= 0 else "降低", abs(round(d)), ("\n" + ks) if ks else ""))
            else:
                self.st_in.config(text="—"); self.st_out.config(text="—"); self.st_diff.config(text="（沒有聲音）")
            bk = e.backing
            bmsg = ""
            if e.backing_error or (bk is not None and bk.error):
                bmsg = "\n伴奏抓不到：" + str(e.backing_error or bk.error)
            self.status.config(text="耳機聽自己的延遲：約 %.0f ms\n直播裡的伴奏已自動對齊人聲\n爆音次數：%d%s"
                               % (e.latency_ms, e.underruns, bmsg))
            self.auto_protect()
            if getattr(e, "last_error", None):
                self.msg.config(text="⚠ 處理出錯，聲音沒有送出去！請截圖給 Claude：" + str(e.last_error)[:80], fg="#ff8a8a")
            elif e.in_clip > 0.5:
                self.msg.config(text="⚠ 麥克風進來就太大聲（破音）！請把聲卡上的麥克風旋鈕轉小一點，或把「麥克風」音量拉小。",
                                fg="#ff8a8a")
            elif self.msg.cget("fg") == "#ff8a8a":
                self.msg.config(text="運作中。", fg=C_SUB)
            if e.recording:
                self.rec_label.config(text="錄音中… %.1f 秒" % (e.rec_samples / engine.SR))
            if not e.playing and self._play_which is not None:
                self._play_which = None
                self.refresh_buttons()
        self.root.after(40, self.tick)

    def auto_protect(self):
        """偵測到爆音（緩衝不足）就自動加大緩衝重新連線：128 → 256 → 512 → 1024。"""
        import time
        now = time.time()
        self._ur_hist.append((now, self.eng.underruns))
        self._ur_hist = [(t, u) for t, u in self._ur_hist if now - t < 3.0]
        if len(self._ur_hist) < 2:
            return
        grew = self._ur_hist[-1][1] - self._ur_hist[0][1]
        if grew >= 3 and self.block_now < 1024:
            self.block_now *= 2
            if self.ultra.get():
                self.ultra.set(0)
            self.eng.stop()
            self.start_engine(0)
            self.msg.config(text="偵測到爆音，已自動加大緩衝（%d）。聲音會晚一點點，但不會再爆音。" % self.block_now)

    def on_close(self):
        try:
            self.save_settings()
            self.eng.stop()
        finally:
            self.root.destroy()


def main():
    if engine.sd is None:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("缺少套件", "找不到 sounddevice 套件：\n%s" % engine.SD_ERROR)
        return
    root = tk.Tk()
    try:
        App(root)
    except Exception:
        log_error(traceback.format_exc())
        messagebox.showerror("錯誤", "程式啟動失敗，詳情請看：\n" + os.path.join(data_dir(), "error.log"))
        return
    root.mainloop()


if __name__ == "__main__":
    main()
