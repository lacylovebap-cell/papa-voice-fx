"""PAPA 調音台 - 混音台頁面（Studio One 風格）＋旋鈕、推桿、音量表。

設計：
- 每條「通道」：名稱、上方插入效果／設定、直立推桿＋音量表、數值
- 右邊「編輯器」：點通道上的效果，就在這裡用旋鈕細調（像 Auto-Tune／外掛視窗）
"""
import math
import tkinter as tk
from tkinter import ttk

# Studio One 風格配色：石墨灰、藍色重點色
BG = "#1b1e22"
PANEL = "#23272c"
STRIP = "#2b3036"
STRIP_HI = "#323840"
EDIT_BG = "#202428"
DEEP = "#15181b"
LINE = "#3a3f46"
TXT = "#e6e9ed"
SUB = "#8e959d"
CYAN = "#3ba4e0"        # 重點色（Studio One 藍）
PINK = "#f06c9b"
GREEN = "#5fd38d"
YELLOW = "#f2c14e"
PURPLE = "#a78bfa"
FONT = "Microsoft JhengHei UI"


# ---------------------------------------------------------------- 旋鈕（Auto-Tune 風格）
class Knob(tk.Canvas):
    """旋鈕：上下拖曳調整（按住 Shift 更細）、滑鼠滾輪微調、雙擊恢復預設。綁定一個 tk 變數。"""

    START, SWEEP = 225.0, 270.0

    def __init__(self, parent, var, lo, hi, label, fmt="{:.0f}", default=None, on_change=None, size=78, step=None):
        super().__init__(parent, width=size + 24, height=size + 46, bg=parent["bg"], highlightthickness=0)
        self.var, self.lo, self.hi, self.fmt = var, float(lo), float(hi), fmt
        self.default = float(default if default is not None else lo)
        self.on_change = on_change
        self.size = size
        self.step = step or (self.hi - self.lo) / 100.0
        self.label = label
        self._y = None
        self._trace = var.trace_add("write", lambda *a: self.redraw())
        self.bind("<ButtonPress-1>", self._press)
        self.bind("<B1-Motion>", self._drag)
        self.bind("<ButtonRelease-1>", lambda e: setattr(self, "_y", None))
        self.bind("<Double-Button-1>", lambda e: self.set(self.default))
        self.bind("<MouseWheel>", self._wheel)
        self.bind("<Button-4>", lambda e: self._wheel(e, 1))
        self.bind("<Button-5>", lambda e: self._wheel(e, -1))
        self.bind("<Destroy>", self._cleanup)
        self.redraw()

    def _cleanup(self, e=None):
        try:
            self.var.trace_remove("write", self._trace)
        except Exception:
            pass

    def value(self):
        try:
            return float(self.var.get())
        except Exception:
            return self.default

    def set(self, v, notify=True):
        v = min(self.hi, max(self.lo, float(v)))
        if self.step >= 1:
            v = round(v)
        else:
            v = round(v / self.step) * self.step
        if abs(v - self.value()) > 1e-9:
            self.var.set(v)
            if notify and self.on_change:
                self.on_change()
        self.redraw()

    def _press(self, e):
        self._y = e.y
        self._v0 = self.value()

    def _drag(self, e):
        if self._y is None:
            return
        fine = (e.state & 0x0001) != 0
        span = self.hi - self.lo
        dv = (self._y - e.y) * span / (600.0 if fine else 150.0)
        self.set(self._v0 + dv)

    def _wheel(self, e, direction=None):
        d = direction if direction is not None else (1 if e.delta > 0 else -1)
        self.set(self.value() + d * self.step)
        return "break"

    def redraw(self):
        self.delete("all")
        s = self.size
        x0, y0 = 12, 6
        cx, cy = x0 + s / 2, y0 + s / 2
        frac = (self.value() - self.lo) / max(1e-9, self.hi - self.lo)
        frac = min(1.0, max(0.0, frac))
        # 外圈刻度＋底軌
        self.create_arc(x0 + 3, y0 + 3, x0 + s - 3, y0 + s - 3, start=self.START, extent=-self.SWEEP, style="arc", outline="#14171a", width=5)
        if frac > 0.001:
            self.create_arc(x0 + 3, y0 + 3, x0 + s - 3, y0 + s - 3, start=self.START, extent=-self.SWEEP * frac, style="arc", outline=CYAN, width=5)
        # 旋鈕本體（金屬感：外圈亮、內圈暗）
        r = s * 0.34
        self.create_oval(cx - r - 2, cy - r - 2, cx + r + 2, cy + r + 2, fill="#0f1113", outline="")
        self.create_oval(cx - r, cy - r, cx + r, cy + r, fill="#4a5058", outline="#5d646d", width=1)
        self.create_oval(cx - r * 0.82, cy - r * 0.82, cx + r * 0.82, cy + r * 0.82, fill="#353a41", outline="")
        ang = math.radians(self.START - self.SWEEP * frac)
        px, py = cx + math.cos(ang) * r * 0.75, cy - math.sin(ang) * r * 0.75
        self.create_line(cx + math.cos(ang) * r * 0.2, cy - math.sin(ang) * r * 0.2, px, py, fill="white", width=2, capstyle="round")
        # 數值框＋名稱
        try:
            txt = self.fmt(self.value()) if callable(self.fmt) else self.fmt.format(self.value())
        except Exception:
            txt = str(self.value())
        self.create_rectangle(x0 + 6, y0 + s + 4, x0 + s - 6, y0 + s + 22, outline=LINE, width=1, fill=DEEP)
        self.create_text(cx, y0 + s + 13, text=txt, fill=CYAN, font=(FONT, 9, "bold"))
        self.create_text(cx, y0 + s + 34, text=self.label, fill=SUB, font=(FONT, 9))


