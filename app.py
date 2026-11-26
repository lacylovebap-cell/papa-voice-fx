"""PAPA 調音台 - 桌面版（Windows）
執行：python app.py    打包：build.bat
"""
import collections
import datetime
import json
import os
import traceback
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

import os as _os
try:
    if _os.environ.get("PAPA_FORCE_PY"):
        raise ImportError("強制使用 Python 引擎")
    import native_engine as engine          # C++ 底層引擎（延遲最低）
    NATIVE = True
except Exception:
    import engine                           # 相容用的 Python 引擎
    NATIVE = False
from dsp import note_name, KEY_NAMES, VOICE_COLORS, REVERB_TYPES, REVERB_NAMES
import mixer

APP = "PaPaVoiceFx"
NONE = "（不使用）"

# 效果：再按一次同一顆＝關閉。聲卡變音是獨立的鎖，不受效果影響。
PRESET_NAMES = {"talk": "🗣 說話", "sing": "🎤 自然修音", "electro": "⚡ 電音", "phone": "📞 電話", "machine": "📼 答錄機", "robot": "🤖 機器人"}
HOTKEYS = {"talk": "1", "sing": "2", "electro": "3", "phone": "4", "machine": "5", "robot": "7"}

# 電音風格（名稱: 修正速度 ms, Flex-Tune, Humanize, 雙重唱亮度, 金屬共鳴），由弱到強
EL_STYLES = {
    "微電": (10, 5, 10, 0, 0),
    "電音": (0, 0, 0, 0, 0),
    "強電": (0, 0, 0, 50, 50),
    "爆電（最強）": (0, 0, 0, 90, 100),
}
STYLE_KEYS = ("retune", "flex", "humanize", "electro_fx", "metal", "vibrato")
# Auto-Tune 參考預設（修正速度 ms, Flex-Tune, Humanize, 自然顫音 dB）
AT_REFS = {
    "自然修音（80／30／80）": (80, 30, 80, 0),
    "通用型（50／25／50）": (50, 25, 50, 0),
    "流行網感（35／25／80）": (35, 25, 80, 0),
    "R&B 絲滑（20／20／20）": (20, 20, 20, 0),
    "說唱電感（10／5／10）": (10, 5, 10, 0),
    "電鰻（0／0／0）": (0, 0, 0, 0),
}
# 自然修音（Auto-Tune「自然流行」：修正速度 60、Flex 20、Humanize 100）
NATURAL = (60, 20, 100)

# 效果的所有參數（都能在混音台用旋鈕細調）
FX_BASE = dict(mode="off", phone=0, machine=0, robot_fx=0, electro_fx=0, strength=100, retune=0, flex=0, humanize=0,
               vibrato=0.0, metal=0, robot_note=57, phone_dist=25, shift=0.0, comp=40, eq_lo=0, eq_mid=0, eq_hi=0)
NEUTRAL = dict(FX_BASE)


def _style_vals(st):
    r, f, h, e, m = EL_STYLES.get(st, EL_STYLES["電音"])
    return dict(retune=r, flex=f, humanize=h, electro_fx=e, metal=m, vibrato=0.0)


PRESETS = {
    "talk": {**FX_BASE, "comp": 45, "eq_mid": 2, "eq_hi": 2},
    "sing": {**FX_BASE, "mode": "natural", "retune": NATURAL[0], "flex": NATURAL[1], "humanize": NATURAL[2],
             "eq_lo": 1, "eq_mid": 1, "eq_hi": 2},
    "electro": {**FX_BASE, "mode": "electro", "comp": 50, "eq_mid": 1, "eq_hi": 2, **_style_vals("電音")},
    "phone": {**FX_BASE, "phone": 1, "comp": 60},
    "machine": {**FX_BASE, "machine": 1, "comp": 60},
    # 機器人：音高固定（不跟著唱）＋環形調變＋金屬共鳴
    "robot": {**FX_BASE, "mode": "robot", "robot_fx": 100, "metal": 60, "comp": 55, "eq_mid": 2, "eq_hi": 1},
}

# 參數：名稱、最小、最大、顯示格式、一格的大小
PARAM_SPEC = {
    "retune": ("修正速度", 0, 400, "{:.0f} ms", 1),
    "flex": ("Flex-Tune", 0, 100, "{:.0f}", 1),
    "humanize": ("Humanize", 0, 100, "{:.0f}", 1),
    "vibrato": ("自然顫音", -12, 12, "{:+.1f} dB", 0.5),
    "strength": ("修正強度", 0, 100, "{:.0f}%", 1),
    "electro_fx": ("雙重唱亮度", 0, 100, "{:.0f}%", 1),
    "metal": ("金屬共鳴", 0, 100, "{:.0f}%", 1),
    "robot_fx": ("機械感", 0, 100, "{:.0f}%", 1),
    "robot_note": ("機器人音高", 45, 72, lambda v: note_name(v), 1),
    "phone_dist": ("破音感", 0, 100, "{:.0f}%", 1),
    "shift": ("升降調", -12, 12, "{:+.1f} 半音", 0.5),
    "comp": ("壓縮", 0, 100, "{:.0f}%", 1),
    "eq_lo": ("低音", -12, 12, "{:+.1f} dB", 0.5),
    "eq_mid": ("中音", -12, 12, "{:+.1f} dB", 0.5),
    "eq_hi": ("高音", -12, 12, "{:+.1f} dB", 0.5),
}
_COMMON = ["shift", "comp", "eq_lo", "eq_mid", "eq_hi"]
EDIT_KEYS = {
    "talk": _COMMON,
    "sing": ["retune", "flex", "humanize", "vibrato", "strength"] + _COMMON,
    "electro": ["retune", "flex", "humanize", "vibrato", "electro_fx", "metal"] + _COMMON,
    "phone": ["phone_dist"] + _COMMON,
    "machine": _COMMON,
    "robot": ["robot_note", "robot_fx", "metal", "comp", "eq_lo", "eq_mid", "eq_hi"],
}
RV_FIELDS = ("type", "mix", "size", "bright", "pre", "width", "lowcut")
# 人聲質感（CD 質感）：全部效果共用，預設關閉
POLISH = ("loud", "clarity", "thick", "widen", "duck")
POLISH_NAMES = {"loud": "響度", "clarity": "清晰度", "thick": "厚度", "widen": "加寬（耳機）", "duck": "伴奏避讓"}
CD_TEMPLATE = dict(loud=50, clarity=40, thick=25, widen=60, duck=30)

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

# 配色：Studio One 風格的石墨灰＋藍色重點色
C_BG, C_CARD, C_TXT, C_SUB = "#1b1e22", "#262a2f", "#e6e9ed", "#8e959d"
C_PINK, C_GREEN, C_YELLOW, C_CYAN = "#f06c9b", "#5fd38d", "#f2c14e", "#4fc3f7"
C_BTN = "#363b42"
C_ACCENT = "#3ba4e0"      # Studio One 藍
C_DEEP = "#15181b"        # 最深的底（波形、清單）
C_LINE = "#3a3f46"        # 邊框
C_DARKTXT = "#0d1013"
FONT = "Microsoft JhengHei UI"


def data_dir():
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    d = os.path.join(base, APP)
    os.makedirs(d, exist_ok=True)
    return d


def rec_dir():
    d = os.path.join(os.path.expanduser("~"), "Documents", APP, "recordings")
    os.makedirs(d, exist_ok=True)
    return d


def write_wav(path, data):
    import wave
    import numpy as np
    pcm = (np.clip(np.asarray(data, np.float64), -1.0, 1.0) * 32767).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(48000)
        w.writeframes(pcm.tobytes())


