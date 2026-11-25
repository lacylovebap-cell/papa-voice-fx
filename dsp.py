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
        self.wet = 0.0
        self.det = 0.0
        self.target = 0.0
        self.f = 0.0
        self.voiced = False
        self.unv = 99
        self.allowed = [True] * 12
        self.p = dict(mode="off", key=0, scale="major", strength=1.0, speed=0.0, shift=0.0, robot=57, card_shift=0.0)
        self.update(**{})

    def set_window(self, ms):
        self.W = int(self.sr * ms / 1000)
        self.Wbase = float(self.W)
        self.Wtarget = float(self.W)
        self.tW = [float(self.W), float(self.W)]
        self.tph = [0.0, 0.5]

    def update(self, **kw):
        self.p.update(kw)
        iv = SCALES.get(self.p["scale"], SCALES["major"])
        self.allowed = [False] * 12
        for i in iv:
            self.allowed[(i + int(self.p["key"])) % 12] = True

    def quantize(self, det):
        m = self.p["mode"]
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
            self.unv = 0
            self.det = 69 + 12 * math.log2(f / 440.0)
            cs = p["card_shift"]
            # 聲卡變音鎖定時：先升調再吸附，確保疊加後音準仍正確
            if p["mode"] == "robot":
                self.target = self.quantize(self.det) + cs
            else:
                self.target = self.quantize(self.det + cs) if active else self.det + cs
            self.voiced = True
        else:
            self.unv += 1
            if self.unv > 3:
                self.voiced = False

        if active and self.voiced:
            cs = p["card_shift"]
            want = cs + (self.target - (self.det + cs)) * p["strength"] + p["shift"]
            tau = p["speed"] * 0.4
            alpha = 1 - math.exp(-n / (self.sr * tau)) if tau > 0.0005 else 1.0
        else:
            want = p["shift"] + p["card_shift"]
            alpha = 0.15
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

        # 依偵測到的音高，把視窗長度對齊成整數個週期（降低變調的粗糙感）
        if self.voiced and self.f > 0:
            per = self.sr / self.f
            # 兩個取樣點相差 W/2，所以 W/2 要是整數個週期，交叉淡化時才會同相位
            k = max(1, int(round(self.Wbase / (2 * per))))
            self.Wtarget = min(max(2 * k * per, self.Wbase * 0.5), self.Wbase * 2.0)
        else:
            self.Wtarget = self.Wbase
        r = 2 ** (self.cur / 12)
        ext = np.concatenate((self.hist, x))
        idx = self.L + np.arange(n)
        ar = np.arange(n)
        PH, WA = [], []
        for tap in (0, 1):
            Wt = self.tW[tap]
            ph = self.tph[tap] + (1 - r) / Wt * ar
            PH.append(ph)
            WA.append(np.full(n, Wt))
        for tap in (0, 1):
            ph = PH[tap]
            wrap = np.nonzero((ph >= 1) | (ph < 0))[0]
            if not len(wrap):
                continue
            w0 = wrap[0]
            o = 1 - tap
            Wn = self.Wtarget
            od = (PH[o][w0] % 1.0) * WA[o][w0]          # 另一個取樣點此刻的延遲
            if r >= 1:   # 升調：延遲遞減，換相時跳到最大延遲
                nd = od + Wn / 2
                p0 = min(max(nd / Wn, 0.9), 0.9999)
            else:        # 降調：延遲遞增，換相時跳到最小延遲
                nd = od - Wn / 2
                p0 = min(max(nd / Wn, 0.0), 0.1)
            ph[w0:] = p0 + (1 - r) / Wn * np.arange(n - w0)
            WA[tap][w0:] = Wn
            self.tW[tap] = Wn
        s = np.zeros(n)
        gsum = np.zeros(n)
        for tap in (0, 1):
            ph = PH[tap] % 1.0
            g = 0.5 - 0.5 * np.cos(2 * np.pi * ph)
            pos = idx - (ph * WA[tap] + 3)
            i1 = np.floor(pos).astype(np.int64)
            t = pos - i1
            y0, y1, y2, y3 = ext[i1 - 1], ext[i1], ext[i1 + 1], ext[i1 + 2]
            # 4 點 Hermite 內插（比線性內插乾淨）
            c1 = 0.5 * (y2 - y0)
            c2 = y0 - 2.5 * y1 + 2 * y2 - 0.5 * y3
            c3 = 0.5 * (y3 - y0) + 1.5 * (y1 - y2)
            s += g * (((c3 * t + c2) * t + c1) * t + y1)
            gsum += g
            self.tph[tap] = float((PH[tap][-1] + (1 - r) / WA[tap][-1]) % 1.0)
        self.hist = ext[-self.L:]
        # 兩個取樣點長度不同時增益和不為 1，正規化避免音量起伏（會聽起來像雜訊）
        s = s / np.maximum(gsum, 0.25)
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


# ---------------------------------------------------------------- 整合
class VoiceFx:
    CARD_SHIFT = 1.5  # 聲卡變音的升調量（由原聲／說話二比對量測）

    def __init__(self, sr=48000, low_latency=False):
        self.sr = sr
        self.tuner = Tuner(sr, 12.0 if low_latency else 21.0)
        self.p = dict(
            phone=False, phone_dist=0.25, machine=False,
            eq_lo=0.0, eq_mid=0.0, eq_hi=0.0, comp=0.4, verb=0.15, card=False,
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
        self.verb = Reverb(sr)
        self.info = (0.0, None, None)
        self.set()

    def set(self, **kw):
        tuner_keys = {"mode", "key", "scale", "strength", "speed", "shift", "robot"}
        tk = {k: v for k, v in kw.items() if k in tuner_keys}
        if "card" in kw:
            tk["card_shift"] = self.CARD_SHIFT if kw["card"] else 0.0
        if tk:
            self.tuner.update(**tk)
        for k, v in kw.items():
            if k in self.p:
                self.p[k] = v
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
        for f in self.eq:
            y = f(y)
        y = self.comp.process(y)
        v = self.p["verb"]
        if v > 0.001:
            y = y + self.verb.process(y) * (v * 0.8)
        else:
            self.verb.process(y)  # 保持尾巴狀態連續
        y = np.clip(y, -1.0, 1.0)
        return y[:n0].astype(np.float32)
