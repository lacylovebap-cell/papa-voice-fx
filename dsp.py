"""怕怕調音台 - DSP 核心（純 numpy / scipy，可離線測試）

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
    """YIN 音高偵測 + 音階吸附 + 雙取樣點變調。"""

    DEC = 4
    R = 512
    PREDICT = 2.0    # 往前預測幾個區塊，補償偵測延遲（讓電音更平、更乾脆）
    CORR_LEN = 384   # 比對長度（樣本）
    SEARCH = 480     # 搜尋範圍（10 ms，涵蓋 100 Hz 以上一個週期）
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
        self.held = None
        self.kcount = 0
        self.p = dict(mode="off", key=0, scale="major", strength=1.0, speed=0.0, shift=0.0, robot=57, card_shift=0.0,
                      flex=0.0, humanize=0.0)
        self.hold_t = 0.0
        self.prev_target = None
        self.update(**{})

    def set_window(self, ms):
        self.W = int(self.sr * ms / 1000)
        self.Wbase = float(self.W)
        self.Wtarget = float(self.W)
        self.tW = [float(self.W), float(self.W)]
        self.tph = [0.0, 0.5]
        self.toff = [0.0, 0.0]

    def update(self, **kw):
        self.p.update(kw)
        iv = SCALES.get(self.p["scale"], SCALES["major"])
        self.allowed = [False] * 12
        for i in iv:
            self.allowed[(i + int(self.p["key"])) % 12] = True

    def sticky(self, t, x):
        """黏住目前的音：聲音沒有明顯離開（< 0.7 半音）就不換音，避免在兩個音之間抖。"""
        h = self.held
        if h is not None and abs(x - h) < 0.7 and t != h:
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
                pred = raw + slope * self.PREDICT
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
            if fl > 0:
                ref = self.dsm if p["mode"] == "natural" else self.det
                dev = abs(self.target - (ref + cs))
                thr = 0.5 - 0.4 * fl
                w = min(1.0, max(0.0, (thr + 0.12 - dev) / 0.12))
                want = cs + p["shift"] + (want - cs - p["shift"]) * w
            tau = p["speed"] * 0.4 + p["humanize"] * 0.3 * min(1.0, self.hold_t / 0.35)
            alpha = 1 - math.exp(-n / (self.sr * tau)) if tau > 0.0005 else 1.0
        else:
            want = p["shift"] + p["card_shift"]
            alpha = 0.15
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

        self.Wtarget = self.Wbase
        r = 2 ** (self.cur / 12)
        ext = np.concatenate((self.hist, x))
        idx = self.L + np.arange(n)
        ar = np.arange(n)
        PH, WA, OF = [], [], []
        for tap in (0, 1):
            PH.append(self.tph[tap] + (1 - r) / self.tW[tap] * ar)
            WA.append(np.full(n, self.tW[tap]))
            OF.append(np.full(n, self.toff[tap]))
        for tap in (0, 1):
            ph = PH[tap]
            wrap = np.nonzero((ph >= 1) | (ph < 0))[0]
            if not len(wrap):
                continue
            w0 = wrap[0]
            o = 1 - tap
            Wn = self.Wtarget
            p0 = 0.9999999 if r >= 1 else 0.0       # 一律從增益 0 的位置重新開始
            # 找最佳接合點：讓新取樣點讀到的聲音和目前正在播的那段最像（避免子音被重複成「啵」）
            od = OF[o][w0] + (PH[o][w0] % 1.0) * WA[o][w0]
            po = idx[w0] - od                      # 另一個取樣點此刻的讀取位置
            pn = idx[w0] - p0 * Wn                 # 新取樣點在偏移 0 時的讀取位置
            Lc, S = self.CORR_LEN, self.SEARCH
            ref = ext[int(po) - Lc:int(po)]
            start = int(pn) - Lc - S
            if start > 2 and len(ref) == Lc:
                seg = ext[start:int(pn)]
                c = np.correlate(seg, ref, mode="valid")[::-1]   # c[k] 對應偏移 k
                e = np.sqrt(np.convolve(seg * seg, np.ones(Lc), mode="valid")[::-1] + 1e-12)
                off = float(np.argmax(c / e))
            else:
                off = 0.0
            ph[w0:] = p0 + (1 - r) / Wn * np.arange(n - w0)
            WA[tap][w0:] = Wn
            OF[tap][w0:] = off
            self.tW[tap] = Wn
            self.toff[tap] = off
        s = np.zeros(n)
        gsum = np.zeros(n)
        for tap in (0, 1):
            ph = PH[tap] % 1.0
            g = 0.5 - 0.5 * np.cos(2 * np.pi * ph)
            pos = idx - (OF[tap] + ph * WA[tap] + 3)
            i1 = np.floor(pos).astype(np.int64)
            t = pos - i1
            y0, y1, y2, y3 = ext[i1 - 1], ext[i1], ext[i1 + 1], ext[i1 + 2]
            c1 = 0.5 * (y2 - y0)
            c2 = y0 - 2.5 * y1 + 2 * y2 - 0.5 * y3
            c3 = 0.5 * (y3 - y0) + 1.5 * (y1 - y2)
            s += g * (((c3 * t + c2) * t + c1) * t + y1)
            gsum += g
            self.tph[tap] = float((PH[tap][-1] + (1 - r) / WA[tap][-1]) % 1.0)
        self.hist = ext[-self.L:]
        # 兩個取樣點長度不同時增益和不為 1，正規化避免音量起伏（會聽起來像雜訊）
        s = s / np.maximum(gsum, 0.5)
        return x * (1 - wet) + s * wet


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
class Reverb:
    COMB = [1116, 1188, 1277, 1356, 1422, 1491, 1557, 1617]
    ALLP = [347, 441, 556, 701]

    def __init__(self, sr=48000):
        s = sr / 44100.0
        self.cd = [int(d * s) for d in self.COMB]
        self.ad = [int(d * s) for d in self.ALLP]
        self.ch = [np.zeros(d) for d in self.cd]
        self.ah = [np.zeros(d) for d in self.ad]
        self.g = 0.82
        self.damp = [0.0] * len(self.cd)
        self.min_delay = min(self.cd + self.ad)

    def process(self, x):
        n = len(x)
        if n > self.min_delay:
            raise ValueError("區塊太大，需 <= %d" % self.min_delay)
        acc = np.zeros(n)
        for i, d in enumerate(self.cd):
            prev = self.ch[i][:n]
            y = x + self.g * prev
            self.ch[i] = np.concatenate((self.ch[i][n:], y))
            acc += y
        y = acc * 0.1
        for i, d in enumerate(self.ad):
            wh = self.ah[i][:n]
            w = y + 0.5 * wh
            y = -0.5 * w + wh
            self.ah[i] = np.concatenate((self.ah[i][n:], w))
        return y


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
        wet = y + amt * (0.55 * dbl + 0.22 * ech)
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
class VoiceFx:
    CARD_SHIFT = 1.5  # 聲卡變音的升調量（由原聲／說話二比對量測）

    def __init__(self, sr=48000, low_latency=False):
        self.sr = sr
        self.tuner = Tuner(sr, 12.0 if low_latency else 21.0)
        self.p = dict(
            phone=False, phone_dist=0.25, machine=False,
            eq_lo=0.0, eq_mid=0.0, eq_hi=0.0, comp=0.4, verb=0.15, card=False, electro_fx=0.0, color="原本", deess=0.0,
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
        self.color_eq = []
        self._color = None
        self.verb = Reverb(sr)
        self.info = (0.0, None, None)
        self.set()

    def set(self, **kw):
        tuner_keys = {"mode", "key", "scale", "strength", "speed", "shift", "robot", "flex", "humanize"}
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
        if self.p["electro_fx"] > 0.001:
            y = self.electro.process(y, float(self.p["electro_fx"]))
        for f in self.color_eq:
            y = f(y)
        for f in self.eq:
            y = f(y)
        y = self.deesser.process(y, float(self.p["deess"]))
        y = self.comp.process(y)
        v = self.p["verb"]
        if v > 0.001:
            y = y + self.verb.process(y) * (v * 0.8)
        else:
            self.verb.process(y)  # 保持尾巴狀態連續
        y = np.clip(y, -1.0, 1.0)
        return y[:n0].astype(np.float32)
