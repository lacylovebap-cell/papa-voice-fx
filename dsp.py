"""PAPA 調音台 - DSP 核心（純 numpy / scipy，可離線測試）

處理順序：電音/變調 → 電話音 → 答錄機音 → EQ → 壓縮 → 混響
所有處理以「區塊」為單位，區塊長度需為 4 的倍數（建議 128 或 256）。
"""
import math
import numpy as np
from scipy.signal import lfilter

SCALES = {
    "major": [0, 2, 4, 5, 7, 9, 11],
    "minor": [0, 2, 3, 5, 7, 8, 10],
    "pent": [0, 2, 4, 7, 9],
    "blues": [0, 3, 5, 6, 7, 10],
}
NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
KEY_NAMES = ["C", "Db", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]
# Krumhansl 大調輪廓：用來從唱過的音判斷是什麼調
_MAJ = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])


class KeyTracker:
    """邊唱邊判斷調：統計最近唱過的音（越久以前權重越小），挑最符合的大調。"""

    def __init__(self, half_life=20.0):
        self.h = np.zeros(12)
        self.half_life = half_life
        self.key = None
        self.sung = 0.0

    def add(self, midi, dt):
        self.h *= 0.5 ** (dt / self.half_life)
        self.h[int(round(midi)) % 12] += dt
        self.sung += dt

    def update(self):
        if self.sung < 2.0 or self.h.sum() <= 0:
            return self.key
        x = self.h / self.h.sum()
        sc = np.array([np.corrcoef(x, np.roll(_MAJ, k))[0, 1] for k in range(12)])
        best = int(np.argmax(sc))
        if self.key is None or sc[best] > sc[self.key] + 0.05:   # 有明顯更好才換調，避免亂跳
            self.key = best
        return self.key


def note_name(m):
    if m is None:
        return "—"
    m = int(round(m))
    return f"{NOTE_NAMES[m % 12]}{m // 12 - 1}"


# ---------------------------------------------------------------- 濾波器
def _rbj(kind, f0, sr, gain_db=0.0, q=0.707):
    A = 10 ** (gain_db / 40)
    w0 = 2 * math.pi * f0 / sr
    cw, sw = math.cos(w0), math.sin(w0)
    alpha = sw / (2 * q)
    if kind == "lowshelf":
        sA = math.sqrt(A)
        b0 = A * ((A + 1) - (A - 1) * cw + 2 * sA * alpha)
        b1 = 2 * A * ((A - 1) - (A + 1) * cw)
        b2 = A * ((A + 1) - (A - 1) * cw - 2 * sA * alpha)
        a0 = (A + 1) + (A - 1) * cw + 2 * sA * alpha
        a1 = -2 * ((A - 1) + (A + 1) * cw)
        a2 = (A + 1) + (A - 1) * cw - 2 * sA * alpha
    elif kind == "highshelf":
        sA = math.sqrt(A)
        b0 = A * ((A + 1) + (A - 1) * cw + 2 * sA * alpha)
        b1 = -2 * A * ((A - 1) + (A + 1) * cw)
        b2 = A * ((A + 1) + (A - 1) * cw - 2 * sA * alpha)
        a0 = (A + 1) - (A - 1) * cw + 2 * sA * alpha
        a1 = 2 * ((A - 1) - (A + 1) * cw)
        a2 = (A + 1) - (A - 1) * cw - 2 * sA * alpha
    elif kind == "peaking":
        b0, b1, b2 = 1 + alpha * A, -2 * cw, 1 - alpha * A
        a0, a1, a2 = 1 + alpha / A, -2 * cw, 1 - alpha / A
    elif kind == "hp":
        b0, b1, b2 = (1 + cw) / 2, -(1 + cw), (1 + cw) / 2
        a0, a1, a2 = 1 + alpha, -2 * cw, 1 - alpha
    elif kind == "lp":
        b0, b1, b2 = (1 - cw) / 2, 1 - cw, (1 - cw) / 2
        a0, a1, a2 = 1 + alpha, -2 * cw, 1 - alpha
    else:
        raise ValueError(kind)
    return np.array([b0, b1, b2]) / a0, np.array([1.0, a1 / a0, a2 / a0])


class Biquad:
    def __init__(self, kind, f0, sr, gain_db=0.0, q=0.707):
        self.kind, self.f0, self.sr, self.q = kind, f0, sr, q
        self.zi = np.zeros(2)
        self.set_gain(gain_db)

    def set_gain(self, gain_db):
        self.gain = gain_db
        self.bypass = self.kind in ("lowshelf", "highshelf", "peaking") and abs(gain_db) < 0.05
        self.b, self.a = _rbj(self.kind, self.f0, self.sr, gain_db, self.q)

    def reset(self):
        self.zi[:] = 0

    def __call__(self, x):
        if self.bypass:
            return x
        y, self.zi = lfilter(self.b, self.a, x, zi=self.zi)
        return y