# ---------------------------------------------------------------- 直立音量表（分段：綠→黃→紅）
class VMeter(tk.Canvas):
    SEG = 28

    def __init__(self, parent, h=180, w=8, color=GREEN):
        super().__init__(parent, width=w, height=h, bg=DEEP, highlightthickness=0)
        self.h, self.w = h, w
        self.level = 0.0
        self.pk = 0.0
        sh = h / self.SEG
        self.segs = []
        for k in range(self.SEG):
            y1 = h - k * sh
            frac = k / self.SEG
            on = "#e5534b" if frac > 0.88 else ("#e9c46a" if frac > 0.7 else "#4cc77f")
            off = "#2a1c1c" if frac > 0.88 else ("#2a2719" if frac > 0.7 else "#1b2a21")
            r = self.create_rectangle(1, y1 - sh + 1, w - 1, y1 - 0.5, fill=off, width=0)
            self.segs.append((r, on, off))
        self.peak = self.create_line(0, h, w, h, fill="white")
        self._lit = -1

    def push(self, v):
        v = min(1.0, max(0.0, float(v)))
        self.level = max(v, self.level * 0.75)
        self.pk = max(self.level, self.pk - 0.008)
        n = int(round((self.level ** 0.5) * self.SEG))
        if n != self._lit:
            for k, (r, on, off) in enumerate(self.segs):
                self.itemconfig(r, fill=on if k < n else off)
            self._lit = n
        yp = self.h - (self.pk ** 0.5) * self.h
        self.coords(self.peak, 0, yp, self.w, yp)