def log_error(text):
    try:
        with open(os.path.join(data_dir(), "error.log"), "a", encoding="utf8") as f:
            f.write("[%s]\n%s\n" % (datetime.datetime.now().isoformat(timespec="seconds"), text))
    except Exception:
        pass


class Wave(tk.Canvas):
    """會跳動的聲音波形：最近約 3 秒的音量。整條用一條線畫，很省電腦資源（不會搶走處理聲音的時間）。"""

    def __init__(self, parent, color, w=210, h=34):
        super().__init__(parent, width=w, height=h, bg=C_DEEP, highlightthickness=0)
        self.w, self.h, self.color = w, h, color
        self.n = w // 3
        self.hist = collections.deque([0.0] * self.n, maxlen=self.n)
        mid = h / 2
        self.base = self.create_line(0, mid, w, mid, fill=C_LINE)
        self.line = self.create_line(*self._points(), fill=color, width=2)

    def _points(self):
        mid = self.h / 2
        pts = []
        for i, v in enumerate(self.hist):
            a = max(0.5, v ** 0.5 * (mid - 2))
            x = i * 3 + 1
            pts += [x, mid - a, x, mid + a, x + 1.5, mid]
        return pts

    def push(self, level):
        self.hist.append(min(1.0, max(0.0, level)))
        self.coords(self.line, *self._points())


