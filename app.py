"""怕怕調音台 - 桌面版（Windows）
執行：python app.py    打包：build.bat
"""
import datetime
import json
import os
import sys
import traceback
import tkinter as tk
from tkinter import messagebox, ttk

import engine
from dsp import NOTE_NAMES, note_name

APP = "PaPaVoiceFx"
SCALE_MAP = {"大調": "major", "小調": "minor", "五聲音階": "pent", "藍調": "blues"}
ROBOT_MAP = {"A2": 45, "E3": 52, "A3": 57, "C4": 60, "E4": 64, "A4": 69}
MODES = [("關", "off"), ("音階吸附", "scale"), ("自動半音", "chrom"), ("機器人", "robot")]
KEYS = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

NEUTRAL = dict(mode="off", phone=0, machine=0, strength=100, speed=0, shift=0, comp=40, verb=0, eq_lo=0, eq_mid=0, eq_hi=0)
PRESET_NAMES = {"talk": "說話", "sing": "唱歌(輕修音)", "electro": "電音歌", "phone": "電話音", "machine": "答錄機音"}
PRESETS = {
    "talk": dict(mode="off", phone=0, machine=0, strength=100, speed=0, shift=0, comp=45, verb=5, eq_lo=0, eq_mid=2, eq_hi=2),
    "sing": dict(mode="scale", phone=0, machine=0, strength=55, speed=12, shift=0, comp=40, verb=28, eq_lo=1, eq_mid=1, eq_hi=3),
    "electro": dict(mode="scale", phone=0, machine=0, strength=100, speed=0, shift=0, comp=50, verb=18, eq_lo=0, eq_mid=2, eq_hi=3),
    "phone": dict(mode="off", phone=1, machine=0, strength=100, speed=0, shift=0, comp=60, verb=0, eq_lo=0, eq_mid=0, eq_hi=0),
    "machine": dict(mode="off", phone=0, machine=1, strength=100, speed=0, shift=0, comp=60, verb=0, eq_lo=0, eq_mid=0, eq_hi=0),
}


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