# ---------------------------------------------------------------- 直立推桿（金屬推頭＋刻度）
class Fader(tk.Canvas):
    """混音台推桿：上下拖曳、滾輪微調、雙擊回到 100%。綁定一個 tk 變數（0～最大值，單位 %）。"""

    def __init__(self, parent, var, hi, on_change=None, h=180, w=40, default=100.0):
        super().__init__(parent, width=w, height=h, bg=parent["bg"], highlightthickness=0, cursor="hand2")
        self.var, self.hi, self.on_change, self.h, self.w = var, float(hi), on_change, h, w
        self.default = default
        self.top, self.bot = 10, h - 10
        self._trace = var.trace_add("write", lambda *a: self.redraw())
        self.bind("<ButtonPress-1>", self._press)
        self.bind("<B1-Motion>", self._drag)
        self.bind("<Double-Button-1>", lambda e: self.set(self.default))
        self.bind("<MouseWheel>", lambda e: (self.set(self.value() + (2 if e.delta > 0 else -2)), "break")[1])
        self.bind("<Destroy>", lambda e: self._cleanup())
        self.redraw()

    def _cleanup(self):
        try:
            self.var.trace_remove("write", self._trace)
        except Exception:
            pass

    def value(self):
        try:
            return float(self.var.get())
        except Exception:
            return 0.0

    def set(self, v):
        v = round(min(self.hi, max(0.0, float(v))))
        if abs(v - self.value()) > 1e-9:
            self.var.set(v)
            if self.on_change:
                self.on_change()
        self.redraw()

    def _y_of(self, v):
        return self.bot - (v / self.hi) * (self.bot - self.top)

    def _press(self, e):
        self._y0, self._v0 = e.y, self.value()

    def _drag(self, e):
        fine = (e.state & 0x0001) != 0
        dv = (self._y0 - e.y) * self.hi / (self.bot - self.top) * (0.25 if fine else 1.0)
        self.set(self._v0 + dv)

    def redraw(self):
        self.delete("all")
        cx = self.w // 2 + 4
        # 刻度
        for k in range(11):
            y = self.top + k * (self.bot - self.top) / 10
            self.create_line(2, y, 8 if k % 5 else 11, y, fill="#5d646d")
        y100 = self._y_of(100) if self.hi >= 100 else None
        if y100 is not None:
            self.create_line(2, y100, 13, y100, fill=CYAN)
        # 軌道
        self.create_rectangle(cx - 2, self.top, cx + 2, self.bot, fill="#0c0e10", outline="#30353b")
        # 推頭（金屬）
        y = self._y_of(self.value())
        self.create_rectangle(cx - 12, y - 9, cx + 12, y + 9, fill="#7b838d", outline="#2a2e33")
        self.create_rectangle(cx - 12, y - 9, cx + 12, y - 3, fill="#959ea8", outline="")
        self.create_rectangle(cx - 12, y + 3, cx + 12, y + 9, fill="#5d646d", outline="")
        self.create_line(cx - 12, y, cx + 12, y, fill="white", width=2)


def label(parent, text, size=10, color=TXT, bold=False, **kw):
    return tk.Label(parent, text=text, bg=parent["bg"], fg=color, font=(FONT, size, "bold" if bold else "normal"), **kw)


def small_button(parent, text, cmd, color="#3a3f46", fg=TXT, size=9, **kw):
    return tk.Button(parent, text=text, command=cmd, bg=color, fg=fg, activebackground="#4a5058", activeforeground=fg,
                     relief="flat", font=(FONT, size, "bold"), cursor="hand2", bd=0, padx=6, pady=3,
                     highlightthickness=1, highlightbackground="#121417", **kw)


