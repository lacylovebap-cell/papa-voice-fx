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


def label(parent, text, size=10, color=TXT, bold=False, **kw):
    return tk.Label(parent, text=text, bg=parent["bg"], fg=color, font=(FONT, size, "bold" if bold else "normal"), **kw)


def small_button(parent, text, cmd, color="#3a3f46", fg=TXT, size=9, **kw):
    return tk.Button(parent, text=text, command=cmd, bg=color, fg=fg, activebackground="#4a5058", activeforeground=fg,
                     relief="flat", font=(FONT, size, "bold"), cursor="hand2", bd=0, padx=6, pady=3,
                     highlightthickness=1, highlightbackground="#121417", **kw)


# ---------------------------------------------------------------- 細調頁
class MixerPage(tk.Frame):
    """細調頁：左邊選要調的東西，右邊出現旋鈕。只放主畫面沒有的細節，不重複。"""

    SECTIONS = (("fx", None), ("rv", 0), ("rv", 1), ("rv", 2), ("polish", None))

    def __init__(self, parent, app):
        super().__init__(parent, bg=BG, padx=10, pady=10)
        self.app = app
        self.knobs = {}
        self.editing = None
        self.dirty = False
        side = tk.Frame(self, bg=PANEL, width=190, highlightthickness=1, highlightbackground=LINE)
        side.pack(side="left", fill="y")
        side.pack_propagate(False)
        label(side, "要調哪一個？", 9, SUB).pack(anchor="w", padx=10, pady=(10, 4))
        self.nav = {}
        for key in self.SECTIONS:
            b = tk.Button(side, text="", anchor="w", justify="left", command=lambda k=key: self.open_editor(*k),
                          bg=PANEL, fg=TXT, activebackground=STRIP_HI, activeforeground=TXT, relief="flat", bd=0,
                          font=(FONT, 9, "bold"), cursor="hand2", padx=10, pady=6)
            b.pack(fill="x", padx=4, pady=1)
            self.nav[key] = b
        label(side, "旋鈕：上下拖曳調整\n雙擊＝恢復預設\n按住 Shift＝更細", 8, SUB, justify="left").pack(anchor="w", padx=10, pady=(14, 0))
        self.editor = tk.Frame(self, bg=EDIT_BG, padx=16, pady=12, highlightthickness=1, highlightbackground=LINE)
        self.editor.pack(side="left", fill="both", expand=True, padx=(8, 0))
        self.open_editor("fx")

    # ---- 編輯器
    def clear_editor(self):
        for w in self.editor.winfo_children():
            w.destroy()
        self.knobs = {}

    def _knob_grid(self, parent, items, cols=5):
        """items: [(key, var, lo, hi, label, fmt, default, step, callback)]"""
        g = tk.Frame(parent, bg=EDIT_BG)
        g.pack(anchor="w", pady=(8, 0))
        for n, (key, var, lo, hi, lab, fmt, dflt, step, cb) in enumerate(items):
            k = Knob(g, var, lo, hi, lab, fmt, dflt, cb, step=step, size=66)
            k.grid(row=n // cols, column=n % cols, padx=4, pady=2)
            self.knobs[key] = k
        return g

    def _title(self, text, note):
        label(self.editor, text, 11, CYAN, True).pack(anchor="w")
        if note:
            label(self.editor, note, 9, SUB, wraplength=560, justify="left").pack(anchor="w", pady=(2, 0))

    def open_editor(self, kind, idx=None):
        a = self.app
        if kind not in ("fx", "rv", "polish"):
            kind, idx = "fx", None
        self.editing = (kind, idx)
        self.clear_editor()
        ed = self.editor
        if kind == "fx":
            p = a.active_preset
            if p is None:
                self._title("效果細調", "現在沒有開效果。先點一個效果，這裡就會出現它的旋鈕：")
                row = tk.Frame(ed, bg=EDIT_BG); row.pack(anchor="w", pady=8)
                for k, txt in a.preset_names.items():
                    small_button(row, txt, lambda kk=k: (a.preset(kk), self.open_editor("fx"))).pack(side="left", padx=2)
            else:
                self._title("效果細調　" + a.preset_names[p], "只影響「%s」，會自動記住，切到別的效果再切回來還在。" % a.preset_names[p])
                if p in ("electro", "sing"):
                    row = tk.Frame(ed, bg=EDIT_BG); row.pack(anchor="w", pady=(8, 0))
                    label(row, "套用參考預設：", 9).pack(side="left")
                    cb = ttk.Combobox(row, values=a.ref_presets(p), state="readonly", width=22)
                    cb.pack(side="left")
                    cb.bind("<<ComboboxSelected>>", lambda e: (a.apply_ref_preset(p, cb.get()), self.open_editor("fx")))
                    self.ref_combo = cb
                items = []
                for key in a.edit_keys(p):
                    spec = a.param_spec[key]
                    items.append((key, a.fxv[key], spec[1], spec[2], spec[0], spec[3], a.fx_default(p, key), spec[4],
                                  lambda kk=key: a.on_tweak(kk)))
                self._knob_grid(ed, items)
                small_button(ed, "↺ 恢復這個效果的預設", lambda: (a.reset_tweaks(p), self.open_editor("fx"))).pack(anchor="w", pady=(10, 0))
        elif kind == "rv":
            i = idx
            v = a.rv[i]
            self._title("混響 %s" % "ABC"[i], "所有效果共用，按效果不會改變。深度 0＝這層關掉。")
            row = tk.Frame(ed, bg=EDIT_BG); row.pack(anchor="w", pady=(8, 0))
            label(row, "類型：", 9).pack(side="left")
            cb = ttk.Combobox(row, textvariable=v["type"], values=["關"] + a.reverb_names, state="readonly", width=12)
            cb.pack(side="left")
            cb.bind("<<ComboboxSelected>>", lambda e: (a.on_reverb_type(i), self.open_editor("rv", i)))
            if v["type"].get() == "關":
                label(ed, "這層目前關著，選一個類型就會打開。", 9, SUB).pack(anchor="w", pady=8)
            else:
                d = a.reverb_type_defaults(v["type"].get())
                cbk = lambda: a.apply()
                self._knob_grid(ed, [
                    ("mix", v["mix"], 0, 100, "深度", "{:.0f}%", 30, 1, cbk),
                    ("size", v["size"], 0, 100, "殘響長度", "{:.0f}%", d["size"], 1, cbk),
                    ("bright", v["bright"], 0, 100, "明亮度", "{:.0f}%", d["bright"], 1, cbk),
                    ("pre", v["pre"], 0, 100, "預延遲", "{:.0f} ms", d["pre"], 1, cbk),
                    ("width", v["width"], 0, 100, "立體寬度", "{:.0f}%", d["width"], 1, cbk),
                    ("lowcut", v["lowcut"], 0, 800, "低頻清除", lambda x: "關" if x < 20 else "{:.0f} Hz".format(x), d["lowcut"], 10, cbk),
                ], cols=6)
                label(ed, "立體寬度只有耳機聽得出來（直播 App 通常是單聲道）。", 8, SUB).pack(anchor="w", pady=(6, 0))
                small_button(ed, "↺ 恢復這個類型的預設", lambda: (a.on_reverb_type(i), self.open_editor("rv", i))).pack(anchor="w", pady=(8, 0))
        elif kind == "polish":
            self._title("人聲質感", ("現在調的是「無效果」自己的一組。" if a.active_preset is None else "現在調的是效果共用的一組（無效果另外一組）。") + "都是 0＝不改變原本的聲音。降噪所有效果共用。")
            cbk = lambda: a.apply()
            self._knob_grid(ed, [
                ("loud", a.polish["loud"], 0, 100, "響度", "{:.0f}%", 0, 1, cbk),
                ("clarity", a.polish["clarity"], 0, 100, "清晰度", "{:.0f}%", 0, 1, cbk),
                ("thick", a.polish["thick"], 0, 100, "厚度", "{:.0f}%", 0, 1, cbk),
                ("widen", a.polish["widen"], 0, 100, "加寬（耳機）", "{:.0f}%", 0, 1, cbk),
                ("deess", a.deess, 0, 100, "去齒音", "{:.0f}%", 40, 1, cbk),
                ("gate", a.gate, 0, 100, "降噪", "{:.0f}%", 0, 1, cbk),
            ])
            label(ed, "響度＝自動把音量穩住　清晰度＝去悶＋空氣感　厚度＝更飽滿　加寬＝左右變寬（耳機）　去齒音＝壓住嘶嘶聲",
                  8, SUB, wraplength=560, justify="left").pack(anchor="w", pady=(6, 0))
            small_button(ed, "✨ 一鍵 CD 質感", a.apply_cd_template, size=9).pack(anchor="w", pady=(10, 0))
        self.refresh()

    # ---- 畫面更新
    def refresh(self):
        a = self.app
        if not hasattr(self, "nav"):
            return
        p = a.active_preset
        texts = {("fx", None): "✨ 效果細調　" + (a.preset_names[p].split(" ", 1)[-1] if p else "（無）"),
                 ("polish", None): "🎤 人聲質感"}
        for i in range(3):
            t = a.rv[i]["type"].get()
            texts[("rv", i)] = "🌫 混響 %s　%s" % ("ABC"[i], "關" if t == "關" else "%s %.0f%%" % (t, float(a.rv[i]["mix"].get())))
        for key, b in self.nav.items():
            on = key == self.editing
            b.config(text=texts[key], bg=CYAN if on else PANEL, fg="white" if on else TXT,
                     activebackground=CYAN if on else STRIP_HI)