class App:
    def __init__(self, root):
        self.root = root
        root.title("PAPA 調音台")
        root.configure(bg=C_BG)
        self.eng = engine.Engine()
        self.settings = self.load_settings()
        self.vars = {}
        self.in_map, self.out_map = {}, {}
        self._save_job = None
        self._play_which = None
        self._play_take = None
        self._replay_job = None
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
        # 專屬模式：開機自動開「變聲」並防誤關（怕怕專用）。舊版使用者（已有設定檔）自動沿用專屬模式。
        self.personal = self.var("personal", 1 if (self.settings and "personal" not in self.settings) else 0, tk.IntVar)
        self.card = self.var("card_general", 0, tk.IntVar, remember=not self.personal.get())
        if self.personal.get():
            self.card.set(1)
        self.ultra = self.var("ultra_v2", 0, tk.IntVar)
        self.alpha = self.var("alpha", 97, D)       # 視窗透明度（微透明）
        self.block_now = 128 if NATIVE else 256
        self._ur_hist = []
        self.bgm_trim = self.var("bgm_trim", 0, D)
        # 混響 A／B／C：全部效果共用（切換效果不會改變）
        self.rv = []
        old_v = self.settings.get("verb") if ("rv1_type" not in self.settings) else None
        for i in range(3):
            t0 = "經典" if i == 0 else "關"
            d = self.reverb_type_defaults("經典" if i == 0 else ("大廳" if i == 1 else "人聲板"))
            mix0 = 15 if i == 0 else 30
            if i == 0 and old_v is not None:        # 舊版的單一混響 → 第 1 層「經典」，聲音一樣
                mix0 = float(old_v)
                d["size"] = ((0.80 + 0.13 * mix0 / 100) - 0.75) / 0.22 * 100
            elif i == 0:
                d["size"] = ((0.80 + 0.13 * 0.15) - 0.75) / 0.22 * 100
            r = {"type": self.var("rv%d_type" % (i + 1), t0)}
            if r["type"].get() not in ["關"] + REVERB_NAMES:
                r["type"].set(t0)
            r["mix"] = self.var("rv%d_mix" % (i + 1), mix0, D)
            for k in ("size", "bright", "pre", "width", "lowcut"):
                r[k] = self.var("rv%d_%s" % (i + 1, k), d[k], D)
            self.rv.append(r)
        self.reverb_names = list(REVERB_NAMES)
        self.polish = {k: self.var("pol_" + k, 0, D) for k in POLISH}
        self.deess = self.var("deess", 40, D)
        self.g_dry = self.var("g_dry", 0, D)
        self.el_style = self.var("el_style3", "電音")
        if self.el_style.get() not in EL_STYLES:
            self.el_style.set("電音")
        self._style_applied = self.el_style.get()
        self.color = tk.StringVar(value="原本")   # 音色跟著效果走（不另外選）
        # 效果的即時參數（由效果按鈕＋細調決定）
        self.fxv = {}
        for k, v in NEUTRAL.items():
            var = self.var(k, v, tk.StringVar if k == "mode" else D, remember=False)
            self.fxv[k] = var
            setattr(self, k, var)
        # 每個效果的細調（只存和預設不同的），會記住
        self.fx_tweaks_var = self.var("fx_tweaks", "{}")
        try:
            self.fx_tweaks = json.loads(self.fx_tweaks_var.get()) or {}
        except Exception:
            self.fx_tweaks = {}
        # 我的設定（自己取名字的存檔）
        self.scenes_var = self.var("scenes", "{}")
        try:
            self.scenes = json.loads(self.scenes_var.get()) or {}
        except Exception:
            self.scenes = {}
        self.current_scene = None
        self.preset_names = PRESET_NAMES
        self.param_spec = PARAM_SPEC

    # ------------------------------------------------------------ 介面
    def label(self, parent, text, size=10, color=C_TXT, bold=False, **kw):
        return tk.Label(parent, text=text, bg=parent["bg"], fg=color, font=(FONT, size, "bold" if bold else "normal"), **kw)

    def button(self, parent, text, cmd, color=C_BTN, fg=C_TXT, size=11, **kw):
        b = tk.Button(parent, text=text, command=cmd, bg=color, fg=fg, activebackground=color, activeforeground=fg,
                      relief="flat", font=(FONT, size, "bold"), cursor="hand2", bd=0, padx=10, pady=6,
                      highlightthickness=1, highlightbackground=C_LINE, highlightcolor=C_LINE, **kw)
        return b

    def _build(self):
        st = ttk.Style()
        try:
            st.theme_use("clam")
        except Exception:
            pass
        # ---- 深色主題（分頁、拉桿、下拉選單、捲軸都改成石墨灰）
        st.configure(".", background=C_BG, foreground=C_TXT, bordercolor=C_LINE, lightcolor=C_CARD, darkcolor=C_DEEP,
                     troughcolor=C_DEEP, focuscolor=C_ACCENT, font=(FONT, 10))
        st.configure("TNotebook", background=C_DEEP, borderwidth=0, tabmargins=(8, 6, 8, 0))
        st.configure("TNotebook.Tab", background=C_CARD, foreground=C_SUB, padding=(18, 6), font=(FONT, 11, "bold"),
                     bordercolor=C_LINE, lightcolor=C_CARD)
        st.map("TNotebook.Tab", background=[("selected", C_BG)], foreground=[("selected", C_ACCENT)],
               lightcolor=[("selected", C_ACCENT)])
        for orient in ("Horizontal", "Vertical"):
            st.configure(orient + ".TScale", background="#5d646d", troughcolor=C_DEEP, bordercolor="#101215",
                         lightcolor="#7b838d", darkcolor="#3d434a", sliderthickness=14)
            st.map(orient + ".TScale", background=[("active", "#8a939d")])
        st.configure("TCombobox", fieldbackground=C_DEEP, background=C_BTN, foreground=C_TXT, arrowcolor=C_TXT,
                     bordercolor=C_LINE, lightcolor=C_LINE, darkcolor=C_LINE, selectbackground=C_DEEP, selectforeground=C_TXT)
        st.map("TCombobox", fieldbackground=[("readonly", C_DEEP), ("disabled", C_CARD)],
               foreground=[("disabled", "#5d646d")], background=[("active", "#454b53")])
        self.root.option_add("*TCombobox*Listbox.background", C_CARD)
        self.root.option_add("*TCombobox*Listbox.foreground", C_TXT)
        self.root.option_add("*TCombobox*Listbox.selectBackground", C_ACCENT)
        self.root.option_add("*TCombobox*Listbox.font", (FONT, 10))
        st.configure("Vertical.TScrollbar", background=C_BTN, troughcolor=C_DEEP, arrowcolor=C_SUB, bordercolor=C_DEEP,
                     lightcolor=C_BTN, darkcolor=C_BTN)
        # 微透明（進階設定可以調）
        try:
            self.root.attributes("-alpha", max(0.7, min(1.0, float(self.alpha.get()) / 100)))
        except Exception:
            pass
        # ---- 最上面：我的設定（自己取名字的存檔，一鍵切換）
        self.scene_bar = tk.Frame(self.root, bg=C_DEEP, padx=10, pady=6)
        self.scene_bar.pack(fill="x")
        # ---- 兩頁：簡單／混音台
        self.nb = ttk.Notebook(self.root)
        self.nb.pack(fill="both", expand=True)
        page1 = tk.Frame(self.nb, bg=C_BG)
        self.nb.add(page1, text="🏠 簡單")
        # 整個畫面可以用滑鼠滾輪上下捲動（螢幕比較矮時也看得到全部）
        outer = tk.Frame(page1, bg=C_BG)
        outer.pack(fill="both", expand=True)
        canvas = tk.Canvas(outer, bg=C_BG, highlightthickness=0)
        sb = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        wrap = tk.Frame(canvas, bg=C_BG, padx=14, pady=12)
        win = canvas.create_window((0, 0), window=wrap, anchor="nw")
        wrap.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(win, width=e.width))
        self.root.bind_all("<MouseWheel>", lambda e: canvas.yview_scroll(int(-e.delta / 120), "units"))
        self.canvas = canvas
        self.wrap = wrap
        # 視窗大小：不超過螢幕高度
        sh = self.root.winfo_screenheight()
        self.root.geometry("1040x%d" % max(500, min(920, sh - 80)))

        # ---- 頂部：開始＋聲卡變音
        top = tk.Frame(wrap, bg=C_BG)
        top.pack(fill="x")
        self.btn_start = self.button(top, "▶ 開始", self.toggle, color=C_ACCENT, fg="white", size=16, width=8)
        self.btn_start.pack(side="left")
        self.btn_card = self.button(top, "", self.toggle_card, size=13)
        self.btn_card.pack(side="left", padx=10, fill="x", expand=True)
        self.msg = self.label(wrap, "戴好耳機，按「開始」。", 10, C_SUB, anchor="w")
        self.msg.pack(fill="x", pady=(6, 6))

        # ---- 狀態面板：一眼看懂現在的聲音
        sp = tk.Frame(wrap, bg=C_DEEP, padx=14, pady=10, highlightthickness=1, highlightbackground=C_LINE)
        sp.pack(fill="x", pady=(0, 8))
        r1 = tk.Frame(sp, bg=C_DEEP); r1.pack(fill="x")
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
        self.label(box, "聲音管道（有聲音時，右邊會跳動）", 11, C_ACCENT, True).grid(row=0, column=0, columnspan=5, sticky="w", pady=(0, 6))
        self.cb_in = self._row(box, 1, "🎙 麥克風", self.dev_in, self.g_in, 200, C_CYAN, "inp")
        self.cb_mon = self._row(box, 2, "🎧 耳機", self.dev_mon, self.g_mon, 150, C_GREEN, "mon")
        self.cb_out = self._row(box, 3, "📡 直播", self.dev_out, self.g_out, 150, C_PINK, "out")
        self.cb_bgm = self._row(box, 4, "🎵 伴奏", self.dev_bgm, self.g_bgm, 150, C_YELLOW, "bgm")
        bgmrow = tk.Frame(box, bg=C_CARD)
        bgmrow.grid(row=5, column=1, columnspan=4, sticky="w", pady=(6, 0))
        self.btn_bgm = self.button(bgmrow, "", self.toggle_bgm, size=10)
        self.btn_bgm.pack(side="left")
        self.button(bgmrow, "❓ 伴奏怎麼接", lambda: messagebox.showinfo("伴奏怎麼接", BGM_HELP), size=10).pack(side="left", padx=8)
        # 伴奏對齊：直播裡人聲和伴奏沒貼齊時調這裡
        al = tk.Frame(box, bg=C_CARD)
        al.grid(row=6, column=1, columnspan=4, sticky="w", pady=(6, 0))
        self.label(al, "⏱ 對齊", 10, C_TXT, True).pack(side="left")
        ttk.Scale(al, from_=-150, to=150, variable=self.bgm_trim, orient="horizontal", length=170,
                  command=lambda v: self.on_trim()).pack(side="left", padx=(6, 2))
        vl = self.label(al, "", 9, C_SUB, width=7)
        vl.pack(side="left")
        self.bgm_trim._fmt = (vl, "{:+.0f} ms")
        self.button(al, "↺", lambda: (self.bgm_trim.set(0), self.on_trim()), size=8).pack(side="left", padx=(0, 18))
        self.label(al, "🎚 避讓", 10, C_TXT, True).pack(side="left")
        ttk.Scale(al, from_=0, to=100, variable=self.polish["duck"], orient="horizontal", length=140,
                  command=lambda v: self.apply()).pack(side="left", padx=(6, 2))
        vl = self.label(al, "", 9, C_SUB, width=5)
        vl.pack(side="left")
        self.polish["duck"]._fmt = (vl, "{:.0f}%")
        self.label(box, "對齊：錄一段邊聽邊拉，人聲比伴奏慢＝往右、快＝往左。避讓：唱歌時伴奏自動讓開一點，人聲更貼合。",
                   9, C_SUB, anchor="w").grid(row=7, column=1, columnspan=4, sticky="w")

        # ---- 效果
        fx = tk.Frame(wrap, bg=C_CARD, padx=12, pady=10)
        fx.pack(fill="x", pady=10)
        self.label(fx, "效果（再按一次＝關掉）", 11, C_ACCENT, True).pack(anchor="w", pady=(0, 2))
        self.now_label = self.label(fx, "", 1, C_CARD)   # 保留給測試用（不顯示內容）
        row = tk.Frame(fx, bg=C_CARD)
        row.pack(fill="x")
        self.preset_btns = {}
        self.preset_hk = {}

        def fx_button(txt, cmd, hk):
            cell = tk.Frame(row, bg=C_CARD)
            cell.pack(side="left", expand=True, fill="x", padx=3)
            lab = self.label(cell, "Ctrl+Alt+" + hk, 8, C_SUB)
            lab.pack()
            b = self.button(cell, txt, cmd, size=10)
            b.config(pady=4, padx=6)
            b.pack(fill="x")
            return b, lab
        self.btn_none, self.none_hk = fx_button("✋ 無效果", self.clear_fx, "0")
        for key, txt in PRESET_NAMES.items():
            b, lab = fx_button(txt, lambda k=key: self.preset(k), HOTKEYS[key])
            self.preset_btns[key] = b
            self.preset_hk[key] = lab
        cell = tk.Frame(row, bg=C_CARD)
        cell.pack(side="left", padx=(8, 3))
        self.label(cell, "每個都能調", 8, C_SUB).pack()
        tb = self.button(cell, "🎛 細調", lambda: self.open_mixer("fx"), color="#2c4a63", size=10)
        tb.config(pady=4, padx=6)
        tb.pack(fill="x")
        # 混響 A／B／C、去齒音：和效果完全分開，切換任何效果都不會跑掉，按效果也不會自己加混響
        fixed = tk.Frame(fx, bg=C_CARD)
        fixed.pack(fill="x", pady=(10, 0))
        for i in range(3):
            self.label(fixed, "🌫 混響 " + "ABC"[i], 11, C_TXT, True).grid(row=i, column=0, sticky="w", pady=1)
            cb = ttk.Combobox(fixed, textvariable=self.rv[i]["type"], values=["關"] + REVERB_NAMES, state="readonly", width=7)
            cb.grid(row=i, column=1, padx=6)
            cb.bind("<<ComboboxSelected>>", lambda e, k=i: self.on_reverb_type(k))
            self.label(fixed, "深度", 9, C_SUB).grid(row=i, column=2)
            ttk.Scale(fixed, from_=0, to=100, variable=self.rv[i]["mix"], orient="horizontal", length=170,
                      command=lambda _v: self.apply()).grid(row=i, column=3, padx=4)
            vl = self.label(fixed, "", 10, C_SUB, width=5)
            vl.grid(row=i, column=4)
            self.rv[i]["mix"]._fmt = (vl, "{:.0f}%")
            self.button(fixed, "⚙", lambda k=i: self.open_mixer("rv", k), size=9).grid(row=i, column=5, padx=4)
        self.label(fixed, "🦷 去齒音", 11, C_TXT, True).grid(row=0, column=6, sticky="w", padx=(24, 6))
        ttk.Scale(fixed, from_=0, to=100, variable=self.deess, orient="horizontal", length=140,
                  command=lambda _v: self.apply()).grid(row=0, column=7)
        vl = self.label(fixed, "", 10, C_SUB, width=5)
        vl.grid(row=0, column=8)
        self.deess._fmt = (vl, "{:.0f}%")
        self.label(fx, "↑ 混響和去齒音所有效果共用：你拉多少就是多少，切換效果不會變；深度拉到 0＝沒有混響。⚙＝更多細調（殘響長度、明亮度、立體寬度…）",
                   9, C_SUB, anchor="w", justify="left", wraplength=900).pack(fill="x")
        pol = tk.Frame(fx, bg=C_CARD)
        pol.pack(fill="x", pady=(8, 0))
        self.label(pol, "✨ 人聲質感", 11, C_TXT, True).grid(row=0, column=0, sticky="w")
        for n, k in enumerate(("loud", "clarity", "thick")):
            self.label(pol, POLISH_NAMES[k], 9, C_SUB).grid(row=0, column=1 + n * 3, padx=(10, 2))
            ttk.Scale(pol, from_=0, to=100, variable=self.polish[k], orient="horizontal", length=120,
                      command=lambda _v: self.apply()).grid(row=0, column=2 + n * 3)
            vl = self.label(pol, "", 9, C_SUB, width=5)
            vl.grid(row=0, column=3 + n * 3)
            self.polish[k]._fmt = (vl, "{:.0f}%")
        self.button(pol, "✨ 一鍵 CD 質感", self.apply_cd_template, color="#2c4a63", size=10).grid(row=0, column=10, padx=(10, 4))
        self.button(pol, "⚙", lambda: self.open_mixer("polish"), size=9).grid(row=0, column=11)
        self.label(fx, "↑ 讓人聲在手機、耳機上都大聲清楚好聽：響度＝自動把音量穩住，清晰度＝更清楚有空氣感，厚度＝更飽滿（預設都是 0，不會改到你現在的聲音）",
                   9, C_SUB, anchor="w", justify="left", wraplength=900).pack(fill="x")
        el = tk.Frame(fx, bg=C_CARD)
        el.pack(fill="x", pady=(8, 0))
        self.label(el, "電音風格：", 10).pack(side="left")
        c1 = ttk.Combobox(el, textvariable=self.el_style, values=list(EL_STYLES), state="disabled", width=14)
        c1.pack(side="left")
        c1.bind("<<ComboboxSelected>>", lambda e: self.on_style())
        self.cb_style = c1
        self.style_hint = self.label(el, "（先按「⚡ 電音」才能選）", 9, C_SUB)
        self.style_hint.pack(side="left", padx=6)

        self.label(fx, "修音和電音都會跟著你唱的歌自動判斷調，不用選調。\n自然修音＝只把走音拉回來，保留顫音和滑音；電音＝電音人聲（風格由軟到硬可選）。\n兩個都已經包含人聲修飾的音色，不用另外調。",
                   9, C_SUB, justify="left", anchor="w").pack(fill="x", pady=(4, 0))
        self.note_label = self.label(fx, "", 1, C_CARD)

        # ---- 錄音
        rc = tk.Frame(wrap, bg=C_CARD, padx=12, pady=10)
        rc.pack(fill="x")
        self.label(rc, "錄音（每錄一段就先暫存在下面清單，想存再存）", 11, C_ACCENT, True).pack(anchor="w", pady=(0, 6))
        row = tk.Frame(rc, bg=C_CARD)
        row.pack(fill="x")
        self.btn_rec = self.button(row, "● 錄音", self.toggle_rec, size=12)
        self.btn_rec.pack(side="left", expand=True, fill="x", padx=3)
        self.btn_play_wet = self.button(row, "▶ 聽選的那段", lambda: self.play("wet"), size=12)
        self.btn_play_wet.pack(side="left", expand=True, fill="x", padx=3)
        self.button(row, "💾 存檔", self.save_rec, size=12).pack(side="left", expand=True, fill="x", padx=3)
        self.button(row, "🗑 刪除", self.delete_take, size=12).pack(side="left", expand=True, fill="x", padx=3)
        self.takes = []          # 暫存的錄音：每段 {name, wet, dry, secs}
        self._take_no = 0
        self.take_list = tk.Listbox(rc, height=4, bg=C_DEEP, fg=C_TXT, selectbackground=C_ACCENT,
                                    selectforeground="white", font=(FONT, 10), activestyle="none", bd=0,
                                    highlightthickness=0, exportselection=False)
        self.take_list.pack(fill="x", pady=(6, 0))
        self.take_list.bind("<Double-Button-1>", lambda e: self.play("wet"))
        self.rec_label = self.label(rc, "還沒錄音（雙擊清單裡的某一段也可以直接聽）", 10, C_SUB, anchor="w")
        self.rec_label.pack(fill="x", pady=(6, 0))

        # ---- 進階（預設收起來）
        self.adv_open = False
        self.btn_adv = self.button(wrap, "⚙ 進階設定 ▸", self.toggle_adv, color=C_BG, fg=C_SUB, size=10)
        self.btn_adv.pack(anchor="w", pady=(10, 0))
        self.adv = tk.Frame(wrap, bg=C_CARD, padx=12, pady=10)
        tk.Checkbutton(self.adv, text="超低延遲（有爆音就取消勾選）", variable=self.ultra, bg=C_CARD, fg=C_TXT,
                       selectcolor=C_DEEP, activebackground=C_CARD, font=(FONT, 10)).grid(row=0, column=0, columnspan=3, sticky="w")
        tk.Checkbutton(self.adv, text="專屬模式：開機自動開「變聲」，關掉前會再確認（怕怕專用，其他歌手不用勾）",
                       variable=self.personal, command=self.on_personal, bg=C_CARD, fg=C_TXT, selectcolor=C_DEEP,
                       activebackground=C_CARD, font=(FONT, 10)).grid(row=6, column=0, columnspan=3, sticky="w", pady=(8, 0))
        self._slider(self.adv, 3, "耳機混一點原聲（抓拍用）", self.g_dry, 0, 100, "{:.0f}%")
        self._slider(self.adv, 7, "視窗透明度（微透明）", self.alpha, 70, 100, "{:.0f}%")
        self.button(self.adv, "重新尋找裝置", lambda: self.refresh_devices(True), size=10).grid(row=4, column=0, sticky="w", pady=(8, 0))
        self.status = self.label(self.adv, "", 9, C_SUB, justify="left", anchor="w")
        self.status.grid(row=5, column=0, columnspan=3, sticky="w", pady=(6, 0))
        self.hint = self.label(wrap, "快捷鍵 Ctrl+Alt+：0 無效果　1 說話　2 修音　3 電音　4 電話　5 答錄機　7 機器人　6 變聲　B 伴奏　R 錄音　F1～F8 我的設定",
                               9, C_SUB, anchor="w")
        self.hint.pack(fill="x", pady=(8, 0))
        # ---- 第二頁：混音台
        self.mixer = mixer.MixerPage(self.nb, self)
        self.nb.add(self.mixer, text="🎛 細調")
        self.nb.bind("<<NotebookTabChanged>>", self._on_tab)
        self.refresh_scene_bar()

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
                self.block_now = 64 if NATIVE else 128
            elif self.block_now < (128 if NATIVE else 256):
                self.block_now = 128 if NATIVE else 256
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
        import time
        self._started_at = time.time()
        self.msg.config(text="運作中。對著麥克風出聲，看「麥克風」那一行有沒有跳動。")

    # ------------------------------------------------------------ 參數
    def apply(self):
        for v in self.vars.values():
            fm = getattr(v, "_fmt", None)
            if fm:
                fm[0].config(text=fm[1].format(v.get()))
        # 電音風格換了 → 載入那個風格的旋鈕值
        if self.active_preset == "electro" and self.el_style.get() != self._style_applied:
            self._load_style()
        g = lambda k: float(self.fxv[k].get())
        color = "人聲修飾" if self.active_preset in ("sing", "electro") else "原本"
        kw = dict(color=color, mode=self.fxv["mode"].get(), key=0, scale="major",
                  strength=g("strength") / 100, speed=g("retune") / 400, flex=g("flex") / 100,
                  humanize=g("humanize") / 100, vibrato=g("vibrato"), shift=g("shift"), robot=g("robot_note"),
                  metal=g("metal") / 100, robot_fx=g("robot_fx") / 100,
                  phone=bool(g("phone")), phone_dist=g("phone_dist") / 100, machine=bool(g("machine")),
                  eq_lo=g("eq_lo"), eq_mid=g("eq_mid"), eq_hi=g("eq_hi"), comp=g("comp") / 100,
                  card=bool(self.card.get()), electro_fx=g("electro_fx") / 100, deess=float(self.deess.get()) / 100)
        for k in POLISH:
            kw[k] = float(self.polish[k].get()) / 100
        for i, r in enumerate(self.rv):
            t = r["type"].get()
            n = "rv%d_" % (i + 1)
            kw[n + "type"] = (REVERB_NAMES.index(t) + 1) if t in REVERB_NAMES else 0
            kw[n + "mix"] = float(r["mix"].get()) / 100 if t in REVERB_NAMES else 0.0
            kw[n + "size"] = float(r["size"].get()) / 100
            kw[n + "bright"] = float(r["bright"].get()) / 100
            kw[n + "pre"] = float(r["pre"].get())
            kw[n + "width"] = float(r["width"].get()) / 100
            kw[n + "lowcut"] = float(r["lowcut"].get())
        self.eng.set_params(**kw)
        for name, v in (("inp", self.g_in), ("mon", self.g_mon), ("dry", self.g_dry), ("out", self.g_out), ("bgm", self.g_bgm)):
            self.eng.set_gain(name, float(v.get()) / 100)
        self.eng.bgm_on = bool(self.bgm_on.get())
        self.eng.bgm_trim_ms = float(self.bgm_trim.get())
        try:
            self.root.attributes("-alpha", max(0.7, min(1.0, float(self.alpha.get()) / 100)))
        except Exception:
            pass
        if hasattr(self, "btn_card"):
            self.refresh_buttons()
        if hasattr(self, "mixer"):
            self.mixer.refresh()
        self.schedule_save()

    # ---- 效果與細調
    def edit_keys(self, p):
        return EDIT_KEYS[p]

    def fx_default(self, p, key):
        d = dict(PRESETS[p])
        if p == "electro":
            d.update(_style_vals(self.el_style.get()))
        return d[key]

    def fx_values(self, p):
        d = dict(PRESETS[p])
        if p == "electro":
            d.update(_style_vals(self.el_style.get()))
        d.update(self.fx_tweaks.get(p, {}))
        return d

    def _store_tweaks(self):
        self.fx_tweaks_var.set(json.dumps(self.fx_tweaks, ensure_ascii=False))

    def on_tweak(self, key):
        """旋鈕轉動：記到目前效果的細調裡。"""
        p = self.active_preset
        if p is None:
            return
        v = float(self.fxv[key].get())
        tw = self.fx_tweaks.setdefault(p, {})
        if abs(v - float(self.fx_default(p, key))) < 1e-9:
            tw.pop(key, None)
        else:
            tw[key] = v
        if not tw:
            self.fx_tweaks.pop(p, None)
        self._store_tweaks()
        self.apply()

    def reset_tweaks(self, p):
        self.fx_tweaks.pop(p, None)
        self._store_tweaks()
        if self.active_preset == p:
            for k, v in self.fx_values(p).items():
                self.fxv[k].set(v)
        self.apply()

    def ref_presets(self, p):
        return (list(EL_STYLES) + list(AT_REFS)) if p == "electro" else list(AT_REFS)

    def apply_ref_preset(self, p, name):
        if name in EL_STYLES:
            vals = _style_vals(name)
        elif name in AT_REFS:
            r, f, h, vb = AT_REFS[name]
            vals = dict(retune=r, flex=f, humanize=h, vibrato=vb)
            if p == "electro":
                vals.update(electro_fx=0, metal=0)
        else:
            return
        tw = self.fx_tweaks.setdefault(p, {})
        for k, v in vals.items():
            if abs(v - float(self.fx_default(p, k))) < 1e-9:
                tw.pop(k, None)
            else:
                tw[k] = v
        if not tw:
            self.fx_tweaks.pop(p, None)
        self._store_tweaks()
        if self.active_preset == p:
            for k, v in self.fx_values(p).items():
                self.fxv[k].set(v)
        self.apply()

    def on_style(self):
        self._load_style()
        self.apply()

    def _load_style(self):
        """選了電音風格：風格相關的旋鈕回到該風格的值（EQ、壓縮的細調保留）。"""
        self._style_applied = self.el_style.get()
        tw = self.fx_tweaks.get("electro", {})
        for k in STYLE_KEYS:
            tw.pop(k, None)
        if not tw:
            self.fx_tweaks.pop("electro", None)
        self._store_tweaks()
        if self.active_preset == "electro":
            for k, v in self.fx_values("electro").items():
                self.fxv[k].set(v)
        self._mixer_fx_changed()

    def preset(self, name):
        """效果：再按一次同一顆＝關閉（聲卡變音不受影響）。混響不受效果影響。"""
        if self.active_preset == name:
            vals, self.active_preset = NEUTRAL, None
        else:
            vals, self.active_preset = self.fx_values(name), name
            if name == "electro":
                self._style_applied = self.el_style.get()
        for k, v in vals.items():
            self.fxv[k].set(v)
        self.apply()
        self._mixer_fx_changed()

    def clear_fx(self):
        for k, v in NEUTRAL.items():
            self.fxv[k].set(v)
        self.active_preset = None
        self.apply()
        self._mixer_fx_changed()

    def set_mode(self, val):
        self.mode.set("off" if self.mode.get() == val else val)
        self.active_preset = None
        self.apply()

    def _mixer_fx_changed(self):
        """效果換了：混音台正在看效果編輯器的話更新它（沒在看就等切過去再更新）。"""
        if not hasattr(self, "mixer") or not self.mixer.editing or self.mixer.editing[0] != "fx":
            return
        if str(self.nb.select()) == str(self.mixer):
            self.mixer.open_editor("fx")
        else:
            self.mixer.dirty = True

    def _on_tab(self, e=None):
        if hasattr(self, "mixer") and str(self.nb.select()) == str(self.mixer) and getattr(self.mixer, "dirty", False):
            self.mixer.dirty = False
            self.mixer.open_editor(*self.mixer.editing)

    def open_mixer(self, kind="fx", idx=None):
        self.nb.select(self.mixer)
        self.mixer.open_editor(kind, idx)

    # ---- 混響
    def reverb_type_defaults(self, name):
        sc, size, bright, pre, width, lowcut = REVERB_TYPES.get(name, REVERB_TYPES["經典"])
        return dict(size=size * 100, bright=bright * 100, pre=pre, width=width * 100, lowcut=lowcut)

    def on_reverb_type(self, i):
        """換混響類型：其他旋鈕回到這個類型的建議值；深度保留（原本是 0 就給 30%）。"""
        r = self.rv[i]
        t = r["type"].get()
        if t in REVERB_NAMES:
            for k, v in self.reverb_type_defaults(t).items():
                r[k].set(round(v, 1))
            if float(r["mix"].get()) <= 0.5:
                r["mix"].set(30)
        self.apply()
        if hasattr(self, "mixer") and self.mixer.editing == ("rv", i):
            self.mixer.open_editor("rv", i)

    # ---- 一鍵 CD 質感（參考 Studio One 混音教學：暗而柔的板式混響＋加寬＋響度）
    def apply_cd_template(self, ask=True):
        if ask and not messagebox.askyesno(
                "一鍵 CD 質感",
                "會把混響 A 換成「CD 絲滑板式」（深度 25%）、混響 B／C 歸零，\n"
                "並打開響度、清晰度、厚度、加寬、伴奏避讓。\n\n效果和變聲都不會動。要套用嗎？\n"
                "（建議先按「＋ 存目前設定」把現在的設定存起來）"):
            return
        r = self.rv[0]
        r["type"].set("CD 絲滑板式")
        for k, v in self.reverb_type_defaults("CD 絲滑板式").items():
            r[k].set(round(v, 1))
        r["mix"].set(25)
        for rr in self.rv[1:]:
            rr["mix"].set(0)
        for k, v in CD_TEMPLATE.items():
            self.polish[k].set(v)
        self.apply()
        if hasattr(self, "mixer") and self.mixer.editing and self.mixer.editing[0] in ("rv", "polish"):
            self.mixer.open_editor(*self.mixer.editing)
        self.msg.config(text="已套用 CD 質感。喜歡的話按最上面「＋ 存目前設定」存起來。", fg=C_SUB)

    # ---- 我的設定（自己取名字的存檔）
    def scene_snapshot(self):
        return dict(active=self.active_preset, el_style=self.el_style.get(),
                    tweaks=json.loads(json.dumps(self.fx_tweaks)),
                    reverb=[{k: r[k].get() for k in RV_FIELDS} for r in self.rv],
                    deess=float(self.deess.get()),
                    polish={k: float(self.polish[k].get()) for k in POLISH})

    def _store_scenes(self):
        self.scenes_var.set(json.dumps(self.scenes, ensure_ascii=False))
        self.schedule_save()

    def save_scene(self, name=None):
        if name is None:
            name = simpledialog.askstring("存成我的設定", "幫目前這組聲音取個名字（例如：怕怕專屬一、怕怕說唱）：\n"
                                          "會存：效果、所有細調、混響、去齒音（不會動到變聲開關和音量）", parent=self.root)
        if not name or not name.strip():
            return
        name = name.strip()[:20]
        if name in self.scenes and not messagebox.askyesno("已經有這個名字", "「%s」已經存在，要用目前的設定覆蓋嗎？" % name):
            return
        self.scenes[name] = self.scene_snapshot()
        self.current_scene = name
        self._store_scenes()
        self.refresh_scene_bar()
        self.msg.config(text="已存成「%s」，之後按上面的按鈕就能一鍵切回來。" % name, fg=C_SUB)

    def load_scene(self, name):
        s = self.scenes.get(name)
        if not s:
            return
        self.fx_tweaks = json.loads(json.dumps(s.get("tweaks", {})))
        self._store_tweaks()
        st = s.get("el_style", "電音")
        self.el_style.set(st if st in EL_STYLES else "電音")
        self._style_applied = self.el_style.get()
        for i, rr in enumerate(s.get("reverb", [])[:3]):
            for k in RV_FIELDS:
                if k in rr:
                    self.rv[i][k].set(rr[k])
        self.deess.set(s.get("deess", self.deess.get()))
        for k, v in s.get("polish", {}).items():
            if k in self.polish:
                self.polish[k].set(v)
        act = s.get("active")
        if act in PRESETS:
            self.active_preset = None
            self.preset(act)
        else:
            self.clear_fx()
        self.current_scene = name
        self.refresh_scene_bar()
        if hasattr(self, "mixer") and self.mixer.editing and self.mixer.editing[0] == "rv":
            self.mixer.open_editor("rv", self.mixer.editing[1])
        self.msg.config(text="已切換到「%s」。" % name, fg=C_SUB)

    def load_scene_index(self, i):
        names = list(self.scenes)
        if i < len(names):
            self.load_scene(names[i])

    def rename_scene(self, name):
        new = simpledialog.askstring("改名", "「%s」要改成什麼名字？" % name, initialvalue=name, parent=self.root)
        if not new or not new.strip() or new.strip() == name:
            return
        new = new.strip()[:20]
        if new in self.scenes:
            messagebox.showinfo("改名", "已經有「%s」了。" % new)
            return
        self.scenes = {(new if k == name else k): v for k, v in self.scenes.items()}
        if self.current_scene == name:
            self.current_scene = new
        self._store_scenes()
        self.refresh_scene_bar()

    def delete_scene(self, name):
        if not messagebox.askyesno("刪除", "確定要刪除「%s」嗎？" % name):
            return
        self.scenes.pop(name, None)
        if self.current_scene == name:
            self.current_scene = None
        self._store_scenes()
        self.refresh_scene_bar()

    def scene_menu(self, name, event):
        m = tk.Menu(self.root, tearoff=0)
        m.add_command(label="用目前的設定覆蓋「%s」" % name, command=lambda: self._overwrite_scene(name))
        m.add_command(label="改名", command=lambda: self.rename_scene(name))
        m.add_command(label="刪除", command=lambda: self.delete_scene(name))
        try:
            m.tk_popup(event.x_root, event.y_root)
        finally:
            m.grab_release()

    def _overwrite_scene(self, name):
        self.scenes[name] = self.scene_snapshot()
        self.current_scene = name
        self._store_scenes()
        self.refresh_scene_bar()

    def refresh_scene_bar(self):
        bar = self.scene_bar
        for w in bar.winfo_children():
            w.destroy()
        tk.Label(bar, text="💾 我的設定", bg=bar["bg"], fg=C_ACCENT, font=(FONT, 10, "bold")).pack(side="left", padx=(0, 8))
        self.scene_btns = {}
        for n, name in enumerate(self.scenes):
            on = name == self.current_scene
            c = C_ACCENT if on else C_BTN
            txt = ("F%d " % (n + 1) if n < 8 else "") + name
            b = tk.Button(bar, text=txt, command=lambda k=name: self.load_scene(k), bg=c, fg="white" if on else C_TXT,
                          activebackground=c, relief="flat", font=(FONT, 10, "bold"), cursor="hand2", bd=0, padx=8, pady=2)
            b.pack(side="left", padx=2)
            b.bind("<Button-3>", lambda e, k=name: self.scene_menu(k, e))
            self.scene_btns[name] = b
        tk.Button(bar, text="＋ 存目前設定", command=self.save_scene, bg="#2c4a63", fg=C_TXT, activebackground="#2c4a63",
                  relief="flat", font=(FONT, 10, "bold"), cursor="hand2", bd=0, padx=8, pady=2).pack(side="left", padx=(8, 4))
        if self.scenes:
            tk.Label(bar, text="（按一下＝切換；右鍵＝覆蓋／改名／刪除）", bg=bar["bg"], fg=C_SUB, font=(FONT, 9)).pack(side="left")
        else:
            tk.Label(bar, text="把現在的效果＋細調＋混響存起來、取名字，之後一鍵切換", bg=bar["bg"], fg=C_SUB, font=(FONT, 9)).pack(side="left")

    def on_personal(self):
        if self.personal.get():
            self.card.set(1)
        self.apply()

    def toggle_card(self):
        if self.card.get():
            if self.personal.get() and not messagebox.askyesno("關閉變聲", "關閉後直播會出現你的原音。\n確定要關閉嗎？"):
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
        c = C_PINK if on else (C_YELLOW if self.personal.get() else C_BTN)
        if self.personal.get():
            txt = "🔒 變聲：開著（專屬模式，按這裡關）" if on else "⚠ 變聲關了＝原音！（按這裡開）"
        else:
            txt = "🎭 變聲（升 1.5 半音）：開" if on else "🎭 變聲：關（原聲）"
        self.btn_card.config(text=txt,
                             bg=c, activebackground=c, fg=C_DARKTXT if on else (C_DARKTXT if self.personal.get() else C_TXT))
        b = bool(self.bgm_on.get())
        c = C_GREEN if b else C_BTN
        self.btn_bgm.config(text="🎵 伴奏：開" if b else "🔇 伴奏：關", bg=c, activebackground=c,
                            fg=C_DARKTXT if b else C_TXT)
        el_on = self.active_preset == "electro"
        self.cb_style.config(state="readonly" if el_on else "disabled")
        self.style_hint.config(text="" if el_on else "（先按「⚡ 電音」才能選）")
        none = self.active_preset is None
        c = C_ACCENT if none else C_BTN
        self.btn_none.config(bg=c, activebackground=c, fg="white" if none else C_TXT)
        voice = "變聲" if on else ("⚠ 原音" if self.personal.get() else "原聲")
        fxname = PRESET_NAMES[self.active_preset].split(" ", 1)[1] if self.active_preset else "無效果"
        extra = ""
        if self.active_preset == "electro":
            extra = "（%s）" % self.el_style.get()
        col = ""
        self.now_label.config(text="現在送出去的聲音 ＝ %s ＋ %s%s%s" % (voice, fxname, extra, col))
        mark = lambda ok: "✅" if ok else "⬜"
        kind = {"talk": "說話（只修飾音色，不修音）", "sing": "自然修音（拉準走音、保留顫音）",
                "electro": "電音人聲（%s）" % self.el_style.get(), "phone": "電話音", "machine": "答錄機音",
                "robot": "機器人聲"}
        self.st_funcs.config(text="目前功能：　%s 變聲　　%s %s　　%s 伴奏" % (
            mark(on), mark(self.active_preset is not None),
            kind.get(self.active_preset, "沒有修音、沒有電音（一般聲音）"), mark(bool(self.bgm_on.get()))),
            fg=C_TXT if (on or not self.personal.get()) else C_YELLOW)
        for k, btn in self.preset_btns.items():
            c = C_ACCENT if self.active_preset == k else C_BTN
            btn.config(bg=c, activebackground=c, fg="white" if self.active_preset == k else C_TXT)
        self.btn_play_wet.config(text="■ 停止" if self.eng.playing else "▶ 聽選的那段")

    # ------------------------------------------------------------ 錄音
    def toggle_rec(self):
        if not self.eng.running:
            messagebox.showinfo("錄音", "請先按「開始」。")
            return
        if self.eng.recording:
            secs = self.eng.stop_recording()
            self.btn_rec.config(text="● 錄音", bg=C_BTN, activebackground=C_BTN)
            wet, dry = self.eng.get_recording("wet"), self.eng.get_recording("dry")
            if wet is None or len(wet) == 0:
                self.rec_label.config(text="沒有錄到聲音")
                return
            voice, bgm = self.eng.get_recording("voice"), self.eng.get_recording("bgm")
            self._take_no += 1
            name = "第 %d 段　%s　%.1f 秒" % (self._take_no, datetime.datetime.now().strftime("%H:%M:%S"), secs)
            self.takes.append(dict(name=name, wet=wet, dry=dry, voice=voice, bgm=bgm, secs=secs,
                                   trim=float(self.bgm_trim.get())))
            if len(self.takes) > 30:                       # 最多暫存 30 段，太舊的自動移除
                self.takes.pop(0)
            self.refresh_takes(select=len(self.takes) - 1)
            self.rec_label.config(text="已暫存「%s」，可以再錄下一段" % name.split("　")[0])
        else:
            if self.eng.playing:
                self.eng.stop_play()
            self.eng.start_recording()
            self.btn_rec.config(text="■ 停止錄音", bg="#d9534f", activebackground="#d9534f")

    def refresh_takes(self, select=None):
        self.take_list.delete(0, "end")
        for t in self.takes:
            self.take_list.insert("end", t["name"])
        if select is not None and self.takes:
            self.take_list.selection_clear(0, "end")
            self.take_list.selection_set(select)
            self.take_list.see(select)

    def selected_take(self):
        sel = self.take_list.curselection()
        if sel:
            return sel[0]
        return len(self.takes) - 1 if self.takes else None

    def take_mix(self, t):
        """錄音的直播聲音：如果錄的時候人聲、伴奏有分開存，就照「目前」的伴奏對齊重新混（播放中拉對齊會即時改變）。"""
        import numpy as np
        v, b = t.get("voice"), t.get("bgm")
        if v is None or b is None or len(v) == 0 or len(v) != len(b):
            return t["wet"]
        d = int(round((float(self.bgm_trim.get()) - float(t.get("trim", 0.0))) * engine.SR / 1000.0))
        if d == 0:
            return t["wet"]
        n = len(b)
        if d > 0:
            b2 = np.concatenate((np.zeros(min(d, n), np.float32), b[:max(0, n - d)]))
        else:
            b2 = np.concatenate((b[min(-d, n):], np.zeros(min(-d, n), np.float32)))
        m = v.astype(np.float32) + b2.astype(np.float32)
        pk = float(np.max(np.abs(m))) if len(m) else 0.0
        return m / pk * 0.98 if pk > 0.98 else m

    def play(self, which="wet"):
        import time
        if self.eng.playing:
            self.eng.stop_play()
            self._play_which = None
            self._play_take = None
            self.refresh_buttons()
            return
        if self.eng.recording:
            self.toggle_rec()
        i = self.selected_take()
        if i is None:
            messagebox.showinfo("聽錄音", "還沒有錄音。")
            return
        data = self.take_mix(self.takes[i]) if which == "wet" else self.takes[i][which]
        if not self.eng.play_array(data):
            messagebox.showinfo("聽錄音", "請先按「開始」才能在耳機播放。")
            return
        self._play_which = which
        self._play_take = (i, time.time(), 0) if which == "wet" else None
        self.rec_label.config(text="播放中：" + self.takes[i]["name"] + "（可以邊聽邊拉「伴奏對齊」）")
        self.refresh_buttons()

    def on_trim(self):
        self.apply()
        if getattr(self, "_play_take", None) is not None and self.eng.playing:
            if getattr(self, "_replay_job", None):
                self.root.after_cancel(self._replay_job)
            self._replay_job = self.root.after(150, self._replay_from_pos)

    def _replay_from_pos(self):
        """播放中改了對齊：從目前播到的位置，用新的對齊繼續播。"""
        import time
        self._replay_job = None
        pt = getattr(self, "_play_take", None)
        if pt is None or not self.eng.playing:
            return
        i, t0, off = pt
        if i >= len(self.takes):
            return
        pos = off + int((time.time() - t0) * engine.SR)
        mix = self.take_mix(self.takes[i])
        if pos < len(mix) - 480:
            self.eng.play_array(mix[pos:])
            self._play_take = (i, time.time(), pos)

    def delete_take(self):
        i = self.selected_take()
        if i is None:
            return
        if self.eng.playing:
            self.eng.stop_play()
        name = self.takes.pop(i)["name"]
        self.refresh_takes(select=min(i, len(self.takes) - 1) if self.takes else None)
        self.rec_label.config(text="已刪除「%s」" % name.split("　")[0])

    def save_rec(self):
        i = self.selected_take()
        if i is None:
            messagebox.showinfo("存檔", "還沒有錄音。")
            return
        t = self.takes[i]
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        d = rec_dir()
        try:
            write_wav(os.path.join(d, "%s_第%d段_處理後.wav" % (stamp, i + 1)), self.take_mix(t))
            write_wav(os.path.join(d, "%s_第%d段_原聲.wav" % (stamp, i + 1)), t["dry"])
        except Exception as e:
            log_error(traceback.format_exc())
            messagebox.showerror("存檔失敗", str(e))
            return
        self.rec_label.config(text="「%s」已存到：%s" % (t["name"].split("　")[0], d))
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

        hk = {"<ctrl>+<alt>+" + HOTKEYS[k]: ui(self.preset, k) for k in PRESET_NAMES}
        hk.update({"<ctrl>+<alt>+0": ui(self.clear_fx), "<ctrl>+<alt>+6": ui(self.toggle_card),
                   "<ctrl>+<alt>+b": ui(self.toggle_bgm), "<ctrl>+<alt>+r": ui(self.toggle_rec)})
        for n in range(8):      # F1～F8：我的設定
            hk["<ctrl>+<alt>+<f%d>" % (n + 1)] = ui(self.load_scene_index, n)
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
        try:
            if str(self.nb.select()) == str(self.mixer):
                self.mixer.push_levels(e.lv, run)
        except Exception:
            pass
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
            mode = ("C++ 底層引擎・" if NATIVE else "Python 引擎・") + ("收音＋耳機同一條線（最即時）" if e.duplex else "收音、耳機分開（相容模式）")
            self.status.config(text="%s\n耳機聽自己的延遲：約 %.0f ms\n直播那條自動緩衝：%.0f ms（不影響耳機）\n耳機爆音次數：%d%s"
                               % (mode, e.latency_ms, e.cable_target / engine.SR * 1000, e.underruns, bmsg))
            self.auto_protect()
            if getattr(e, "last_error", None):
                self.msg.config(text="⚠ 處理出錯，聲音沒有送出去！請截圖給 Claude：" + str(e.last_error)[:80], fg="#ff7b72")
            elif e.in_clip > 0.5:
                self.msg.config(text="⚠ 麥克風進來就太大聲（破音）！請把聲卡上的麥克風旋鈕轉小一點，或把「麥克風」音量拉小。",
                                fg="#ff7b72")
            elif self.msg.cget("fg") == "#ff7b72":
                self.msg.config(text="運作中。", fg=C_SUB)
            if e.recording:
                self.rec_label.config(text="錄音中… %.1f 秒" % (e.rec_samples / engine.SR))
            if not e.playing and self._play_which is not None:
                self._play_which = None
                self._play_take = None
                self.refresh_buttons()
        self.root.after(60, self.tick)

    def auto_protect(self):
        """偵測到爆音就自動加大緩衝重新連線：128 → 256 → 512（最多到 512，避免延遲太大）。"""
        import time
        now = time.time()
        if now - getattr(self, "_started_at", 0) < 4.0:     # 剛開始的幾秒一定會有短暫不穩，不算
            self._ur_hist = []
            return
        self._ur_hist.append((now, self.eng.underruns))
        self._ur_hist = [(t, u) for t, u in self._ur_hist if now - t < 3.0]
        if len(self._ur_hist) < 2:
            return
        grew = self._ur_hist[-1][1] - self._ur_hist[0][1]
        if grew >= 4 and self.block_now < 512:
            self.block_now *= 2
            if self.ultra.get():
                self.ultra.set(0)
            self.eng.stop()
            self.start_engine(0)
            self.msg.config(text="耳機那條來不及處理，已調整緩衝（%d）。如果常出現，請關掉其他吃電腦資源的程式。" % self.block_now)

    def on_close(self):
        try:
            self.save_settings()
            self.eng.stop()
        finally:
            self.root.destroy()