# ---------------------------------------------------------------- 混音台頁面
class MixerPage(tk.Frame):
    """app：主程式（PAPA 調音台 App），提供變數與動作。"""

    FADER_H = 170

    def __init__(self, parent, app):
        super().__init__(parent, bg=BG, padx=10, pady=8)
        self.app = app
        self.meters = {}
        self.knobs = {}
        self.editing = None        # ("fx", None) / ("rv", i) / ("deess", None) / ("card", None) / ("align", None)
        self.dirty = False
        label(self, "推桿＝音量；點通道上的按鈕，右邊出現旋鈕。旋鈕上下拖曳調整，雙擊恢復預設。",
              9, SUB, anchor="w").pack(fill="x", pady=(0, 6))
        body = tk.Frame(self, bg=BG)
        body.pack(fill="both", expand=True)
        self.strips = tk.Frame(body, bg=BG)
        self.strips.pack(side="left", fill="y")
        self.editor = tk.Frame(body, bg=EDIT_BG, padx=12, pady=10, highlightthickness=1, highlightbackground=LINE)
        self.editor.pack(side="left", fill="both", expand=True, padx=(8, 0))
        self._build_strips()
        self.open_editor("fx")

    # ---- 通道
    def _strip(self, title, color, var, top, gmax, meter_key=None, fmt="{:.0f}%"):
        f = tk.Frame(self.strips, bg=STRIP, padx=3, pady=0, width=86, highlightthickness=1, highlightbackground="#121417")
        f.pack(side="left", fill="y", padx=1)
        f.pack_propagate(False)
        tk.Frame(f, bg=color, height=4).pack(fill="x")          # 通道顏色條（像 Studio One）
        hd = tk.Frame(f, bg=STRIP_HI)
        hd.pack(fill="x")
        tk.Label(hd, text=title, bg=STRIP_HI, fg=TXT, font=(FONT, 10, "bold")).pack(pady=3)
        area = tk.Frame(f, bg=STRIP, height=176)
        area.pack(fill="x", pady=(4, 4))
        area.pack_propagate(False)
        top(area)
        fr = tk.Frame(f, bg=STRIP)
        fr.pack()
        fd = Fader(fr, var, gmax, on_change=lambda: self.app.apply(), h=self.FADER_H,
                   default=100.0 if gmax >= 100 and meter_key else 30.0)
        fd.pack(side="left")
        if meter_key:
            m = VMeter(fr, h=self.FADER_H, color=color)
            m.pack(side="left", padx=(2, 0))
            self.meters[meter_key] = m
        val = tk.Label(f, text="", bg=DEEP, fg=CYAN, font=(FONT, 9, "bold"), width=7)
        val.pack(pady=(6, 6))
        var._fmt2 = (val, fmt)
        return f

    def _build_strips(self):
        a = self.app

        def mic_top(area):
            label(area, "麥克風進來的音量", 8, SUB, wraplength=80, justify="center").pack()

        def voice_top(area):
            label(area, "插入效果", 8, SUB).pack()
            self.btn_ins_card = small_button(area, "", lambda: self.open_editor("card"))
            self.btn_ins_card.pack(fill="x", pady=1)
            self.btn_ins_fx = small_button(area, "", lambda: self.open_editor("fx"))
            self.btn_ins_fx.pack(fill="x", pady=1)
            small_button(area, "🎚 EQ／壓縮", lambda: self.open_editor("fx")).pack(fill="x", pady=1)
            small_button(area, "🦷 去齒音", lambda: self.open_editor("deess")).pack(fill="x", pady=1)
            small_button(area, "✨ 人聲質感", lambda: self.open_editor("polish")).pack(fill="x", pady=1)

        def bgm_top(area):
            self.btn_bgm2 = small_button(area, "", a.toggle_bgm)
            self.btn_bgm2.pack(fill="x", pady=1)
            label(area, "對齊／避讓", 8, SUB).pack(pady=(6, 0))
            small_button(area, "⏱ 調整", lambda: self.open_editor("align")).pack(fill="x", pady=1)

        def rv_top(i):
            def top(area):
                cb = ttk.Combobox(area, textvariable=a.rv[i]["type"], values=["關"] + a.reverb_names, state="readonly", width=7)
                cb.pack(pady=(0, 2))
                cb.bind("<<ComboboxSelected>>", lambda e: (a.on_reverb_type(i), self.open_editor("rv", i)))
                small_button(area, "⚙ 細調", lambda: self.open_editor("rv", i)).pack(fill="x", pady=1)
                label(area, "所有效果共用\n切換效果不會變", 8, SUB, justify="center").pack(pady=(4, 0))
            return top

        def mon_top(area):
            label(area, "自己耳機\n聽到的音量", 8, SUB, justify="center").pack()

        self._strip("麥克風", "#4fc3f7", a.g_in, mic_top, 200, "inp")
        self._strip("人聲", PINK, a.g_out, voice_top, 150, "out")
        self._strip("伴奏", YELLOW, a.g_bgm, bgm_top, 150, "bgm")
        for i, name in enumerate(("A", "B", "C")):
            self._strip("混響 " + name, ("#a78bfa", "#7c8cf8", "#4dd0c8")[i], a.rv[i]["mix"], rv_top(i), 100)
        self._strip("耳機", GREEN, a.g_mon, mon_top, 150, "mon")

    # ---- 編輯器
    def clear_editor(self):
        for w in self.editor.winfo_children():
            w.destroy()
        self.knobs = {}

    def _knob_grid(self, parent, items, cols=3):
        """items: [(key, var, lo, hi, label, fmt, default, step)]"""
        g = tk.Frame(parent, bg=EDIT_BG)
        g.pack(fill="x", pady=(6, 0))
        for n, (key, var, lo, hi, lab, fmt, dflt, step, cb) in enumerate(items):
            k = Knob(g, var, lo, hi, lab, fmt, dflt, cb, step=step, size=70)
            k.grid(row=n // cols, column=n % cols, padx=2, pady=2)
            self.knobs[key] = k
        return g

    def open_editor(self, kind, idx=None):
        a = self.app
        self.editing = (kind, idx)
        self.clear_editor()
        ed = self.editor
        if kind == "fx":
            p = a.active_preset
            if p is None:
                label(ed, "效果編輯器", 12, CYAN, True).pack(anchor="w")
                label(ed, "現在沒有開效果。\n先在「簡單」頁或下面點一個效果，這裡就會出現它的旋鈕。", 10, SUB, justify="left").pack(anchor="w", pady=6)
                row = tk.Frame(ed, bg=EDIT_BG); row.pack(anchor="w")
                for k, txt in a.preset_names.items():
                    small_button(row, txt, lambda kk=k: (a.preset(kk), self.open_editor("fx"))).pack(side="left", padx=2)
                return
            label(ed, "效果編輯器　%s" % a.preset_names[p], 12, CYAN, True).pack(anchor="w")
            label(ed, "這裡調的只影響「%s」，會自動記住；切到別的效果再切回來還在。" % a.preset_names[p], 9, SUB, wraplength=330, justify="left").pack(anchor="w")
            if p in ("electro", "sing"):
                row = tk.Frame(ed, bg=EDIT_BG); row.pack(anchor="w", pady=(6, 0))
                label(row, "套用參考預設：", 10).pack(side="left")
                names = a.ref_presets(p)
                cb = ttk.Combobox(row, values=names, state="readonly", width=22)
                cb.pack(side="left")
                cb.bind("<<ComboboxSelected>>", lambda e: (a.apply_ref_preset(p, cb.get()), self.open_editor("fx")))
                self.ref_combo = cb
            items = []
            for key in a.edit_keys(p):
                spec = a.param_spec[key]
                items.append((key, a.fxv[key], spec[1], spec[2], spec[0], spec[3], a.fx_default(p, key), spec[4],
                              lambda kk=key: a.on_tweak(kk)))
            self._knob_grid(ed, items)
            row = tk.Frame(ed, bg=EDIT_BG); row.pack(anchor="w", pady=(8, 0))
            small_button(row, "↺ 恢復這個效果的預設", lambda: (a.reset_tweaks(p), self.open_editor("fx"))).pack(side="left")
        elif kind == "rv":
            i = idx
            v = a.rv[i]
            label(ed, "混響 %s　編輯器" % "ABC"[i], 12, CYAN, True).pack(anchor="w")
            label(ed, "混響和效果完全分開：所有效果共用，拉多少就是多少；深度拉到 0＝這層關掉。", 9, SUB, wraplength=330, justify="left").pack(anchor="w")
            row = tk.Frame(ed, bg=EDIT_BG); row.pack(anchor="w", pady=(6, 0))
            label(row, "類型：", 10).pack(side="left")
            cb = ttk.Combobox(row, textvariable=v["type"], values=["關"] + a.reverb_names, state="readonly", width=8)
            cb.pack(side="left")
            cb.bind("<<ComboboxSelected>>", lambda e: (a.on_reverb_type(i), self.open_editor("rv", i)))
            if v["type"].get() == "關":
                label(ed, "這層目前關著。選一個類型就會打開。", 10, SUB).pack(anchor="w", pady=8)
                return
            d = a.reverb_type_defaults(v["type"].get())
            cbk = lambda: a.apply()
            items = [
                ("mix", v["mix"], 0, 100, "深度（量）", "{:.0f}%", 30, 1, cbk),
                ("size", v["size"], 0, 100, "殘響長度", "{:.0f}%", d["size"], 1, cbk),
                ("bright", v["bright"], 0, 100, "明亮度", "{:.0f}%", d["bright"], 1, cbk),
                ("pre", v["pre"], 0, 100, "預延遲", "{:.0f} ms", d["pre"], 1, cbk),
                ("width", v["width"], 0, 100, "立體寬度", "{:.0f}%", d["width"], 1, cbk),
                ("lowcut", v["lowcut"], 0, 800, "低頻清除", lambda x: "關" if x < 20 else "{:.0f} Hz".format(x), d["lowcut"], 10, cbk),
            ]
            self._knob_grid(ed, items)
            label(ed, "立體寬度只有耳機聽得出來（直播 App 通常是單聲道）。", 9, SUB, wraplength=330, justify="left").pack(anchor="w", pady=(6, 0))
            row = tk.Frame(ed, bg=EDIT_BG); row.pack(anchor="w", pady=(6, 0))
            small_button(row, "↺ 恢復這個類型的預設", lambda: (a.on_reverb_type(i), self.open_editor("rv", i))).pack(side="left")
        elif kind == "deess":
            label(ed, "去齒音", 12, CYAN, True).pack(anchor="w")
            label(ed, "只在「嘶、ㄘ、ㄙ」這類刺耳的齒音出現時才壓下來，所有效果共用。", 9, SUB, wraplength=330, justify="left").pack(anchor="w")
            self._knob_grid(ed, [("deess", a.deess, 0, 100, "去齒音", "{:.0f}%", 40, 1, lambda: a.apply())])
        elif kind == "card":
            label(ed, "🎭 變聲", 12, CYAN, True).pack(anchor="w")
            label(ed, "專屬變聲的參數是固定的（不會被任何細調改到）：\n升 1.5 半音、350 Hz −6 dB、3.2 kHz −5 dB。", 10, TXT, justify="left", wraplength=330).pack(anchor="w", pady=6)
            small_button(ed, "開／關 變聲", a.toggle_card, size=10).pack(anchor="w")
        elif kind == "polish":
            label(ed, "✨ 人聲質感（CD 質感）", 12, CYAN, True).pack(anchor="w")
            label(ed, "所有效果共用。讓人聲在手機、耳機上都大聲、清楚、立體。預設都是 0，不會改到原本的聲音。", 9, SUB,
                  wraplength=330, justify="left").pack(anchor="w")
            cbk = lambda: a.apply()
            self._knob_grid(ed, [
                ("loud", a.polish["loud"], 0, 100, "響度", "{:.0f}%", 0, 1, cbk),
                ("clarity", a.polish["clarity"], 0, 100, "清晰度", "{:.0f}%", 0, 1, cbk),
                ("thick", a.polish["thick"], 0, 100, "厚度", "{:.0f}%", 0, 1, cbk),
                ("widen", a.polish["widen"], 0, 100, "加寬（耳機）", "{:.0f}%", 0, 1, cbk),
            ])
            label(ed, "響度：自動把音量穩住（小聲拉上來、大聲壓住）\n清晰度：去悶＋空氣感\n厚度：像多人疊唱一樣飽滿（直播聽得到）\n加寬：左右變寬、更貼耳（只有耳機聽得出來）",
                  9, SUB, justify="left", wraplength=330).pack(anchor="w", pady=(6, 0))
            small_button(ed, "✨ 一鍵 CD 質感", a.apply_cd_template, size=10).pack(anchor="w", pady=(8, 0))
        elif kind == "align":
            label(ed, "⏱ 伴奏：對齊／避讓", 12, CYAN, True).pack(anchor="w")
            label(ed, "直播裡伴奏和人聲沒貼齊時調這裡（只影響直播和錄音，不影響你耳機裡聽到的）。\n"
                      "最準的方法：錄一段 → 播放 → 一邊聽一邊轉旋鈕，播放中就會即時改變。\n"
                      "人聲比伴奏慢（拖拍）→ 往右轉；人聲比伴奏快（搶拍）→ 往左轉。", 9, SUB, justify="left", wraplength=330).pack(anchor="w")
            self._knob_grid(ed, [("bgm_trim", a.bgm_trim, -150, 150, "伴奏延後", "{:+.0f} ms", 0, 1, lambda: a.on_trim()),
                                 ("duck", a.polish["duck"], 0, 100, "伴奏避讓", "{:.0f}%", 0, 1, lambda: a.apply())])
            label(ed, "伴奏避讓：唱歌時伴奏自動讓開一點點（最多 6 dB），人聲浮在伴奏上面、比較貼合。", 9, SUB,
                  wraplength=330, justify="left").pack(anchor="w", pady=(6, 0))
        self.refresh()

    # ---- 畫面更新
    def refresh(self):
        a = self.app
        for v in [a.g_in, a.g_out, a.g_bgm, a.g_mon] + [r["mix"] for r in a.rv]:
            fm = getattr(v, "_fmt2", None)
            if fm:
                try:
                    fm[0].config(text=fm[1].format(float(v.get())))
                except Exception:
                    pass
        if hasattr(self, "btn_ins_card"):
            on = bool(a.card.get())
            self.btn_ins_card.config(text="🎭 變聲：開" if on else "🎭 變聲：關", bg=PINK if on else "#3a3f46",
                                     activebackground=PINK if on else "#3a3f46", fg="#0d1013" if on else TXT)
            p = a.active_preset
            self.btn_ins_fx.config(text=("✨ " + a.preset_names[p].split(" ", 1)[-1]) if p else "✨ 效果：無")
            b = bool(a.bgm_on.get())
            self.btn_bgm2.config(text="🎵 伴奏：開" if b else "🔇 伴奏：關", bg=GREEN if b else "#3a3f46",
                                 activebackground=GREEN if b else "#3a3f46", fg="#0d1013" if b else TXT)

    def push_levels(self, lv, running):
        for k, m in self.meters.items():
            m.push(lv.get(k, 0.0) if running else 0.0)
