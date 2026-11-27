"""縮小版（迷你調音台）：很小的一個視窗，可以「置頂」永遠在最上面。

放的東西（照怕怕的需求）：
- 麥克風／耳機／直播／伴奏四條小聲波
- 可以拉視窗邊邊放大縮小（整個等比例放大，字、按鈕、聲波一起變）
- 試聽：原本／直播／外放
- 拉桿（每條前後都有 −／＋）：伴奏、耳機伴奏、耳機混響、整體混響
- 效果：無效果、說話、修音、電音、電話、答錄機、機器人
- 一鍵：5 個（縮小版自己選要哪 5 個，跟完整版的一鍵按鈕分開）
- 錄音：小圓鈕，按一下開始、再按一下停止
"""
import json
import tkinter as tk
from tkinter import ttk

MINI_FONT = 8
MINI_FX = [("無", None), ("說話", "talk"), ("修音", "sing"), ("電音", "electro"),
           ("電話", "phone"), ("答錄", "machine"), ("機器", "robot")]
MINI_OC_DEFAULT = ["CD", "流行", "甜歌", "抒情", "古風"]
MINI_OC_SLOTS = 5
MINI_SCALE_MIN, MINI_SCALE_MAX = 0.8, 3.0


class MiniWindow:
    def __init__(self, app):
        import sys
        self.A = sys.modules[type(app).__module__]      # app.py 的常數和 Wave（打包後 app.py 是 __main__）
        self.app = app
        self.win = None
        self.fx_btns = {}
        self.oc_btns = {}
        self.lis_btns = []
        self._resize_job = None
        self._base = None          # 放大 1 倍時的大小（寬, 高）
        self._sizes_set = []       # 程式自己設過的大小（這些不是「怕怕拉的」，不用重排）

    # ---- 等比例放大縮小
    @property
    def k(self):
        try:
            return max(MINI_SCALE_MIN, min(MINI_SCALE_MAX, float(self.app.mini_scale.get())))
        except Exception:
            return 1.0

    def px(self, n):
        return max(1, int(round(n * self.k)))

    def font(self, bold=False):
        f = (self.A.FONT, max(7, int(round(MINI_FONT * self.k))))
        return f + ("bold",) if bold else f

    def _on_configure(self, e):
        if e.widget is not self.win or self._base is None:
            return
        if self._resize_job:
            self.win.after_cancel(self._resize_job)
        self._resize_job = self.win.after(250, self._resize_done)   # 放開滑鼠後才重新排（拉的時候不會一直閃）

    def _resize_done(self):
        self._resize_job = None
        if not self.shown or self._base is None:
            return
        w, h = self.win.winfo_width(), self.win.winfo_height()
        if any(abs(w - a) <= 2 and abs(h - b) <= 2 for a, b in self._sizes_set):
            return                 # 是程式自己調的大小（或視窗還沒跟上），不是拉邊邊
        bw, bh = self._base
        k0 = self.k
        sw, sh = w / max(1, bw), h / max(1, bh)
        k = sw if abs(sw - k0) >= abs(sh - k0) else sh                       # 看哪一邊拉得比較多
        k = max(MINI_SCALE_MIN, min(MINI_SCALE_MAX, k))
        if abs(k - k0) < 0.04:
            if abs(w - self.win.winfo_reqwidth()) > 2 or abs(h - self.win.winfo_reqheight()) > 2:
                self._fit()          # 拉一點點：彈回剛好的大小
            return
        self.set_scale(k)

    def set_scale(self, k):
        """整個縮小版等比例放大縮小（k＝倍數，1＝原本大小）。"""
        self.app.mini_scale.set(round(max(MINI_SCALE_MIN, min(MINI_SCALE_MAX, k)), 2))
        if not self.shown:
            return
        if self._resize_job:
            self.win.after_cancel(self._resize_job)
            self._resize_job = None
        for c in self.win.winfo_children():
            if not isinstance(c, tk.Toplevel):
                c.destroy()
        self._build()
        self.set_pin(bool(self.app.mini_pin.get()))
        self.refresh()
        self._fit()
        self.app.schedule_save()

    def _fit(self):
        """視窗大小剛好包住內容（保持比例）。"""
        import re
        w = self.win
        w.update_idletasks()
        try:
            m = re.match(r"\d+x\d+([+-]-?\d+[+-]-?\d+)", w.geometry())     # 位置不變（還沒顯示時 winfo_x 會是 0）
            rw, rh = w.winfo_reqwidth(), w.winfo_reqheight()
            self._sizes_set = [(w.winfo_width(), w.winfo_height()), (rw, rh)]   # 之前的大小（視窗可能還沒跟上）＋新的大小
            w.geometry("%dx%d%s" % (rw, rh, m.group(1) if m else ""))
        except Exception:
            pass

    # ---- 開／關
    @property
    def shown(self):
        return self.win is not None and self.win.winfo_exists()

    def show(self):
        a, A = self.app, self.A
        if self.shown:
            self.win.deiconify()
            return
        w = self.win = tk.Toplevel(a.root)
        w.title("PAPA")
        w.configure(bg=A.C_BG)
        w.resizable(True, True)          # 拉邊邊＝整個等比例放大縮小
        w.protocol("WM_DELETE_WINDOW", self.expand)       # 關掉縮小版＝回到完整版（不是關掉程式）
        try:
            w.iconbitmap(default="")
        except Exception:
            pass
        self._build()
        geo = a.mini_geo.get()
        if geo:
            try:
                w.geometry(geo)
            except Exception:
                pass
        self.set_pin(bool(a.mini_pin.get()))
        a.root.withdraw()
        self.refresh()
        w.update_idletasks()
        k = self.k
        self._base = (w.winfo_reqwidth() / k, w.winfo_reqheight() / k)
        self._fit()
        w.bind("<Configure>", self._on_configure)

    def expand(self):
        """回到完整版。"""
        a = self.app
        if self.shown:
            try:
                a.mini_geo.set("+%d+%d" % (self.win.winfo_x(), self.win.winfo_y()))
            except Exception:
                pass
            self.win.destroy()
        self.win = None
        a.root.deiconify()
        a.root.lift()

    def set_pin(self, on):
        self.app.mini_pin.set(1 if on else 0)
        if self.shown:
            self.win.attributes("-topmost", bool(on))
            self.btn_pin.config(bg=self.A.C_ACCENT if on else self.A.C_BTN, fg=self.A.C_DARKTXT if on else self.A.C_TXT)

    # ---- 小元件
    def _btn(self, parent, text, cmd, w=None, **kw):
        A = self.A
        b = tk.Button(parent, text=text, command=cmd, bg=A.C_BTN, fg=A.C_TXT, activebackground=A.C_ACCENT,
                      activeforeground=A.C_DARKTXT, relief="flat", bd=0, padx=1, pady=0, cursor="hand2",
                      font=self.font(), highlightthickness=0, **kw)
        if w:
            b.config(width=w)
        return b

    def _lbl(self, parent, text, **kw):
        A = self.A
        return tk.Label(parent, text=text, bg=A.C_BG, fg=A.C_SUB, font=self.font(), padx=0, pady=0, bd=0, **kw)

    def _build(self):
        a, A, w = self.app, self.A, self.win
        # 標題列：PAPA、錄音、置頂、展開
        tb = tk.Frame(w, bg=A.C_DEEP)
        tb.pack(fill="x")
        tk.Label(tb, text="PAPA", bg=A.C_DEEP, fg=A.C_TXT, font=self.font(True)).pack(side="left", padx=(3, 0))
        self.btn_expand = self._btn(tb, "⤢", self.expand, w=2)
        self.btn_expand.pack(side="right", padx=1, pady=1)
        self.btn_pin = self._btn(tb, "📌", lambda: self.set_pin(not a.mini_pin.get()), w=2)
        self.btn_pin.pack(side="right", padx=1, pady=1)
        self.rec_time = tk.Label(tb, text="", bg=A.C_DEEP, fg=A.C_PINK, font=self.font(), width=5)
        self.rec_time.pack(side="right")
        # 錄音：小圓鈕
        P = self.px
        self.rec_cv = tk.Canvas(tb, width=P(13), height=P(13), bg=A.C_DEEP, highlightthickness=0, cursor="hand2")
        self.rec_cv.pack(side="right", padx=1)
        self.rec_dot = self.rec_cv.create_oval(P(1), P(1), P(12), P(12), fill=A.C_BTN, outline="")
        self.rec_in = self.rec_cv.create_oval(P(4), P(4), P(9), P(9), fill=A.C_PINK, outline="")
        self.rec_cv.bind("<Button-1>", lambda e: a.toggle_rec())

        body = tk.Frame(w, bg=A.C_BG)
        body.pack(fill="x", padx=2, pady=(2, 1))
        left = tk.Frame(body, bg=A.C_BG)
        left.pack(side="left", anchor="n")
        self.waves = {}
        for k, name, col in (("inp", "麥", A.C_CYAN), ("mon", "耳", A.C_GREEN), ("out", "播", A.C_PINK), ("bgm", "伴", A.C_YELLOW)):
            r = tk.Frame(left, bg=A.C_BG)
            r.pack(fill="x", pady=0)
            lb = self._lbl(r, name, width=2)
            lb.config(font=(A.FONT, max(6, int(round(7 * self.k)))))     # 字小一點：四條聲波也不會讓視窗變高
            lb.pack(side="left")
            wv = A.Wave(r, col, w=P(66), h=P(8))
            wv.itemconfig(wv.line, width=1 if self.k < 1.6 else 2)
            wv.pack(side="left", pady=1)
            self.waves[k] = wv
        lr = tk.Frame(left, bg=A.C_BG)
        lr.pack(fill="x", pady=(1, 0))
        self.lis_btns = []
        for i, t in enumerate(("原本", "直播", "外放")):
            b = self._btn(lr, t, lambda m=i: a.set_listen(m))
            b.pack(side="left", padx=(0, 1))
            self.lis_btns.append(b)

        right = tk.Frame(body, bg=A.C_BG)
        right.pack(side="left", padx=(3, 0), anchor="n")
        self.vals = {}
        for name, var, lo, hi in (("伴奏", a.g_bgm, 0, 200), ("耳伴", a.g_monbgm, 50, 300),
                                  ("耳混", a.g_monrv, 0, 100), ("混響", a.g_rvall, 0, 200)):
            r = tk.Frame(right, bg=A.C_BG)
            r.pack(fill="x")
            self._lbl(r, name, width=3, anchor="w").pack(side="left")
            self._btn(r, "−", lambda v=var, lo=lo, hi=hi: self.nudge(v, -1, lo, hi), w=1).pack(side="left")
            ttk.Scale(r, from_=lo, to=hi, variable=var, orient="horizontal", length=P(62),
                      command=lambda _v: a.apply()).pack(side="left", padx=1)
            self._btn(r, "+", lambda v=var, lo=lo, hi=hi: self.nudge(v, +1, lo, hi), w=1).pack(side="left")
            vl = self._lbl(r, "", width=5, anchor="e")      # 放大時「100%」也不會擠到 ＋
            vl.pack(side="left")
            self.vals[name] = (var, vl)

        fx = tk.Frame(w, bg=A.C_BG)
        fx.pack(fill="x", padx=2, pady=(0, 1))
        self.fx_btns = {}
        for i, (t, key) in enumerate(MINI_FX):
            b = self._btn(fx, t, (lambda k=key: a.clear_fx()) if key is None else (lambda k=key: a.preset(k)))
            b.grid(row=0, column=i, sticky="ew", padx=(0, 1))
            fx.columnconfigure(i, weight=1)
            self.fx_btns[key] = b

        oc = tk.Frame(w, bg=A.C_BG)
        oc.pack(fill="x", padx=2, pady=(0, 2))
        self.oc_frame = tk.Frame(oc, bg=A.C_BG)
        self.oc_frame.pack(side="left", fill="x", expand=True)
        self._btn(oc, "⚙", self.choose_oc, w=2).pack(side="left")
        self._build_oc()

    def nudge(self, var, d, lo, hi):
        var.set(max(lo, min(hi, round(float(var.get())) + d)))
        self.app.apply()
        self.refresh()

    # ---- 一鍵（縮小版自己的 5 個）
    def oc_visible(self):
        try:
            allst = self.app.all_styles()
            v = [k for k in json.loads(self.app.mini_oc_var.get() or "[]") if k in allst]
        except Exception:
            v = []
        return (v or list(MINI_OC_DEFAULT))[:MINI_OC_SLOTS]

    def set_oc_visible(self, names):
        allst = self.app.all_styles()
        self.app.mini_oc_var.set(json.dumps([k for k in names if k in allst][:MINI_OC_SLOTS], ensure_ascii=False))
        self.refresh_oc()

    def refresh_oc(self):
        if self.shown:
            self._build_oc()

    def _build_oc(self):
        for c in self.oc_frame.winfo_children():
            c.destroy()
        self.oc_btns = {}
        for i, k in enumerate(self.oc_visible()):
            b = self._btn(self.oc_frame, k[:2], lambda kk=k: self.app.apply_style(kk))
            b.grid(row=0, column=i, sticky="ew", padx=(0, 1))
            self.oc_frame.columnconfigure(i, weight=1)
            self.oc_btns[k] = b

    def choose_oc(self):
        """⚙：選縮小版要放哪 5 個一鍵（按一下＝加入／拿掉）。"""
        A = self.A
        m = tk.Toplevel(self.win)
        m.title("縮小版的一鍵")
        m.configure(bg=A.C_CARD)
        m.transient(self.win)
        if self.app.mini_pin.get():
            m.attributes("-topmost", True)
        tk.Label(m, text="按一下＝放上去／拿掉（最多 %d 個）" % MINI_OC_SLOTS, bg=A.C_CARD, fg=A.C_SUB,
                 font=self.font()).pack(anchor="w", padx=4, pady=(3, 1))
        g = tk.Frame(m, bg=A.C_CARD)
        g.pack(padx=4, pady=(0, 4))
        order = list(self.oc_visible())
        btns = {}

        def paint():
            for k, b in btns.items():
                b.config(bg=A.C_ACCENT if k in order else A.C_BTN, fg=A.C_DARKTXT if k in order else A.C_TXT)

        def tog(k):
            if k in order:
                order.remove(k)
            elif len(order) < MINI_OC_SLOTS:
                order.append(k)
            paint()
            self.set_oc_visible(order)
        for n, k in enumerate(self.app.all_styles()):
            b = self._btn(g, k, lambda kk=k: tog(kk))
            b.grid(row=n // 4, column=n % 4, sticky="ew", padx=1, pady=1)
            btns[k] = b
        paint()
        self.oc_dialog = m
        return m

    # ---- 每次 tick 更新（聲波、亮哪顆、數字、錄音）
    def refresh(self):
        if not self.shown:
            return
        a, A = self.app, self.A
        e = a.eng
        run = e.running
        for k, wv in self.waves.items():
            wv.push(e.lv[k] if run else 0.0)
        for name, (var, vl) in self.vals.items():
            t = "%d%%" % round(float(var.get()))
            if vl.cget("text") != t:
                vl.config(text=t)
        cur = a.active_preset
        for key, b in self.fx_btns.items():
            on = key == cur
            b.config(bg=A.C_ACCENT if on else A.C_BTN, fg=A.C_DARKTXT if on else A.C_TXT)
        for i, b in enumerate(self.lis_btns):
            on = int(a.listen.get()) == i
            b.config(bg=A.C_ACCENT if on else A.C_BTN, fg=A.C_DARKTXT if on else A.C_TXT)
        st = getattr(a, "_style_now", None)
        for k, b in self.oc_btns.items():
            on = k == st and cur is not None
            b.config(bg=A.C_ACCENT if on else A.C_BTN, fg=A.C_DARKTXT if on else A.C_TXT)
        rec = bool(getattr(e, "recording", False))
        self.rec_cv.itemconfig(self.rec_dot, fill=A.C_PINK if rec else A.C_BTN)
        self.rec_cv.itemconfig(self.rec_in, fill=A.C_DARKTXT if rec else A.C_PINK)
        if rec:
            import time
            if not getattr(self, "_rec_t0", None):
                self._rec_t0 = time.monotonic()
            s = int(time.monotonic() - self._rec_t0)
            self.rec_time.config(text="%d:%02d" % (s // 60, s % 60))
        else:
            self._rec_t0 = None
            if self.rec_time.cget("text"):
                self.rec_time.config(text="")