_INSTANCE_PORT = 47391


def already_running():
    """只允許開一個調音台：用本機的一個連接埠當作「已經開著」的記號。
    如果已經有一個在跑，就通知它跳到最前面，自己結束。"""
    import socket
    try:
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.bind(("127.0.0.1", _INSTANCE_PORT))
        srv.listen(1)
        return srv
    except OSError:
        try:
            c = socket.create_connection(("127.0.0.1", _INSTANCE_PORT), timeout=1)
            c.sendall(b"show")
            c.close()
        except Exception:
            pass
        return None


def boost_priority():
    """讓 Windows 優先處理調音台（聲音比較不會被其他程式卡到）。"""
    try:
        import ctypes
        k = ctypes.windll.kernel32
        k.SetPriorityClass(k.GetCurrentProcess(), 0x00000080)   # HIGH_PRIORITY_CLASS
        ctypes.windll.winmm.timeBeginPeriod(1)
    except Exception:
        pass


def main():
    boost_priority()
    srv = already_running()
    if srv is None:
        return
    if engine.sd is None:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("缺少套件", "找不到 sounddevice 套件：\n%s" % engine.SD_ERROR)
        return
    root = tk.Tk()

    def listen():
        import threading

        def loop():
            while True:
                try:
                    c, _ = srv.accept()
                    c.close()
                    root.after(0, lambda: (root.deiconify(), root.lift(), root.focus_force()))
                except Exception:
                    return
        threading.Thread(target=loop, daemon=True).start()
    listen()
    try:
        App(root)
    except Exception:
        log_error(traceback.format_exc())
        messagebox.showerror("錯誤", "程式啟動失敗，詳情請看：\n" + os.path.join(data_dir(), "error.log"))
        return
    root.mainloop()


if __name__ == "__main__":
    main()