# ---------------------------------------------------------------- 電音/變調
class Tuner:
    """YIN 音高偵測 + 音階吸附 + 變調。

    變調方式（2026-11 改良）：同一時間只用「一個」讀取頭，速度 = 變調比例；
    讀取頭快跑到邊界時，往回（或往前）跳整數個聲音週期，並用短交叉淡化接起來。
    舊版是兩個取樣點一直疊在一起播，說話時音高一直滑動，兩份聲音對不齊，
    會產生細微的金屬／電音感；新版只有在接合的那幾毫秒才有兩份聲音，而且已經對齊。
    """

    DEC = 4
    R = 512
    PREDICT = 2.0    # 往前預測幾個區塊，補償偵測延遲（讓電音更平、更乾脆）
    CORR_LEN = 384   # 比對長度（樣本）
    SEARCH = 480     # 搜尋範圍（10 ms，涵蓋 100 Hz 以上一個週期）
    DMIN = 3.0       # 讀取頭最少延遲（內插需要）
    JFLOOR = 96      # 每次至少跳 2 ms
    SHIFT_LAT = 120  # 變調平均延遲估計（樣本，約 2.5 ms，伴奏對時用）
    LOOKAHEAD = 0    # 不額外等待（怕怕要求：任何功能都不能增加延遲）
    H = 256

    def __init__(self, sr=48000, window_ms=21.0):
        self.sr = sr
        self.srD = sr / self.DEC
        self.set_window(window_ms)
        self.L = 16384
        self.hist = np.zeros(self.L)
        self.phase = 0.0
        self.dbuf = np.zeros(self.R)
        self.tau_max = min(self.R - self.H - 1, int(self.srD / 70))
        self.tau_min = max(2, int(self.srD / 900))
        self.cur = 0.0
        self.raw_prev = 0.0
        self.wet = 0.0
        self.det = 0.0
        self.target = 0.0
        self.f = 0.0
        self.voiced = False
        self.unv = 99
        self.allowed = [True] * 12
        self.keyt = KeyTracker()
        self.dsm = 0.0          # 平滑後的音高（自然修音用）
        self.vc = 0.0           # 顫音的中心（更慢，約 0.25 秒）
        self.vt = 0.0           # 自然顫音額外加的量（半音）
        self.held = None
        self.kcount = 0
        self.p = dict(mode="off", key=0, scale="major", strength=1.0, speed=0.0, shift=0.0, robot=57, card_shift=0.0,
                      flex=0.0, humanize=0.0, sticky=0.7, predict=2.0, vibrato=0.0)
        self.hold_t = 0.0
        self.prev_target = None
        self.update(**{})

    def set_window(self, ms):
        self.W = int(self.sr * ms / 1000)
        self.X = max(192, self.W // 2)       # 接合時交叉淡化長度
        self.da = self.DMIN + self.JFLOOR    # 目前讀取頭的延遲（樣本）
        self.db = None                       # 淡入中的新讀取頭（接合時才有）
        self.prog = 0
        self.xk = 1.0
        self.splices = 0
        self.extra = 0.0

    def update(self, **kw):
        self.p.update(kw)
        iv = SCALES.get(self.p["scale"], SCALES["major"])
        self.allowed = [False] * 12
        for i in iv:
            self.allowed[(i + int(self.p["key"])) % 12] = True

    def sticky(self, t, x):
        """黏住目前的音：聲音沒有明顯離開（< 0.7 半音）就不換音，避免在兩個音之間抖。"""
        h = self.held
        if h is not None and abs(x - h) < self.p.get("sticky", 0.7) and t != h:
            return h
        self.held = t
        return t

    def quantize(self, det):
        m = self.p["mode"]
        if m in ("natural", "electro"):
            k = self.keyt.key
            if k is None:                     # 還沒判斷出調：先吸最近的半音
                return float(round(det))
            allowed = [False] * 12
            for i in SCALES["major"]:
                allowed[(i + k) % 12] = True
            c0 = int(round(det)); best, bd = c0, 1e9
            for c in range(c0 - 6, c0 + 7):
                if allowed[c % 12] and abs(c - det) < bd:
                    bd, best = abs(c - det), c
            return float(best)
        if m == "chrom":
            return float(round(det))
        if m == "robot":
            r = self.p["robot"]
            return r + 12 * round((det - r) / 12)
        c0 = int(round(det))
        best, bd = c0, 1e9
        for c in range(c0 - 6, c0 + 7):
            if self.allowed[c % 12]:
                dd = abs(c - det)
                if dd < bd:
                    bd, best = dd, c
        return float(best)

    def _detect(self):
        H, tm = self.H, self.tau_max
        seg = self.dbuf[-(H + tm + 1):]
        a = seg[:H]
        if math.sqrt(float(np.mean(seg[-H:] ** 2))) < 0.008:
            return 0.0
        r = np.correlate(seg, a, mode="valid")  # r[k] = sum a[j]*seg[j+k], k=0..tm
        cs = np.concatenate(([0.0], np.cumsum(seg * seg)))
        e1 = cs[H] - cs[0]
        taus = np.arange(1, tm + 1)
        e2 = cs[taus + H] - cs[taus]
        d = e1 + e2 - 2 * r[1: tm + 1]
        d = np.maximum(d, 0.0)
        cm = np.ones(tm + 2)
        csum = np.cumsum(d)
        cm[1: tm + 1] = np.where(csum > 0, d * taus / np.maximum(csum, 1e-12), 1.0)
        cand = np.nonzero(cm[self.tau_min: tm] < 0.15)[0]
        if len(cand) == 0:
            return 0.0
        t = int(cand[0]) + self.tau_min
        while t + 1 < tm and cm[t + 1] < cm[t]:
            t += 1
        tr = float(t)
        if 1 < t < tm:
            a0, b0, c0 = cm[t - 1], cm[t], cm[t + 1]
            den = a0 - 2 * b0 + c0
            if abs(den) > 1e-9:
                tr = t + (a0 - c0) / (2 * den)
        f = self.srD / tr
        return f if 70 <= f <= 900 else 0.0

    def process(self, x):
        n = len(x)
        p = self.p
        active = p["mode"] != "off"
        xd = x.reshape(-1, self.DEC).mean(axis=1)
        self.dbuf = np.concatenate((self.dbuf, xd))[-self.R:]
        f = self._detect()
        self.f = f
        if f > 0:
            raw = 69 + 12 * math.log2(f / 440.0)
            if self.unv == 0 and abs(raw - self.raw_prev) < 0.7:
                slope = raw - self.raw_prev            # 每區塊變化量
                pred = raw + slope * self.p.get("predict", self.PREDICT)
            else:
                pred = raw
            self.raw_prev = raw
            self.unv = 0
            self.det = pred
            dt = n / self.sr
            self.keyt.add(raw, dt)
            self.kcount += 1
            if self.kcount % 40 == 0:
                self.keyt.update()
            # 平滑音高：只追「音的中心」，不追顫音與滑音
            a = 1 - math.exp(-dt / 0.08)
            self.dsm = raw if abs(raw - self.dsm) > 1.0 else self.dsm + (raw - self.dsm) * a
            a2 = 1 - math.exp(-dt / 0.25)
            self.vc = raw if abs(raw - self.vc) > 1.5 else self.vc + (raw - self.vc) * a2
            cs = p["card_shift"]
            # 聲卡變音鎖定時：先升調再吸附，確保疊加後音準仍正確
            if p["mode"] == "robot":
                self.target = self.quantize(self.det) + cs
            elif p["mode"] == "natural":
                self.target = self.sticky(self.quantize(self.dsm + cs), self.dsm + cs)
            else:
                self.target = self.sticky(self.quantize(self.det + cs), self.det + cs) if active else self.det + cs
            self.voiced = True
        else:
            self.unv += 1
            if self.unv > 3:
                self.voiced = False
                self.held = None

        if active and self.voiced:
            # Humanize：同一個音唱越久，修正越慢（長音保留自然的起伏）
            if self.prev_target is not None and self.target == self.prev_target:
                self.hold_t += n / self.sr
            else:
                self.hold_t = 0.0
            self.prev_target = self.target
            cs = p["card_shift"]
            if p["mode"] == "natural":
                # 自然修音：只把「音的中心」拉到準的音上，顫音和滑音原樣保留（不會有機械感）
                want = cs + (self.target - (self.dsm + cs)) * p["strength"] + p["shift"]
            else:
                want = cs + (self.target - (self.det + cs)) * p["strength"] + p["shift"]
            # Flex-Tune：只修離目標音「夠近」的音，離很遠的（刻意的表情、滑音）放過
            fl = p["flex"]
            w = 1.0
            if fl > 0:
                ref = self.dsm if p["mode"] == "natural" else self.det
                dev = abs(self.target - (ref + cs))
                thr = 0.5 - 0.4 * fl
                w = min(1.0, max(0.0, (thr + 0.12 - dev) / 0.12))
                want = cs + p["shift"] + (want - cs - p["shift"]) * w
            # 自然顫音（dB）：放大／縮小你本身的顫音（0＝不變）；直接作用，不經過修正速度的平滑
            vb = p.get("vibrato", 0.0)
            self.vt = 0.0
            if abs(vb) > 0.01 and p["mode"] != "robot":
                vt = (10 ** (vb / 20) - 1) * min(1.5, max(-1.5, self.det - self.vc))
                self.vt = vt if p["mode"] == "natural" else vt * (1 - p["strength"] * w)
            tau = p["speed"] * 0.4 + p["humanize"] * 0.3 * min(1.0, self.hold_t / 0.35)
            alpha = 1 - math.exp(-n / (self.sr * tau)) if tau > 0.0005 else 1.0
        else:
            want = p["shift"] + p["card_shift"]
            alpha = 0.15
            self.vt = 0.0
        if not self.voiced:
            self.prev_target = None
            self.hold_t = 0.0
        if self.voiced and self.unv > 0:
            want = self.cur
        self.cur += (want - self.cur) * alpha

        wet_t = 1.0 if (active or abs(p["shift"] + p["card_shift"]) > 0.001) else 0.0
        k = np.arange(1, n + 1)
        wet = wet_t + (self.wet - wet_t) * (0.997 ** k)
        self.wet = float(wet[-1])
        if self.wet < 0.0005 and wet_t == 0.0:
            self.hist = np.concatenate((self.hist, x))[-self.L:]
            return x

        r = 2 ** ((self.cur + self.vt) / 12)
        self.extra = float(self.LOOKAHEAD) if p["mode"] in ("electro", "chrom", "scale", "robot") else 0.0
        ext = np.concatenate((self.hist, x))
        s = self._shift(ext, n, r)
        self.hist = ext[-self.L:]
        return x * (1 - wet) + s * wet

    # ------------------------------------------------------------ 變調核心
    def _jmin(self, r):
        return int(max(self.JFLOOR, math.ceil(2 * self.X * abs(r - 1))))

    def _read(self, ext, pos):
        i1 = np.floor(pos).astype(np.int64)
        t = pos - i1
        y0, y1, y2, y3 = ext[i1 - 1], ext[i1], ext[i1 + 1], ext[i1 + 2]
        c1 = 0.5 * (y2 - y0)
        c2 = y0 - 2.5 * y1 + 2 * y2 - 0.5 * y3
        c3 = 0.5 * (y3 - y0) + 1.5 * (y1 - y2)
        return ((c3 * t + c2) * t + c1) * t + y1

    def _splice(self, ext, idx, r):
        """找新讀取頭的位置：往回（升調）或往前（降調）跳，讓接起來的波形最像。"""
        Lc, S = self.CORR_LEN, self.SEARCH
        J0 = self._jmin(r)
        src = int(math.floor(idx - self.da))
        ref = ext[src - Lc:src]
        if r > 1:
            seg = ext[src - J0 - S - Lc:src - J0]
        else:
            seg = ext[src + J0 - Lc:src + J0 + S]
        c = np.correlate(seg, ref, mode="valid")
        e = np.sqrt(np.convolve(seg * seg, np.ones(Lc), mode="valid") + 1e-12)
        c = c / (e * (math.sqrt(float(ref @ ref)) + 1e-9))
        if r > 1:
            c = c[::-1]                       # c[j] ↔ 跳 J0 + j
        cm = float(np.max(c))
        j = int(np.nonzero(c >= cm - 0.03)[0][0])   # 優先跳最短的（延遲最小）
        while j + 1 < len(c) and c[j + 1] > c[j]:
            j += 1
        J = J0 + j
        self.db = self.da + J if r > 1 else self.da - J
        # 波形很像 → 等增益淡化；不像（氣音、子音）→ 等功率淡化，避免音量凹陷
        self.xk = min(1.0, max(0.0, (float(c[j]) - 0.3) / 0.4))
        self.prog = 0
        self.splices += 1

    def _shift(self, ext, n, r):
        out = np.zeros(n)
        L, X, Dp = self.L, self.X, self.DMIN
        D0 = Dp + self.extra             # 讀取頭的「工作範圍」下緣（壓平模式會多留一點）
        dr = r - 1.0
        i = 0
        while i < n:
            if self.db is None:
                if dr > 1e-9:
                    thr = D0 + X * dr
                    j = 0 if self.da <= thr else int(math.ceil((self.da - thr) / dr))
                elif dr < -1e-9:
                    thr = D0 + self._jmin(r) + self.SEARCH + 2
                    j = 0 if self.da >= thr else int(math.ceil((thr - self.da) / -dr))
                else:
                    j = n
                m = min(n - i, j)
                if m > 0:
                    k = np.arange(m)
                    d = np.maximum(Dp, self.da - dr * k)
                    out[i:i + m] = self._read(ext, L + i + k - d)
                    self.da = max(Dp, self.da - dr * m)
                    i += m
                if i < n:
                    self._splice(ext, L + i, r)
            else:
                m = min(n - i, X - self.prog)
                k = np.arange(m)
                t = (self.prog + k + 0.5) / X
                eg = 0.5 - 0.5 * np.cos(np.pi * t)
                q = self.xk
                gin = q * eg + (1 - q) * np.sin(0.5 * np.pi * t)
                gout = q * (1 - eg) + (1 - q) * np.cos(0.5 * np.pi * t)
                da = np.maximum(Dp, self.da - dr * k)
                db = np.maximum(Dp, self.db - dr * k)
                pos = L + i + k
                out[i:i + m] = gout * self._read(ext, pos - da) + gin * self._read(ext, pos - db)
                self.prog += m
                self.da = max(Dp, self.da - dr * m)
                self.db -= dr * m
                i += m
                if self.prog >= X:
                    self.da, self.db = self.db, None
        return out


# ---------------------------------------------------------------- 壓縮
class Compressor:
    def __init__(self):
        self.gain_db = 0.0
        self.amount = 0.4

    def process(self, x):
        c = self.amount
        if c <= 0.001:
            self.gain_db *= 0.9
            if abs(self.gain_db) < 0.01:
                return x
        n = len(x)
        level = 20 * math.log10(max(float(np.sqrt(np.mean(x * x))), 1e-5))
        thr = -6 - 30 * c
        ratio = 1 + 7 * c
        tgt = min(0.0, (thr - level) * (1 - 1 / ratio)) if c > 0.001 else 0.0
        coef = 0.6 if tgt < self.gain_db else 0.08  # 變小快、回復慢
        new = self.gain_db + (tgt - self.gain_db) * coef
        g = 10 ** (np.linspace(self.gain_db, new, n, endpoint=False) / 20)
        self.gain_db = new
        return x * g * (1.0 + 0.4 * c)


# ---------------------------------------------------------------- 混響
# 混響類型：(延遲倍率, 殘響長度 0~1, 明亮度 0~1, 預延遲 ms, 立體寬度 0~1, 低頻清除 Hz)
REVERB_TYPES = {
    "經典": (1.00, 0.62, 1.00, 0.0, 1.0, 0.0),       # 原本的混響
    "房間": (0.55, 0.45, 0.60, 4.0, 0.7, 150.0),
    "人聲板": (0.80, 0.60, 0.85, 12.0, 1.0, 200.0),
    "大廳": (1.25, 0.75, 0.55, 25.0, 1.0, 120.0),
    "教堂": (1.55, 0.90, 0.45, 45.0, 1.0, 100.0),
    # CD 絲滑板式：參考 Valhalla VintageVerb「Smooth Plate／1970s」— 偏暗、柔順、預延遲 25 ms、約 2 秒
    "CD 絲滑板式": (0.85, 0.72, 0.30, 25.0, 1.0, 0.0),
}
REVERB_NAMES = list(REVERB_TYPES)        # 類型編號：0＝關，1 起依序對應
REVERB_LAYERS = 3
REVERB_FIELDS = ("type", "mix", "size", "bright", "pre", "width", "lowcut")


class Reverb:
    """Freeverb 式混響核心：8 條梳狀（回授裡有低通＝明亮度）＋4 條全通。spread＝右聲道多錯開的樣本數。"""
    COMB = [1116, 1188, 1277, 1356, 1422, 1491, 1557, 1617]
    ALLP = [347, 441, 556, 701]

    def __init__(self, sr=48000, scale=1.0, spread=0):
        self.sr = sr
        self.g = 0.82
        self.damp = 0.0
        self.spread = spread
        self.set_scale(scale)

    def set_scale(self, scale):
        s = self.sr / 44100.0 * scale
        self.scale = scale
        sp = int(self.spread * self.sr / 44100.0)
        self.cd = [int(d * s) + sp for d in self.COMB]
        self.ad = [int(d * s) + sp for d in self.ALLP]
        self.ch = [np.zeros(d) for d in self.cd]
        self.ah = [np.zeros(d) for d in self.ad]
        self.fl = [0.0] * len(self.cd)
        self.min_delay = min(self.cd + self.ad)

    def process(self, x):
        # 區塊比最短延遲還長時，切成小段處理（任何緩衝大小都能用）
        if len(x) > self.min_delay:
            step = self.min_delay
            return np.concatenate([self._proc(x[i:i + step]) for i in range(0, len(x), step)])
        return self._proc(x)

    def _proc(self, x):
        n = len(x)
        acc = np.zeros(n)
        dmp = self.damp
        for i, d in enumerate(self.cd):
            prev = self.ch[i][:n]
            if dmp > 1e-6:
                f, _ = lfilter([1 - dmp], [1, -dmp], prev, zi=[dmp * self.fl[i]])
                self.fl[i] = float(f[-1])
            else:
                f = prev
                self.fl[i] = float(prev[-1])
            y = x + self.g * f
            self.ch[i] = np.concatenate((self.ch[i][n:], y))
            acc += y
        y = acc * 0.1
        for i, d in enumerate(self.ad):
            wh = self.ah[i][:n]
            w = y + 0.5 * wh
            y = -0.5 * w + wh
            self.ah[i] = np.concatenate((self.ah[i][n:], w))
        return y


class ReverbLayer:
    """一層混響（像混音台上的一條 FX 通道）：類型、深度、殘響長度、明亮度、預延遲、立體寬度、低頻清除都能各自調。"""
    WET = 1.6 * math.sqrt(2.0)

    def __init__(self, sr):
        self.sr = sr
        self.L = self.R = None
        self.type = 0
        self.mix, self.size, self.bright, self.pre_ms, self.width, self.lowcut = 0.0, 0.6, 1.0, 0.0, 1.0, 0.0
        self.pre_buf = np.zeros(int(sr * 0.12))
        self.pre = 0
        self.hp = None
        self._hp_f = 0.0
        self.lp = None              # 高頻柔化（明亮度越低，尾巴越暗、越柔）
        self._lp_f = 0.0

    def set(self, type=None, mix=None, size=None, bright=None, pre=None, width=None, lowcut=None):
        if type is not None and int(type) != self.type:
            self.type = max(0, min(len(REVERB_NAMES), int(type)))
            if self.type > 0:
                scale = REVERB_TYPES[REVERB_NAMES[self.type - 1]][0]
                if self.L is None:
                    self.L, self.R = Reverb(self.sr, scale, 0), Reverb(self.sr, scale, 23)
                else:
                    self.L.set_scale(scale); self.R.set_scale(scale)
                self.pre_buf[:] = 0.0
        for k, v in (("mix", mix), ("size", size), ("bright", bright), ("pre_ms", pre), ("width", width), ("lowcut", lowcut)):
            if v is not None:
                setattr(self, k, float(v))
        cl = lambda v: min(1.0, max(0.0, v))
        if self.L is not None:
            for rv in (self.L, self.R):
                rv.g = 0.75 + 0.22 * cl(self.size)
                rv.damp = (1.0 - cl(self.bright)) * 0.7
        self.pre = int(min(100.0, max(0.0, self.pre_ms)) * self.sr / 1000)
        f = min(800.0, max(0.0, self.lowcut))
        if f != self._hp_f:
            self._hp_f = f
            self.hp = Biquad("hp", f, self.sr, q=0.707) if f >= 20 else None
        b = min(1.0, max(0.0, self.bright))
        fl = 3000.0 + 15000.0 * b * b
        if fl != self._lp_f:
            self._lp_f = fl
            self.lp = [Biquad("lp", fl, self.sr, q=0.707), Biquad("lp", fl, self.sr, q=0.707)] if fl < 17500 else None

    @property
    def active(self):
        return self.type > 0 and self.mix > 0.001

    def process(self, y):
        """回傳 (中間, 兩側)：中間加到人聲（直播只聽得到這個），兩側只在耳機做出左右寬度。"""
        x = y
        if self.hp is not None:
            x = self.hp(x)
        if self.pre > 0:
            P = len(self.pre_buf)
            ext = np.concatenate((self.pre_buf, x))
            self.pre_buf = ext[-P:]
            x = ext[P - self.pre:P - self.pre + len(x)]
        l, r = self.L.process(x), self.R.process(x)
        if self.lp is not None:
            l, r = self.lp[0](l), self.lp[1](r)
        k = self.mix * self.WET
        return (l + r) * (0.5 * k), (l - r) * (0.5 * k * min(1.0, max(0.0, self.width)))


# ---------------------------------------------------------------- 人聲質感（CD 質感）
class Doubler:
    """人聲加寬：四個稍微延遲、音高微微飄動的副本，左右分開（像 Doubler 外掛）。
    回傳 (中間, 兩側)：中間＝厚度（直播也聽得到），兩側＝立體寬度（只有耳機聽得出來）。"""
    VOICES = ((9.4, 0.25, 0.31, -1.0), (16.0, 0.30, 0.23, 1.0), (23.7, 0.35, 0.37, -0.8), (21.0, 0.28, 0.27, 0.8))

    def __init__(self, sr):
        self.sr = sr
        self.L = 1 << 13
        self.buf = np.zeros(self.L)
        self.w = 0
        self.t = 0

    def process(self, y, thick, widen):
        n = len(y)
        L = self.L
        self.buf[(self.w + np.arange(n)) % L] = y
        tt = (self.t + np.arange(n)) / self.sr
        mid = np.zeros(n); side = np.zeros(n)
        for base, depth, rate, pan in self.VOICES:
            d = (base + depth * np.sin(2 * np.pi * rate * tt + base)) * self.sr / 1000.0
            pos = (self.w + np.arange(n)) - d
            i0 = np.floor(pos).astype(np.int64)
            fr = pos - i0
            v = self.buf[i0 % L] * (1 - fr) + self.buf[(i0 + 1) % L] * fr
            mid += v
            side += v * pan
        self.w = (self.w + n) % L
        self.t += n
        g = 0.18
        return mid * (g * thick), side * (g * widen)


class Leveler:
    """響度：自動把人聲穩定在好聽的大小（小聲拉上來、大聲壓住），再用柔和的限制器收頂。
    沒聲音的時候不會亂放大底噪。"""

    def __init__(self, sr):
        self.sr = sr
        self.gdb = 0.0

    def process(self, y, amt):
        n = len(y)
        if amt <= 0.001:
            self.gdb *= 0.9
            if abs(self.gdb) < 0.01:
                return y
        rms = math.sqrt(float(np.mean(y * y)) + 1e-12)
        lvl = 20 * math.log10(rms)
        target = -20.0 + 4.0 * amt                 # 響度越大，目標越大聲
        if lvl < -48.0 or amt <= 0.001:            # 沒聲音：慢慢回到 0 dB，不放大底噪
            want = 0.0 if amt > 0.001 else 0.0
            tau = 0.8
        else:
            want = max(-9.0, min(12.0, target - lvl)) * amt
            tau = 0.12 if want < self.gdb else 0.9   # 壓下來快、拉上去慢
        dt = n / self.sr
        new = self.gdb + (want - self.gdb) * (1 - math.exp(-dt / tau))
        g = 10 ** ((self.gdb + (new - self.gdb) * np.arange(1, n + 1) / n) / 20)
        self.gdb = new
        z = y * g
        # 柔和收頂（0.7 以上才開始壓，最大不超過 0.98）
        a = np.abs(z)
        over = a > 0.7
        if np.any(over):
            z = np.where(over, np.sign(z) * (0.7 + 0.28 * np.tanh((a - 0.7) / 0.28)), z)
        return z


# ---------------------------------------------------------------- 電音質感
class ElectroLayer:
    """雙重唱（兩個緩慢擺動的延遲副本）＋空氣感高頻＋短回音，讓電音聽起來亮、寬、精緻。"""

    def __init__(self, sr):
        self.sr = sr
        self.L = 1 << 15
        self.buf = np.zeros(self.L)
        self.w = 0
        self.t = 0
        self.air = Biquad("highshelf", 9000, sr, 5.0)
        self.pres = Biquad("peaking", 4500, sr, 2.5, q=0.9)
        self.echo = np.zeros(int(sr * 0.5))
        self.ew = 0
        self.ed = int(sr * 0.165)

    def process(self, y, amt):
        n = len(y)
        L = self.L
        pos_w = (self.w + np.arange(n)) % L
        self.buf[pos_w] = y
        tt = (self.t + np.arange(n)) / self.sr
        out = np.zeros(n)
        for base, depth, rate, phase in ((0.017, 0.0022, 0.37, 0.0), (0.023, 0.0026, 0.29, 2.1)):
            d = (base + depth * np.sin(2 * np.pi * rate * tt + phase)) * self.sr
            pos = (self.w + np.arange(n)) - d
            i0 = np.floor(pos).astype(np.int64)
            fr = pos - i0
            out += self.buf[i0 % L] * (1 - fr) + self.buf[(i0 + 1) % L] * fr
        self.w = (self.w + n) % L
        self.t += n
        dbl = out * 0.5
        # 回音（約 1/8 拍，帶一點回授）
        E = len(self.echo)
        ri = (self.ew - self.ed + np.arange(n)) % E
        ech = self.echo[ri].copy()
        self.echo[(self.ew + np.arange(n)) % E] = y + ech * 0.28
        self.ew = (self.ew + n) % E
        wet = y + amt * 0.55 * dbl     # 不加回音：混響只由使用者的「混響」拉桿決定
        bright = self.pres(self.air(wet))
        return y * (1 - amt) + bright * amt


# ---------------------------------------------------------------- 聲線（直播人聲調音公式）
VOICE_COLORS = {
    "原本": [],
    # 唱歌用的人聲修飾：參考「少女甜嗓」公式，強度減半，比較自然
    "人聲修飾": [(200, -1.5, 1.0), (2000, 2.5, 1.8), (4000, 1.5, 0.8), (8000, -1.5, 1.0), (16000, 1.0, 0.3)],
    "少女甜嗓": [(200, -3, 1.0), (2000, 5, 1.8), (4000, 3, 0.8), (8000, -3, 1.0), (16000, 2, 0.3)],
    "御姐煙嗓": [(80, 4, 0.7), (250, 3, 1.2), (800, -2, 2.0), (3000, -3, 1.5), (12000, 2, 0.5)],
    "古風戲腔": [(80, -3, 1.2), (250, -2, 1.0), (1500, 3, 0.8), (4000, 4, 1.0), (10000, 3, 1.2)],
    "ASMR": [(300, 6, 0.5), (1200, -4, 2.0), (4000, 3, 0.8), (8000, 6, 1.5), (12000, 5, 0.3)],
}


# ---------------------------------------------------------------- 金屬共鳴（更電的機械感）
class MetalComb:
    """跟著目前的目標音調整的回授梳狀濾波器：只放大「準確音高」的泛音，聲音變得更像電子合成、更電。
    共鳴強度與延遲長度都是逐點慢慢變（約 8 ms／6 ms），開關或換音時不會「啵」。"""

    def __init__(self, sr):
        self.sr = sr
        self.L = 4096
        self.buf = np.zeros(self.L)
        self.w = 0
        self.D = 200.0
        self.Dt = 200.0
        self.g = 0.0
        self.kg = 1 - math.exp(-1.0 / (0.008 * sr))
        self.kd = 1 - math.exp(-1.0 / (0.006 * sr))

    def process(self, x, amt, midi):
        if amt <= 0.001 and self.g < 1e-4:
            # 保持狀態連續
            idx = (self.w + np.arange(len(x))) % self.L
            self.buf[idx] = x
            self.w = (self.w + len(x)) % self.L
            self.g = 0.0
            return x
        if midi is not None:
            f = 440.0 * 2 ** ((midi - 69) / 12)
            self.Dt = min(self.L - 2, max(20.0, self.sr / f))
        # 有唱音時才共鳴；一停下來很快收掉（約 20 ms），不留尾音（不會像回音／混響）
        gt = (0.35 + 0.35 * amt) if (midi is not None and amt > 0.001) else 0.0
        out = np.empty(len(x))
        buf, L, w = self.buf, self.L, self.w
        g, D, kg, kd, Dt = self.g, self.D, self.kg, self.kd, self.Dt
        for i in range(len(x)):
            g += (gt - g) * kg
            D += (Dt - D) * kd
            i0 = int(D); fr = D - i0
            a = buf[(w - i0) % L]; b = buf[(w - i0 - 1) % L]
            y = x[i] + g * (a * (1 - fr) + b * fr)
            buf[w] = y
            out[i] = y * (1 - g)           # 補償回授造成的音量增加
            w = (w + 1) % L
        self.w, self.g, self.D = w, g, D
        return x * (1 - amt) + out * amt


# ---------------------------------------------------------------- 去齒音
class DeEsser:
    """只在出現「嘶、ㄘ、ㄙ」這類刺耳齒音時，把 5kHz 以上壓下來；平常母音不受影響。"""

    def __init__(self, sr):
        self.hp = [Biquad("hp", 5000, sr, q=0.7), Biquad("hp", 5000, sr, q=0.7)]
        self.shelf = Biquad("highshelf", 4500, sr, 0.0)
        self.g = 1.0
        self.reduced = 0.0

    def process(self, y, amt):
        hb = y
        for f in self.hp:
            hb = f(hb)
        if amt <= 0.001:
            self.g = 1.0
            self.shelf.set_gain(0.0)
            return y
        s = float(np.sqrt(np.mean(hb * hb))) + 1e-9
        t = float(np.sqrt(np.mean(y * y))) + 1e-9
        rel = 20 * math.log10(s / t)              # 高頻占整體多少（dB）
        thr = -6.0 - 8.0 * amt                    # 強度越高，越早開始壓
        excess = max(0.0, rel - thr)
        gdb = -min(14.0 * amt + 2.0, excess * (0.6 + 0.4 * amt)) if excess > 0 else 0.0
        want = 10 ** (gdb / 20)
        g_end = want if want < self.g else self.g + (want - self.g) * 0.3   # 壓得快、放得慢
        self.g = g_end
        self.reduced = -20 * math.log10(max(g_end, 1e-6))
        # 用高頻搁架濾波器直接把高頻調小（不會有分頻相位問題）
        self.shelf.set_gain(-self.reduced if self.reduced > 0.05 else 0.0)
        return self.shelf(y)


# ---------------------------------------------------------------- 限制器
class Limiter:
    """太大聲時平滑地把音量壓下來（不像硬削波那樣把波形切平，聽起來不會破、不會吵）。"""

    def __init__(self, sr, ceiling=0.89, release=0.15):
        self.g = 1.0
        self.ceiling = ceiling
        self.rel = math.exp(-1.0 / (release * sr))
        self.reduced = 0.0    # 最近一次壓了多少 dB（給畫面顯示）

    def process(self, x):
        n = len(x)
        if n == 0:
            return x
        peak = float(np.max(np.abs(x)))
        want = min(1.0, self.ceiling / peak) if peak > 1e-9 else 1.0
        if want < self.g:
            g_end = want                                   # 變大聲：立刻壓
        else:
            g_end = want + (self.g - want) * self.rel ** n   # 變小聲：慢慢放開
        ramp = np.linspace(self.g, g_end, n, endpoint=False) if g_end > self.g else np.full(n, g_end)
        self.g = g_end
        self.reduced = -20 * math.log10(max(g_end, 1e-6))
        y = x * ramp
        # 保險：極少數超過的樣本用柔和曲線收邊，不硬切
        over = np.abs(y) > self.ceiling
        if over.any():
            c = self.ceiling
            y[over] = np.sign(y[over]) * (c + (1 - c) * np.tanh((np.abs(y[over]) - c) / (1 - c)))
        return y


# ---------------------------------------------------------------- 整合
class NoiseGate:
    """降噪（向下擴展器）：聲音低於門檻時慢慢壓低，一出聲立刻打開；不增加延遲。和 C++ 版相同。"""

    def __init__(self, sr):
        self.sr = sr
        self.env = 0.0
        self.gdb = 0.0
        self.hold = 0.0

    def process(self, y, n, amt):
        if n <= 0:
            return y
        rms = float(np.sqrt(np.mean(y[:n] ** 2)))      # 用平均音量判斷（細小雜音的尖峰不會讓它打開）
        self.env = max(rms, self.env * np.exp(-n / (self.sr * 0.05)))
        tgt = 0.0
        if amt > 0.001:
            lvl = 20 * np.log10(self.env + 1e-9)
            thr = -78.0 + 33.0 * amt
            rng = -(10.0 + 30.0 * amt)
            if lvl >= thr:
                self.hold, tgt = 0.15 * self.sr, 0.0
            elif self.hold > 0:
                self.hold -= n
            else:
                tgt = max(rng, (lvl - thr) * 4.0)
        else:
            self.hold = 0.0
        g0db = self.gdb
        if tgt > self.gdb:
            self.gdb = tgt
        else:
            self.gdb += (tgt - self.gdb) * (1 - np.exp(-n / (self.sr * 0.08)))
        if g0db > -0.01 and self.gdb > -0.01:
            return y
        g0, g1 = 10 ** (g0db / 20), 10 ** (self.gdb / 20)
        y = y.copy()
        y[:n] *= g0 + (g1 - g0) * np.arange(1, n + 1) / n
        return y


class VoiceFx:
    CARD_SHIFT = 1.5  # 聲卡變音的升調量（由原聲／說話二比對量測）

    def __init__(self, sr=48000, low_latency=False):
        self.sr = sr
        self.tuner = Tuner(sr, 12.0 if low_latency else 21.0)
        self.p = dict(
            phone=False, phone_dist=0.25, machine=False,
            eq_lo=0.0, eq_mid=0.0, eq_hi=0.0, comp=0.4, verb=0.15, card=False, electro_fx=0.0, color="原本", deess=0.0, metal=0.0, robot_fx=0.0,
            loud=0.0, clarity=0.0, thick=0.0, widen=0.0, gate=0.0,
        )
        # 聲卡變音音色（由怕怕原聲／說話二比對量測）：中低頻 -6dB、3.2kHz 凹陷
        self.card_eq = [Biquad("lowshelf", 350, sr, -6.0), Biquad("peaking", 3200, sr, -5.0, q=1.4)]
        # 電話音
        self.ph_hp = [Biquad("hp", 320, sr, q=0.9) for _ in range(2)]
        self.ph_lp = [Biquad("lp", 3300, sr, q=0.9) for _ in range(2)]
        self.ph_mix = 0.0
        # 答錄機音（乾淨版）：小喇叭窄頻＋中頻共鳴，不失真、不加雜訊
        self.m_chain = [Biquad("hp", 450, sr, q=0.7), Biquad("hp", 450, sr, q=0.7),
                        Biquad("lp", 3400, sr, q=0.7), Biquad("lp", 3400, sr, q=0.7),
                        Biquad("peaking", 1400, sr, 5.0, q=1.0), Biquad("peaking", 700, sr, -2.0, q=1.2)]
        self.m_mix = 0.0
        # 音色
        self.eq = [Biquad("lowshelf", 150, sr), Biquad("peaking", 2200, sr, q=0.8), Biquad("highshelf", 7000, sr)]
        self.comp = Compressor()
        self.electro = ElectroLayer(sr)
        self.deesser = DeEsser(sr)
        self.metal = MetalComb(sr)
        self.ring_ph = 0.0
        self.color_eq = []
        self._color = None
        # 混響：最多 3 層，各自獨立（跟效果無關，切換效果不會改變）
        self.layers = [ReverbLayer(sr) for _ in range(REVERB_LAYERS)]
        self.side = np.zeros(0, np.float32)
        # 人聲質感（全部效果共用，預設關閉）
        self.clar = [Biquad("peaking", 250, sr, 0.0, q=1.0), Biquad("peaking", 5000, sr, 0.0, q=1.0),
                     Biquad("highshelf", 9000, sr, 0.0)]
        self.doubler = Doubler(sr)
        self.leveler = Leveler(sr)
        self.gate = NoiseGate(sr)
        self.info = (0.0, None, None)
        self.set()

    def set(self, **kw):
        # 舊的單一「混響」拉桿：對應到第 1 層「經典」（和以前的聲音完全一樣）
        if "verb" in kw:
            v = float(kw["verb"])
            self.layers[0].set(type=1, mix=v, size=(0.05 + 0.13 * v) / 0.22, bright=1.0, pre=0.0, width=1.0, lowcut=0.0)
        for i, L in enumerate(self.layers):
            lk = {f: kw["rv%d_%s" % (i + 1, f)] for f in REVERB_FIELDS if "rv%d_%s" % (i + 1, f) in kw}
            if lk:
                L.set(**lk)
        tuner_keys = {"mode", "key", "scale", "strength", "speed", "shift", "robot", "flex", "humanize", "sticky", "predict", "vibrato"}
        tk = {k: v for k, v in kw.items() if k in tuner_keys}
        if "card" in kw:
            tk["card_shift"] = self.CARD_SHIFT if kw["card"] else 0.0
        if tk:
            self.tuner.update(**tk)
        for k, v in kw.items():
            if k in self.p:
                self.p[k] = v
        if self.p["color"] != self._color:
            self._color = self.p["color"]
            self.color_eq = [Biquad("peaking", min(f, self.sr * 0.45), self.sr, g, q=q)
                             for f, g, q in VOICE_COLORS.get(self._color, [])]
        c = float(self.p["clarity"])
        self.clar[0].set_gain(-3.0 * c)     # 去悶
        self.clar[1].set_gain(2.5 * c)      # 清楚
        self.clar[2].set_gain(5.0 * c)      # 空氣感
        self.eq[0].set_gain(self.p["eq_lo"])
        self.eq[1].set_gain(self.p["eq_mid"])
        self.eq[2].set_gain(self.p["eq_hi"])
        self.comp.amount = self.p["comp"]

    # ----- 效果分支
    def _phone(self, x):
        y = x
        for f in self.ph_hp + self.ph_lp:
            y = f(y)
        k = self.p["phone_dist"] * 40
        y = ((1 + k) * y) / (1 + k * np.abs(y))
        return y * 1.6

    def _machine(self, x):
        """答錄機音（乾淨版）：像聲音從答錄機／話筒小喇叭播出來，但不失真。"""
        y = x
        for f in self.m_chain:
            y = f(y)
        return y * 1.8

    @staticmethod
    def _xfade(dry, wet, m0, m1):
        n = len(dry)
        r = np.linspace(m0, m1, n, endpoint=False)
        return dry * (1 - r) + wet * r

    def process(self, x):
        x = np.asarray(x, dtype=np.float64)
        n0 = len(x)
        pad = (-n0) % 4
        if pad:
            x = np.concatenate((x, np.zeros(pad)))
        x = self.gate.process(x, n0, float(self.p["gate"]))   # 降噪：先把靜音時的細小雜音壓掉
        y = self.tuner.process(x)
        self.info = (self.tuner.f,
                     self.tuner.det if self.tuner.voiced else None,
                     self.tuner.target if self.tuner.voiced and self.tuner.p["mode"] != "off" else None)

        # 電話音
        pt = 1.0 if self.p["phone"] else 0.0
        if pt > 0 or self.ph_mix > 0.001:
            y = self._xfade(y, self._phone(y), self.ph_mix, pt)
        else:
            for f in self.ph_hp + self.ph_lp:
                f.reset()
        self.ph_mix = pt
        # 答錄機
        mt = 1.0 if self.p["machine"] else 0.0
        if mt > 0 or self.m_mix > 0.001:
            y = self._xfade(y, self._machine(y), self.m_mix, mt)
        self.m_mix = mt

        if self.p["card"]:
            for f in self.card_eq:
                y = f(y)
        else:
            for f in self.card_eq:
                f.reset()
        tg = self.tuner.target if (self.tuner.voiced and self.tuner.p["mode"] != "off") else None
        # 機器人聲：環形調變（55 Hz）讓聲音像機器、金屬喇叭
        rf = float(self.p["robot_fx"])
        if rf > 0.001:
            n = len(y)
            ph = self.ring_ph + 2 * np.pi * 55.0 / self.sr * np.arange(1, n + 1)
            a = 0.7 * rf
            y = y * (1 - a) + y * np.sin(ph) * a * 1.6
            self.ring_ph = float(ph[-1] % (2 * np.pi))
        y = self.metal.process(y, float(self.p["metal"]), tg)
        if self.p["electro_fx"] > 0.001:
            y = self.electro.process(y, float(self.p["electro_fx"]))
        for f in self.color_eq:
            y = f(y)
        for f in self.eq:
            y = f(y)
        for f in self.clar:
            y = f(y)
        y = self.deesser.process(y, float(self.p["deess"]))
        y = self.comp.process(y)
        y = self.leveler.process(y, float(self.p["loud"]))
        # 人聲加寬／厚度
        side = np.zeros(len(y))
        th, wd = float(self.p["thick"]), float(self.p["widen"])
        if th > 0.001 or wd > 0.001:
            dm, ds = self.doubler.process(y, th, wd)
            y = y + dm
            side = side + ds
        else:
            self.doubler.process(y, 0.0, 0.0)
        # 混響（每一層各自加上去；兩側成分另外給耳機做立體寬度）
        dry = y
        for L in self.layers:
            if L.active:
                m, sd = L.process(dry)
                y = y + m
                side = side + sd
        self.side = side[:n0].astype(np.float32)
        y = np.clip(y, -1.0, 1.0)
        return y[:n0].astype(np.float32)