class App:
    def __init__(self, root):
        self.root = root
        root.title("怕怕調音台")
        root.geometry("1120x760")
        self.eng = engine.Engine()
        self.settings = self.load_settings()
        self.in_map, self.out_map = {}, {}
        self._play_which = None
        self._save_job = None
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

    # ------------------------------------------------------------ 介面
    def var(self, name, default, kind=tk.StringVar):
        v = kind(value=self.settings.get(name, default))
        self.vars[name] = v
        return v

    def _build(self):
        self.vars = {}
        st = ttk.Style()
        try:
            st.theme_use("clam")
        except Exception:
            pass
        st.configure("Big.TButton", font=("Microsoft JhengHei", 12, "bold"), padding=8)
        st.configure("H.TLabel", font=("Microsoft JhengHei", 11, "bold"), foreground="#c2306f")
        st.configure("Note.TLabel", font=("Microsoft JhengHei", 24, "bold"))
        st.configure("On.TButton", background="#7dffb0", foreground="#0b2216", font=("Microsoft JhengHei", 10, "bold"))
        st.map("On.TButton", background=[("active", "#a8ffcc")])
        st.configure("Lock.TButton", background="#ff7eb6", foreground="#2a0a1a", font=("Microsoft JhengHei", 13, "bold"), padding=10)
        st.map("Lock.TButton", background=[("active", "#ff9cc7")])
        st.configure("Unlock.TButton", background="#ffd84d", foreground="#3a2a00", font=("Microsoft JhengHei", 13, "bold"), padding=10)
        st.map("Unlock.TButton", background=[("active", "#ffe27a")])

        left = ttk.Frame(self.root, padding=8)
        mid = ttk.Frame(self.root, padding=8)
        right = ttk.Frame(self.root, padding=8)
        left.grid(row=0, column=0, sticky="n")
        mid.grid(row=0, column=1, sticky="n")
        right.grid(row=0, column=2, sticky="n")

        # ---- 左：裝置、啟動、預設、錄音、狀態
        ttk.Label(left, text="① 裝置與啟動", style="H.TLabel").pack(anchor="w")
        self.dev_in = self.var("dev_in", "")
        self.dev_mon = self.var("dev_mon", "")
        self.dev_out = self.var("dev_out", "")
        self.cb_in = self._combo(left, "麥克風輸入（你的聲卡）", self.dev_in)
        self.cb_mon = self._combo(left, "監聽輸出（耳機，插在聲卡上）", self.dev_mon)
        self.cb_out = self._combo(left, "直播輸出（選 CABLE Input）", self.dev_out)
        self.block = self.var("block", "256")
        self.excl = self.var("excl", 0, tk.IntVar)
        self.lowlat = self.var("lowlat", 0, tk.IntVar)
        row = ttk.Frame(left)
        row.pack(fill="x", pady=2)
        ttk.Label(row, text="區塊大小").pack(side="left")
        ttk.Combobox(row, textvariable=self.block, values=["128", "256", "512"], width=6, state="readonly").pack(side="left", padx=6)
        ttk.Button(row, text="重新整理裝置", command=self.refresh_devices).pack(side="right")
        ttk.Checkbutton(left, text="WASAPI 獨佔模式（延遲最低，需聲卡支援）", variable=self.excl).pack(anchor="w")
        ttk.Checkbutton(left, text="低延遲電音（處理視窗較短，音質稍粗）", variable=self.lowlat).pack(anchor="w")
        self.btn_start = ttk.Button(left, text="▶ 開始", style="Big.TButton", command=self.toggle)
        self.btn_start.pack(fill="x", pady=8)
        self.msg = ttk.Label(left, text="請先戴上耳機再開始。", wraplength=300, foreground="#666")
        self.msg.pack(anchor="w")

        ttk.Label(left, text="② 聲卡變音（鎖定）", style="H.TLabel").pack(anchor="w", pady=(12, 2))
        self.card = tk.IntVar(value=1)   # 每次開啟一律鎖定，避免直播出現原音
        self.vars["card"] = self.card
        self.btn_card = ttk.Button(left, command=self.toggle_card)
        self.btn_card.pack(fill="x")
        ttk.Label(left, text="開著時，不管按哪個效果都會疊在聲卡變音上，只有按這顆才會關。", foreground="#666", wraplength=300).pack(anchor="w")
        ttk.Label(left, text="③ 一鍵效果（再按一次＝關閉）", style="H.TLabel").pack(anchor="w", pady=(12, 2))
        pf = ttk.Frame(left)
        pf.pack(fill="x")
        self.active_preset = None
        self.preset_btns = {}
        for i, (key, txt) in enumerate(PRESET_NAMES.items()):
            b = ttk.Button(pf, text=txt, command=lambda k=key: self.preset(k))
            b.grid(row=i // 2, column=i % 2, sticky="ew", padx=2, pady=2)
            self.preset_btns[key] = b
        pf.columnconfigure(0, weight=1)
        pf.columnconfigure(1, weight=1)
        ttk.Label(left, text="全域快捷鍵（再按一次＝關閉）：Ctrl+Alt+ 1說話 2唱歌 3電音 4電話 5答錄機 6聲卡變音鎖定\nE=電音開關  P=電話音開關  M=答錄機開關  R=錄音開關",
                  foreground="#666", justify="left").pack(anchor="w", pady=4)

        ttk.Label(left, text="④ 錄音與回放", style="H.TLabel").pack(anchor="w", pady=(10, 2))
        rf = ttk.Frame(left)
        rf.pack(fill="x")
        self.btn_rec = ttk.Button(rf, text="● 錄音", command=self.toggle_rec)
        self.btn_rec.grid(row=0, column=0, columnspan=2, sticky="ew", padx=2, pady=2)
        self.btn_play_wet = ttk.Button(rf, text="▶ 聽處理後", command=lambda: self.play("wet"))
        self.btn_play_wet.grid(row=1, column=0, sticky="ew", padx=2, pady=2)
        self.btn_play_dry = ttk.Button(rf, text="▶ 聽原聲", command=lambda: self.play("dry"))
        self.btn_play_dry.grid(row=1, column=1, sticky="ew", padx=2, pady=2)
        ttk.Button(rf, text="💾 存成 WAV", command=self.save_rec).grid(row=2, column=0, columnspan=2, sticky="ew", padx=2, pady=2)
        rf.columnconfigure(0, weight=1)
        rf.columnconfigure(1, weight=1)
        self.rec_label = ttk.Label(left, text="尚未錄音", foreground="#666")
        self.rec_label.pack(anchor="w")
        ttk.Label(left, text="回放時耳機只聽錄音，直播輸出不受影響。", foreground="#666").pack(anchor="w")

        # ---- 中：電音
        ttk.Label(mid, text="④ 電音／修音", style="H.TLabel").pack(anchor="w")
        self.note_label = ttk.Label(mid, text="—", style="Note.TLabel")
        self.note_label.pack(anchor="w")
        self.hz_label = ttk.Label(mid, text="偵測到的音 → 修正後的音", foreground="#666")
        self.hz_label.pack(anchor="w")
        self.meter = ttk.Progressbar(mid, maximum=100, length=300)
        self.meter.pack(fill="x", pady=4)
        self.mode = self.var("mode", "off")
        mf = ttk.Frame(mid)
        mf.pack(fill="x", pady=4)
        self.mode_btns = {}
        for txt, val in MODES[1:]:
            b = ttk.Button(mf, text=txt, command=lambda v=val: self.set_mode(v))
            b.pack(side="left", padx=2, expand=True, fill="x")
            self.mode_btns[val] = b
        kf = ttk.Frame(mid)
        kf.pack(fill="x")
        self.key = self.var("key", "C")
        self.scale = self.var("scale", "大調")
        self.robot = self.var("robot", "A3")
        ttk.Label(kf, text="調").pack(side="left")
        c = ttk.Combobox(kf, textvariable=self.key, values=KEYS, width=4, state="readonly")
        c.pack(side="left", padx=4)
        c.bind("<<ComboboxSelected>>", lambda e: self.apply())
        ttk.Label(kf, text="音階").pack(side="left")
        c = ttk.Combobox(kf, textvariable=self.scale, values=list(SCALE_MAP), width=8, state="readonly")
        c.pack(side="left", padx=4)
        c.bind("<<ComboboxSelected>>", lambda e: self.apply())
        ttk.Label(kf, text="機器人音").pack(side="left")
        c = ttk.Combobox(kf, textvariable=self.robot, values=list(ROBOT_MAP), width=4, state="readonly")
        c.pack(side="left", padx=4)
        c.bind("<<ComboboxSelected>>", lambda e: self.apply())
        self.strength = self.var("strength", 100, tk.DoubleVar)
        self.speed = self.var("speed", 0, tk.DoubleVar)
        self.shift = self.var("shift", 0, tk.DoubleVar)
        self.slider(mid, "電音強度（100＝完全吸附）", self.strength, 0, 100, "{:.0f}%")
        self.slider(mid, "修正速度（0＝瞬間跳音最電）", self.speed, 0, 100, "{:.0f}%")
        self.slider(mid, "額外升降調（半音，疊在聲卡變音之上）", self.shift, -12, 12, "{:+.1f}")

        ttk.Label(mid, text="⑤ 特殊音色", style="H.TLabel").pack(anchor="w", pady=(14, 0))
        self.phone = self.var("phone", 0, tk.IntVar)
        self.machine = self.var("machine", 0, tk.IntVar)
        self.phone_dist = self.var("phone_dist", 25, tk.DoubleVar)
        ttk.Checkbutton(mid, text="電話音（窄頻＋失真）", variable=self.phone, command=self.apply).pack(anchor="w")
        self.slider(mid, "電話音失真", self.phone_dist, 0, 100, "{:.0f}%")
        ttk.Checkbutton(mid, text="答錄機音（乾淨版：像從答錄機小喇叭播出，不失真）", variable=self.machine, command=self.apply).pack(anchor="w", pady=(6, 0))

        # ---- 右：音色與音量
        ttk.Label(right, text="⑥ 音色", style="H.TLabel").pack(anchor="w")
        self.eq_lo = self.var("eq_lo", 0, tk.DoubleVar)
        self.eq_mid = self.var("eq_mid", 0, tk.DoubleVar)
        self.eq_hi = self.var("eq_hi", 0, tk.DoubleVar)
        self.comp = self.var("comp", 40, tk.DoubleVar)
        self.verb = self.var("verb", 15, tk.DoubleVar)
        self.slider(right, "低頻（厚度）", self.eq_lo, -12, 12, "{:+.0f} dB")
        self.slider(right, "中頻（明亮度）", self.eq_mid, -12, 12, "{:+.0f} dB")
        self.slider(right, "高頻（空氣感）", self.eq_hi, -12, 12, "{:+.0f} dB")
        self.slider(right, "壓縮（大小聲更平均）", self.comp, 0, 100, "{:.0f}%")
        self.slider(right, "混響（空間感）", self.verb, 0, 100, "{:.0f}%")

        ttk.Label(right, text="⑦ 音量", style="H.TLabel").pack(anchor="w", pady=(14, 0))
        self.g_in = self.var("g_in", 100, tk.DoubleVar)
        self.g_mon = self.var("g_mon", 100, tk.DoubleVar)
        self.g_dry = self.var("g_dry", 0, tk.DoubleVar)
        self.g_out = self.var("g_out", 100, tk.DoubleVar)
        self.slider(right, "麥克風輸入增益", self.g_in, 0, 200, "{:.0f}%")
        self.slider(right, "監聽音量（耳機）", self.g_mon, 0, 150, "{:.0f}%")
        self.slider(right, "監聽混入原聲（抓拍用）", self.g_dry, 0, 100, "{:.0f}%")
        self.slider(right, "直播輸出音量", self.g_out, 0, 150, "{:.0f}%")
        self.status = ttk.Label(right, text="", foreground="#666", wraplength=300, justify="left")
        self.status.pack(anchor="w", pady=10)

    def _combo(self, parent, text, var):
        ttk.Label(parent, text=text).pack(anchor="w", pady=(6, 0))
        cb = ttk.Combobox(parent, textvariable=var, state="readonly", width=42)
        cb.pack(fill="x")
        return cb

    def slider(self, parent, text, var, lo, hi, fmt):
        f = ttk.Frame(parent)
        f.pack(fill="x", pady=2)
        top = ttk.Frame(f)
        top.pack(fill="x")
        ttk.Label(top, text=text).pack(side="left")
        val = ttk.Label(top, text="", foreground="#1a7fa6")
        val.pack(side="right")
        s = ttk.Scale(f, from_=lo, to=hi, variable=var, orient="horizontal", length=300, command=lambda v: self.apply())
        s.pack(fill="x")
        var._fmt = (val, fmt)

    # ------------------------------------------------------------ 裝置
    def refresh_devices(self):
        try:
            ins, outs = engine.list_devices()
        except Exception as e:
            ins, outs = [], []
            self.msg.config(text="讀取裝置失敗：%s" % e)
        self.in_map = {n: i for i, n in ins}
        self.out_map = {n: i for i, n in outs}
        self.cb_in["values"] = list(self.in_map)
        self.cb_mon["values"] = list(self.out_map)
        self.cb_out["values"] = ["（不使用）"] + list(self.out_map)
        di, do = engine.default_devices()
        names_in = list(self.in_map)
        names_out = list(self.out_map)
        if self.dev_in.get() not in self.in_map:
            self.dev_in.set(next((n for n, i in self.in_map.items() if i == di), names_in[0] if names_in else ""))
        if self.dev_mon.get() not in self.out_map:
            self.dev_mon.set(next((n for n, i in self.out_map.items() if i == do), names_out[0] if names_out else ""))
        if self.dev_out.get() not in self.out_map:
            cable = next((n for n in names_out if "cable" in n.lower() or "vb-audio" in n.lower()), "（不使用）")
            self.dev_out.set(cable)

    def toggle(self):
        if self.eng.running:
            self.eng.stop()
            self.btn_start.config(text="▶ 開始")
            self.msg.config(text="已停止。")
            return
        self.start_engine(self.excl.get())

    def start_engine(self, exclusive):
        try:
            in_dev = self.in_map[self.dev_in.get()]
            mon_dev = self.out_map[self.dev_mon.get()]
            out_dev = self.out_map.get(self.dev_out.get())
        except KeyError:
            messagebox.showerror("裝置", "請先選好麥克風與監聽輸出。")
            return
        self.apply()
        try:
            self.eng.start(in_dev, mon_dev, out_dev, block=int(self.block.get()),
                           exclusive=bool(exclusive), low_latency=bool(self.lowlat.get()))
        except Exception as e:
            log_error(traceback.format_exc())
            if exclusive:
                self.msg.config(text="獨佔模式失敗，改用一般模式。")
                self.excl.set(0)
                return self.start_engine(0)
            self.msg.config(text="啟動失敗：%s" % e)
            messagebox.showerror("啟動失敗", str(e))
            return
        self.btn_start.config(text="■ 停止")
        where = "直播輸出：" + (self.dev_out.get() if out_dev is not None else "未使用")
        self.msg.config(text="運作中。%s" % where)

    # ------------------------------------------------------------ 參數
    def apply(self):
        if hasattr(self, "btn_card"):
            self.refresh_buttons()
        for v in self.vars.values():
            fm = getattr(v, "_fmt", None)
            if fm:
                fm[0].config(text=fm[1].format(v.get()))
        kw = dict(
            mode=self.mode.get(), key=KEYS.index(self.key.get()), scale=SCALE_MAP.get(self.scale.get(), "major"),
            strength=self.strength.get() / 100, speed=self.speed.get() / 100, shift=round(self.shift.get() * 10) / 10,
            card=bool(self.card.get()),
            robot=ROBOT_MAP.get(self.robot.get(), 57),
            phone=bool(self.phone.get()), phone_dist=self.phone_dist.get() / 100, machine=bool(self.machine.get()),
            eq_lo=round(self.eq_lo.get()), eq_mid=round(self.eq_mid.get()), eq_hi=round(self.eq_hi.get()),
            comp=self.comp.get() / 100, verb=self.verb.get() / 100,
        )
        self.eng.set_params(**kw)
        self.eng.set_gain("inp", self.g_in.get() / 100)
        self.eng.set_gain("mon", self.g_mon.get() / 100)
        self.eng.set_gain("dry", self.g_dry.get() / 100)
        self.eng.set_gain("out", self.g_out.get() / 100)
        self.schedule_save()

    def preset(self, name):
        """一鍵效果：再按一次同一個＝關閉（回到乾淨狀態，聲卡變音不受影響）。"""
        p = NEUTRAL if self.active_preset == name else PRESETS[name]
        for k, v in p.items():
            getattr(self, k).set(v)
        self.active_preset = None if p is NEUTRAL else name
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

    def refresh_buttons(self):
        on = bool(self.card.get())
        self.btn_card.config(text=("🔒 聲卡變音：鎖定中（按這裡關閉）" if on else "⚠ 聲卡變音已關閉＝原音！（按這裡打開）"),
                             style="Lock.TButton" if on else "Unlock.TButton")
        for k, b in self.preset_btns.items():
            b.config(style="On.TButton" if self.active_preset == k else "TButton")
        for v, b in self.mode_btns.items():
            b.config(style="On.TButton" if self.mode.get() == v else "TButton")
        self.btn_play_wet.config(text="■ 停止播放" if self.eng.playing and self._play_which == "wet" else "▶ 聽處理後")
        self.btn_play_dry.config(text="■ 停止播放" if self.eng.playing and self._play_which == "dry" else "▶ 聽原聲")

    # ------------------------------------------------------------ 錄音
    def toggle_rec(self):
        if not self.eng.running:
            messagebox.showinfo("錄音", "請先按「開始」。")
            return
        if self.eng.recording:
            secs = self.eng.stop_recording()
            self.btn_rec.config(text="● 錄音")
            self.rec_label.config(text="已錄 %.1f 秒，可播放或存檔" % secs)
        else:
            self.eng.start_recording()
            self.btn_rec.config(text="■ 停止錄音")

    def play(self, which):
        if self.eng.playing and self._play_which == which:
            self.eng.stop_play()
            self.refresh_buttons()
            return
        self._play_which = which
        if self.eng.recording:
            self.toggle_rec()
        if not self.eng.has_recording():
            messagebox.showinfo("播放", "還沒有錄音。")
            return
        if not self.eng.play(which):
            messagebox.showinfo("播放", "請先按「開始」才能在耳機播放。")

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

        def toggler(var):
            def f():
                var.set(0 if var.get() else 1)
                self.apply()
            return f

        def electro():
            if self.mode.get() == "off":
                self.mode.set(self._last_mode if getattr(self, "_last_mode", "off") != "off" else "scale")
            else:
                self._last_mode = self.mode.get()
                self.mode.set("off")
            self.active_preset = None
            self.apply()

        hk = {
            "<ctrl>+<alt>+1": ui(self.preset, "talk"), "<ctrl>+<alt>+2": ui(self.preset, "sing"),
            "<ctrl>+<alt>+3": ui(self.preset, "electro"), "<ctrl>+<alt>+4": ui(self.preset, "phone"),
            "<ctrl>+<alt>+5": ui(self.preset, "machine"), "<ctrl>+<alt>+6": ui(self.toggle_card),
            "<ctrl>+<alt>+e": ui(electro), "<ctrl>+<alt>+p": ui(toggler(self.phone)),
            "<ctrl>+<alt>+m": ui(toggler(self.machine)), "<ctrl>+<alt>+r": ui(self.toggle_rec),
        }
        try:
            self._hk = keyboard.GlobalHotKeys(hk)
            self._hk.daemon = True
            self._hk.start()
        except Exception:
            log_error(traceback.format_exc())

    # ------------------------------------------------------------ 狀態更新
    def tick(self):
        e = self.eng
        if e.running:
            f, det, tgt = e.info
            if det is None:
                self.note_label.config(text="—")
                self.hz_label.config(text="沒有偵測到音高")
            else:
                self.note_label.config(text=note_name(det) + ("  →  " + note_name(tgt) if tgt is not None else ""))
                self.hz_label.config(text="%.0f Hz" % f)
            self.meter["value"] = min(100, e.level * 110)
            self.status.config(text="預估延遲約 %.0f ms（另加電音處理 10～20 ms）\n緩衝不足次數：%d" % (e.latency_ms, e.underruns))
            if e.recording:
                self.rec_label.config(text="錄音中… %.1f 秒" % (e.rec_samples / engine.SR))
            elif e.playing:
                self.rec_label.config(text="播放中…")
            if not e.playing and self._play_which is not None:
                self._play_which = None
                self.refresh_buttons()
        else:
            self.meter["value"] = 0
        self.root.after(50, self.tick)

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
        messagebox.showerror("缺少套件", "找不到 sounddevice 套件：\n%s\n\n請依說明重新安裝。" % engine.SD_ERROR)
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
