"""離線測試 DSP 核心（不需要聲卡）。執行：python test_dsp.py"""
import sys
import time
import numpy as np
from dsp import VoiceFx, Tuner, note_name, KEY_NAMES

SR = 48000
BLOCK = 256
fails = 0


def run(sig, seconds=1.5, block=BLOCK, **params):
    fx = VoiceFx(SR)
    fx.set(comp=0.0, verb=0.0, **params)  # 先關掉壓縮與混響，只測電音本身
    total = int(SR * seconds) // block * block
    x = sig(np.arange(total) / SR).astype(np.float32)
    out = np.zeros(total, np.float32)
    for s in range(0, total, block):
        out[s:s + block] = fx.process(x[s:s + block])
    return x, out, fx


def est_freq(sig):
    a = len(sig) // 2
    x = sig[a:a + 8192].astype(np.float64)
    lo, hi = SR // 900, SR // 70
    ac = np.array([np.dot(x, x) / len(x)] + [np.dot(x[:-t], x[t:]) / (len(x) - t) for t in range(1, hi + 2)])
    best = lo + int(np.argmax(ac[lo:hi + 1]))
    for t in range(lo, best):
        if ac[t] > 0.85 * ac[best] and ac[t] >= ac[t - 1] and ac[t] >= ac[t + 1]:
            best = t
            break
    a0, b0, c0 = ac[best - 1], ac[best], ac[best + 1]
    den = a0 - 2 * b0 + c0
    tr = best + (a0 - c0) / (2 * den) if abs(den) > 1e-12 else best
    return SR / tr


def rich(f):
    return lambda t: 0.3 * np.sin(2 * np.pi * f * t) + 0.15 * np.sin(4 * np.pi * f * t) + 0.08 * np.sin(6 * np.pi * f * t)


def hz(m):
    return 440 * 2 ** ((m - 69) / 12)


def check(name, ok, detail=""):
    global fails
    if not ok:
        fails += 1
    print(("PASS  " if ok else "FAIL  ") + name + ("  " + detail if detail else ""))


def near(name, got, want, tol):
    check(name, abs(got - want) <= tol and np.isfinite(got), f"got={got:.1f} want={want:.1f}")


def band_energy(sig, lo, hi):
    sp = np.abs(np.fft.rfft(sig * np.hanning(len(sig)))) ** 2
    fr = np.fft.rfftfreq(len(sig), 1 / SR)
    return float(sp[(fr >= lo) & (fr < hi)].sum())


# ---------- 電音
_, o, _ = run(rich(226), mode="chrom", strength=1.0, speed=0.0)
near("半音吸附 226→220Hz", est_freq(o), 220, 3)
_, o, _ = run(rich(hz(64) * 1.015), mode="scale", key=0, scale="major", strength=1.0)
near("C 大調 E4 偏高 1.5% → E4", est_freq(o), hz(64), 4)
_, o, _ = run(rich(hz(61) * 1.01), mode="scale", key=0, scale="major", strength=1.0)
f = est_freq(o)
check("C 大調 C#4 吸到大調音內的音", abs(f - hz(60)) < 4 or abs(f - hz(62)) < 4, f"got={f:.1f}")
_, o, _ = run(rich(250), mode="robot", robot=57, strength=1.0)
near("機器人 250Hz → 220Hz", est_freq(o), 220, 4)
_, o, _ = run(rich(200), mode="robot", robot=57, strength=1.0)
near("機器人 200Hz → 220Hz", est_freq(o), 220, 4)
_, o, _ = run(rich(226), mode="off", shift=0)
near("關閉時頻率不變", est_freq(o), 226, 2)
_, o, _ = run(rich(220), mode="off", shift=12)
near("升八度 220→440Hz", est_freq(o), 440, 10)
x, o, _ = run(rich(226), mode="chrom", strength=1.0)
ratio = np.sqrt(np.mean(o[SR:] ** 2) / np.mean(x[SR:] ** 2))
check("電音輸出音量正常", 0.6 < ratio < 1.6, f"音量比={ratio:.2f}")
check("無 NaN/爆音", np.all(np.isfinite(o)) and np.max(np.abs(o)) <= 1.0)
_, o, _ = run(lambda t: np.zeros_like(t), mode="chrom", strength=1.0, seconds=0.5)
check("靜音輸入 → 靜音輸出", float(np.max(np.abs(o))) < 1e-6)

# 128 區塊也要正常
_, o, _ = run(rich(226), mode="chrom", strength=1.0, block=128)
near("區塊 128 也能吸附 226→220Hz", est_freq(o), 220, 3)

# 隨時間變化的旋律：階梯式吸附
def melody(t):
    notes = [57, 59, 61, 62, 64]
    seg = (t * 4).astype(int) % len(notes)
    f = hz(np.array(notes)[seg]) * 1.012
    ph = 2 * np.pi * np.cumsum(f) / SR
    return 0.3 * np.sin(ph) + 0.15 * np.sin(2 * ph)

x, o, fx = run(melody, mode="chrom", strength=1.0, seconds=2.0)
check("旋律輸入不會輸出異常", np.all(np.isfinite(o)))

# ---------- 電話音：頻帶外應被大幅衰減
rng = np.random.default_rng(0)
noise = lambda t: rng.standard_normal(len(t)) * 0.2
x, o, _ = run(noise, phone=True, seconds=1.0)
seg_i, seg_o = x[SR // 2:SR // 2 + 16384], o[SR // 2:SR // 2 + 16384]
low_ratio = band_energy(seg_o, 20, 150) / band_energy(seg_i, 20, 150)
mid_ratio = band_energy(seg_o, 800, 2000) / band_energy(seg_i, 800, 2000)
hi_ratio = band_energy(seg_o, 6000, 12000) / band_energy(seg_i, 6000, 12000)
check("電話音：低頻被削掉", low_ratio < mid_ratio * 0.05, f"低/中={low_ratio / mid_ratio:.3f}")
check("電話音：高頻被削掉", hi_ratio < mid_ratio * 0.05, f"高/中={hi_ratio / mid_ratio:.3f}")

# ---------- 答錄機音（乾淨版）
x, o, _ = run(rich(300), machine=True, seconds=1.0)
check("答錄機音：輸出有效", np.all(np.isfinite(o)) and np.sqrt(np.mean(o[SR // 2:] ** 2)) > 0.02)
x, o, _ = run(noise, machine=True, seconds=1.0)
seg_o = o[SR // 2:SR // 2 + 16384]; seg_i = x[SR // 2:SR // 2 + 16384]
r = lambda lo, hi: band_energy(seg_o, lo, hi) / band_energy(seg_i, lo, hi)
check("答錄機音：低頻削掉", r(50, 200) < r(1000, 2000) * 0.03, f"{r(50,200)/r(1000,2000):.4f}")
check("答錄機音：高頻削掉", r(7000, 12000) < r(1000, 2000) * 0.03, f"{r(7000,12000)/r(1000,2000):.4f}")
# 不失真：純音進去，諧波不應增加
x, o, _ = run(lambda t: 0.4 * np.sin(2 * np.pi * 1000 * t), machine=True, seconds=1.0)
seg = o[SR // 2:SR // 2 + 16384].astype(np.float64)
h1, h3 = band_energy(seg, 950, 1050), band_energy(seg, 2950, 3050)
check("答錄機音：不失真（3 次諧波 < -50dB）", 10 * np.log10(h3 / h1) < -50, f"{10*np.log10(h3/h1):.0f} dB")

# ---------- 聲卡變音音色：200Hz 降約 6dB、3.2kHz 降約 5dB、1kHz 幾乎不變
def band_gain(lo, hi, **kw):
    # 直接量聲卡變音的 EQ 濾波器（不含變調）
    from dsp import VoiceFx as _V
    fx = _V(SR)
    x = rng.standard_normal(SR).astype(np.float64) * 0.2
    y = x.copy()
    for f in fx.card_eq:
        y = f(y)
    a, b = x[SR // 4:SR // 4 + 16384], y[SR // 4:SR // 4 + 16384]
    return 10 * np.log10(band_energy(b, lo, hi) / band_energy(a, lo, hi))
g200, g1k, g32 = band_gain(150, 260, card=True), band_gain(900, 1100, card=True), band_gain(3000, 3400, card=True)
check("聲卡變音：中低頻約 -6dB", -8 < g200 < -4, f"{g200:.1f} dB")
check("聲卡變音：1kHz 幾乎不變", -1.5 < g1k < 1.0, f"{g1k:.1f} dB")
check("聲卡變音：3.2kHz 約 -5dB", -6.5 < g32 < -3, f"{g32:.1f} dB")
_, o, _ = run(rich(220), mode="off", card=True)
near("聲卡變音：本身就升 1.5 半音 220→239.8Hz", est_freq(o), 220 * 2 ** (1.5 / 12), 4)
# 鎖定：開著聲卡變音時，任何模式都不會回到原音高
_, o, _ = run(rich(hz(57) * 1.004), mode="chrom", strength=1.0, card=True)
f = est_freq(o)
check("聲卡變音＋電音：音高不是原音，且吸附在半音上", abs(f - hz(57)) > 5 and abs(12 * np.log2(f / 440) - round(12 * np.log2(f / 440))) < 0.15, f"got={f:.1f}")
for kw in (dict(phone=True), dict(machine=True), dict(mode="robot")):
    _, o, _ = run(rich(220), card=True, **kw)
    f = est_freq(o)
    check("聲卡變音鎖定＋%s：仍有變調" % list(kw)[0], abs(f - 220) > 4, f"got={f:.1f}")

# ---------- EQ
x, o, _ = run(noise, eq_lo=9.0, seconds=1.0)
gain = band_energy(o[SR // 2:SR // 2 + 16384], 20, 100) / band_energy(x[SR // 2:SR // 2 + 16384], 20, 100)
check("EQ 低頻 +9dB 有提升", gain > 3.0, f"能量比={gain:.1f}")

# ---------- 壓縮
def swell(t):
    return np.where((t % 1.0) < 0.5, 0.05, 0.6) * np.sin(2 * np.pi * 300 * t)

fx = VoiceFx(SR); fx.set(comp=0.0, verb=0.0)
xx = swell(np.arange(SR * 2) / SR).astype(np.float32)
def run_fx(fx, xx):
    out = np.zeros_like(xx)
    for s in range(0, len(xx) - BLOCK + 1, BLOCK):
        out[s:s + BLOCK] = fx.process(xx[s:s + BLOCK])
    return out
o0 = run_fx(fx, xx)
fx = VoiceFx(SR); fx.set(comp=0.9, verb=0.0)
o1 = run_fx(fx, xx)
rng_db = lambda s: 20 * np.log10(np.sqrt(np.mean(s[int(SR * 1.55):int(SR * 1.95)] ** 2)) / np.sqrt(np.mean(s[int(SR * 1.05):int(SR * 1.45)] ** 2)))
check("壓縮縮小了大小聲差距", abs(rng_db(o1)) < abs(rng_db(o0)) - 3, f"無壓縮{rng_db(o0):.1f}dB → 壓縮{rng_db(o1):.1f}dB")

# ---------- 混響：有尾巴且會衰減
fx = VoiceFx(SR); fx.set(comp=0.0, verb=1.0)
imp = np.zeros(SR * 2, np.float32); imp[100:356] = 0.5
o = run_fx(fx, imp)
e1 = np.sqrt(np.mean(o[SR // 4:SR // 2] ** 2)); e2 = np.sqrt(np.mean(o[int(SR * 1.5):] ** 2))
check("混響有尾巴且逐漸衰減", e1 > 1e-4 and e2 < e1, f"0.25~0.5s={e1:.4f} 1.5s後={e2:.5f}")

# ---------- 變調乾淨度：穩定音升 1.5 半音後，諧波以外的成分要很少
def hn(o, f):
    seg = o[SR:SR + 65536].astype(float) * np.blackman(65536)
    sp = np.abs(np.fft.rfft(seg)) ** 2; fr = np.fft.rfftfreq(65536, 1 / SR)
    m = np.zeros_like(fr, bool)
    for k in range(1, int(8000 / f)):
        m |= np.abs(fr - k * f) < 3
    bb = (fr > 80) & (fr < 8000)
    return 10 * np.log10(sp[bb & m].sum() / sp[bb & ~m].sum())
vals = []
for f0 in (150, 220, 330, 520):
    _, o, _ = run(rich(f0), seconds=2.5, card=True)
    vals.append(hn(o, f0 * 2 ** (1.5 / 12)))
check("變調乾淨度（諧波/雜訊 > 35 dB）", min(vals) > 35, " ".join("%.0f" % v for v in vals))

# ---------- 接合時不爆音：高頻（> 5 kHz）不能突然冒出尖峰
from scipy.signal import butter, sosfilt
def worst_click(**kw):
    fx = VoiceFx(SR); fx.set(comp=0.0, verb=0.0, **kw)
    xx = melody(np.arange(SR * 3) / SR).astype(np.float32)
    o = np.concatenate([fx.process(xx[s0:s0 + 256]) for s0 in range(0, len(xx) // 256 * 256, 256)]).astype(np.float64)
    hp = sosfilt(butter(6, 5000, "hp", fs=SR, output="sos"), o)
    e = np.sqrt(np.mean(hp[:len(hp) // 240 * 240].reshape(-1, 240) ** 2, 1))[20:]
    return 20 * np.log10(e.max() / np.sqrt(np.mean(o[SR // 2:] ** 2)) + 1e-12), fx.tuner.splices
for kw in (dict(card=True), dict(card=True, mode="chrom"), dict(card=True, mode="natural")):
    w, sp = worst_click(**kw)
    check("接合時不會啵（%s）" % "＋".join("%s=%s" % kv for kv in kw.items()), w < -45 and sp > 5, "最差 %.0f dB，接合 %d 次" % (w, sp))

# 說話 ↔ 電音 來回切換（讀取頭工作範圍會改變）也不能啵
fx = VoiceFx(SR); fx.set(comp=0.0, verb=0.0, card=True)
xx = melody(np.arange(SR * 3) / SR).astype(np.float32)
outs = []
for k, s0 in enumerate(range(0, len(xx) // 256 * 256, 256)):
    if k % 40 == 0:
        fx.set(mode="electro" if (k // 40) % 2 else "off")
    outs.append(fx.process(xx[s0:s0 + 256]))
o = np.concatenate(outs).astype(np.float64)
hp = sosfilt(butter(6, 5000, "hp", fs=SR, output="sos"), o)
e = np.sqrt(np.mean(hp[:len(hp) // 240 * 240].reshape(-1, 240) ** 2, 1))[20:]
w = 20 * np.log10(e.max() / np.sqrt(np.mean(o[SR // 2:] ** 2)) + 1e-12)
check("說話 ↔ 電音 來回切換不會啵", w < -45, "最差 %.0f dB" % w)

# ---------- 說話時（音高一直滑動）不能有細微電音／金屬感
def speechlike(sec=3.0, scale=1.0, seed=1):
    rg = np.random.default_rng(seed)
    n = int(SR * sec); tt = np.arange(n) / SR
    f0 = np.zeros(n)
    for k in range(int(sec / 0.25) + 1):
        a0, b0 = rg.uniform(180, 330, 2)
        i0, i1 = int(k * 0.25 * SR), min(n, int((k + 1) * 0.25 * SR))
        if i0 >= n:
            break
        f0[i0:i1] = np.linspace(a0, b0, i1 - i0)
    f0 = np.convolve(f0, np.ones(2400) / 2400, mode="same"); f0[:2400] = f0[2400]; f0[-2400:] = f0[-2401]
    f0 *= scale
    ph = 2 * np.pi * np.cumsum(f0) / SR
    xs = np.zeros(n)
    for k in range(1, 30):
        fk = k * f0
        amp = (1.0 / k) * (np.exp(-((fk - 750) / 250) ** 2) * 3 + np.exp(-((fk - 1250) / 300) ** 2) * 2 + np.exp(-((fk - 2600) / 400) ** 2) + 0.15)
        xs += amp * np.sin(k * ph) * (fk < SR / 2.2)
    xs *= 0.25 + 0.75 * (0.5 - 0.5 * np.cos(2 * np.pi * tt / 0.25))
    return (0.25 * xs / np.max(np.abs(xs))).astype(np.float32)
def flutter(xs, o):
    res = []
    for lo, hi in ((300, 600), (600, 1200), (1200, 2400), (2400, 4800)):
        sos = butter(4, [lo, hi], btype="band", fs=SR, output="sos")
        a0, b0 = sosfilt(sos, xs.astype(np.float64)), sosfilt(sos, o.astype(np.float64))
        ea = np.sqrt(np.mean(a0[:len(a0) // 480 * 480].reshape(-1, 480) ** 2, 1)) + 1e-6
        eb = np.sqrt(np.mean(b0[:len(b0) // 480 * 480].reshape(-1, 480) ** 2, 1)) + 1e-6
        dd = 20 * np.log10(eb / ea)[50:]
        sm = np.convolve(dd, np.ones(21) / 21, mode="same")
        res.append(np.std((dd - sm)[20:-20]))
    return float(np.mean(res))
xs = speechlike()
fx = VoiceFx(SR); fx.set(comp=0.0, verb=0.0, card=True)
os_ = np.concatenate([fx.process(xs[s0:s0 + 256]) for s0 in range(0, len(xs) // 256 * 256, 256)])
fl = flutter(xs[:len(os_)], os_)
check("說話模式＋變聲：沒有細微電音感（頻段抖動 < 1.0 dB，理想值約 0.7）", fl < 1.0, "%.2f dB" % fl)

# ---------- 電音質感層
x, o0, _ = run(noise, seconds=1.0, mode="chrom")
x, o1, _ = run(noise, seconds=1.0, mode="chrom", electro_fx=0.8)
seg0, seg1 = o0[SR // 2:SR // 2 + 16384], o1[SR // 2:SR // 2 + 16384]
hf = lambda s: band_energy(s, 8000, 14000) / band_energy(s, 500, 2000)
check("電音質感：高頻更亮", hf(seg1) > hf(seg0) * 1.5, f"{hf(seg1) / hf(seg0):.2f} 倍")
check("電音質感：輸出有效", np.all(np.isfinite(o1)) and np.max(np.abs(o1)) <= 1.0)
# 不自帶回音：短聲之後 100～400 ms 不應有明顯的回聲
fx = VoiceFx(SR); fx.set(comp=0.0, verb=0.0, mode="electro", electro_fx=0.9, metal=1.0)
imp = np.zeros(SR, np.float32); imp[1000:1480] = 0.5 * np.sin(2 * np.pi * 300 * np.arange(480) / SR)
oi = np.concatenate([fx.process(imp[s0:s0 + 256]) for s0 in range(0, SR // 256 * 256, 256)])
tail = np.sqrt(np.mean(oi[1480 + SR // 10:1480 + int(SR * 0.4)] ** 2)); head = np.sqrt(np.mean(oi[1000:1480] ** 2))
check("電音不自帶回音／混響（混響 0% 時）", 20 * np.log10(tail / head + 1e-12) < -40, "%.0f dB" % (20 * np.log10(tail / head + 1e-12)))

# ---------- 電音能把顫音壓平
tv = np.arange(SR * 3) / SR
fv = 220 * 2 ** ((0.4 * np.sin(2 * np.pi * 5.5 * tv)) / 12)
pv = 2 * np.pi * np.cumsum(fv) / SR
xv = (0.3 * np.sin(pv) + 0.15 * np.sin(2 * pv)).astype(np.float32)
fx = VoiceFx(SR); fx.set(comp=0.0, verb=0.0, mode="chrom", strength=1.0, speed=0.0)
ov = np.concatenate([fx.process(xv[s:s + 256]) for s in range(0, len(xv) // 256 * 256, 256)])
def pitch_sd(o, N=1024):
    ps = []
    for s in range(SR, len(o) - N, 240):
        seg = o[s:s + N].astype(float); ac = np.correlate(seg, seg, "full")[N - 1:]
        lo, hi = SR // 500, SR // 120; k = lo + np.argmax(ac[lo:hi]); a, b, c = ac[k - 1], ac[k], ac[k + 1]
        ps.append(1200 * np.log2(SR / (k + (a - c) / (2 * (a - 2 * b + c))) / 220))
    return np.std(ps)
sd_in, sd_out = pitch_sd(xv), pitch_sd(ov)
check("電音把顫音壓平（< 1/3）", sd_out < sd_in / 3, f"{sd_in:.0f} → {sd_out:.1f} cents")

# ---------- 自動判斷調（不用自己選調）
def tune(notes, dur=0.4, cents=0.0, vib=0.0, seconds=None):
    seq = []
    for nn in notes:
        k = int(SR * dur); tt = np.arange(k) / SR
        f = hz(nn) * 2 ** ((cents / 100 + vib * np.sin(2 * np.pi * 5.5 * tt)) / 12)
        seq.append(f)
    f = np.concatenate(seq); ph = 2 * np.pi * np.cumsum(f) / SR
    return (0.3 * np.sin(ph) + 0.15 * np.sin(2 * ph)).astype(np.float32)
amaj = [57, 59, 61, 62, 64, 66, 68, 69, 64, 61, 57, 62, 66, 69, 68, 64] * 2   # A 大調旋律
xa = tune(amaj)
fx = VoiceFx(SR); fx.set(comp=0.0, verb=0.0, mode="electro", strength=1.0, speed=0.0)
for s0 in range(0, len(xa) // 256 * 256, 256):
    fx.process(xa[s0:s0 + 256])
k = fx.tuner.keyt.key
check("邊唱邊判斷調：A 大調旋律 → A", k == 9, "判斷為 %s" % (KEY_NAMES[k] if k is not None else "未知"))

# ---------- 自然修音：保留顫音，只把音的中心拉準
def pitch_track(o, N=1024):
    ps = []
    for s0 in range(SR // 2, len(o) - N, 240):
        seg = o[s0:s0 + N].astype(float); ac = np.correlate(seg, seg, "full")[N - 1:]
        lo, hi = SR // 600, SR // 120; kk = lo + np.argmax(ac[lo:hi]); a, b, c = ac[kk - 1], ac[kk], ac[kk + 1]
        ps.append(1200 * np.log2(SR / (kk + (a - c) / (2 * (a - 2 * b + c))) / 440) + 6900)
    return np.array(ps)
xs = tune([57] * 8, dur=0.5, cents=35, vib=0.25)      # 偏高 35 cents 且有顫音的長音
fx = VoiceFx(SR); fx.set(comp=0.0, verb=0.0, mode="natural", strength=1.0, speed=0.1)
on = np.concatenate([fx.process(xs[s0:s0 + 256]) for s0 in range(0, len(xs) // 256 * 256, 256)])
fx = VoiceFx(SR); fx.set(comp=0.0, verb=0.0, mode="electro", strength=1.0, speed=0.0)
oe = np.concatenate([fx.process(xs[s0:s0 + 256]) for s0 in range(0, len(xs) // 256 * 256, 256)])
pi, pn, pe = pitch_track(xs), pitch_track(on), pitch_track(oe)
check("自然修音：音的中心被拉準", abs(np.median(pn) - 5700) < 12, "中心偏 %+.0f cents（原本 +35）" % (np.median(pn) - 5700))
check("自然修音：顫音保留（不會有機械感）", np.std(pn) > 0.7 * np.std(pi), "原 %.0f → %.0f cents" % (np.std(pi), np.std(pn)))
check("電音：顫音被壓平（電音感）", np.std(pe) < 0.5 * np.std(pi), "原 %.0f → %.0f cents" % (np.std(pi), np.std(pe)))

# ---------- Flex-Tune：離目標音很遠的（刻意表情）不修
xf = tune([57] * 6, dur=0.5, cents=45, vib=0.0)     # 偏 45 cents（很遠）
xn = tune([57] * 6, dur=0.5, cents=15, vib=0.0)     # 偏 15 cents（很近）
def center(x, **kw):
    fx = VoiceFx(SR); fx.set(comp=0.0, verb=0.0, mode="electro", strength=1.0, speed=0.0, **kw)
    o = np.concatenate([fx.process(x[s0:s0 + 256]) for s0 in range(0, len(x) // 256 * 256, 256)])
    return np.median(pitch_track(o)) - 5700
check("Flex-Tune 0：偏很遠也拉回", abs(center(xf, flex=0.0)) < 12)
check("Flex-Tune 高：偏很遠的放過", center(xf, flex=0.6) > 30, "%+.0f" % center(xf, flex=0.6))
check("Flex-Tune 高：偏很近的仍修準", abs(center(xn, flex=0.6)) < 8, "%+.0f" % center(xn, flex=0.6))

# ---------- Humanize：長音保留起伏，短音照樣修
xh = tune([57] * 4, dur=1.0, cents=0, vib=0.25)
def vib_after(x, **kw):
    fx = VoiceFx(SR); fx.set(comp=0.0, verb=0.0, mode="electro", strength=1.0, speed=0.0, **kw)
    o = np.concatenate([fx.process(x[s0:s0 + 256]) for s0 in range(0, len(x) // 256 * 256, 256)])
    return np.std(pitch_track(o))
v0, v1 = vib_after(xh, humanize=0.0), vib_after(xh, humanize=1.0)
check("Humanize：長音的自然起伏保留更多", v1 > v0 * 1.8, "%.0f → %.0f cents" % (v0, v1))

# ---------- 聲線 EQ
from dsp import VOICE_COLORS
for name in VOICE_COLORS:
    x, o, _ = run(noise, seconds=0.6, color=name)
    check("聲線「%s」輸出有效" % name, np.all(np.isfinite(o)) and np.max(np.abs(o)) <= 1.0)
x, o, _ = run(noise, seconds=1.0, color="少女甜嗓")
g2k = band_energy(o[SR // 2:SR // 2 + 16384], 1800, 2200) / band_energy(x[SR // 2:SR // 2 + 16384], 1800, 2200)
check("少女甜嗓：2kHz 提升", 10 * np.log10(g2k) > 3, "%.1f dB" % (10 * np.log10(g2k)))

# ---------- 去齒音：齒音被壓、母音不變
def sib_test(amt):
    tt = np.arange(SR * 2) / SR
    vowel = 0.3 * np.sin(2 * np.pi * 220 * tt) + 0.15 * np.sin(2 * np.pi * 440 * tt)
    from scipy.signal import butter, sosfilt
    nz = sosfilt(butter(4, [5500, 9000], "bandpass", fs=SR, output="sos"), np.random.default_rng(3).standard_normal(len(tt))) * 0.5
    gate = ((tt % 0.5) > 0.35).astype(float)       # 每 0.5 秒出現一次「ㄙ」
    x = (vowel * (1 - gate) + nz * gate).astype(np.float32)
    fx = VoiceFx(SR); fx.set(comp=0.0, verb=0.0, deess=amt)
    o = np.concatenate([fx.process(x[s0:s0 + 256]) for s0 in range(0, len(x) // 256 * 256, 256)])
    m = gate[:len(o)] > 0
    sib = np.sqrt(np.mean(o[SR // 2:][m[SR // 2:]] ** 2)); vow = np.sqrt(np.mean(o[SR // 2:][~m[SR // 2:]] ** 2))
    return sib, vow
s0, v0 = sib_test(0.0); s1, v1 = sib_test(0.7)
check("去齒音：齒音被壓下來", 20 * np.log10(s1 / s0) < -5, "%.1f dB" % (20 * np.log10(s1 / s0)))
check("去齒音：母音幾乎不變", abs(20 * np.log10(v1 / v0)) < 1.0, "%.2f dB" % (20 * np.log10(v1 / v0)))

# ---------- 混響力度：100% 要夠大（停掉後 0.1 秒殘響 > -8 dB）
fx = VoiceFx(SR); fx.set(comp=0.0, verb=1.0)
xr = np.zeros(SR * 2, np.float32); tr = np.arange(int(SR * 0.3)) / SR; xr[:len(tr)] = 0.4 * np.sin(2 * np.pi * 330 * tr)
orv = np.concatenate([fx.process(xr[s0:s0 + 256]) for s0 in range(0, len(xr) // 256 * 256, 256)])
ref = np.sqrt(np.mean(orv[:int(SR * 0.3)] ** 2)); aft = np.sqrt(np.mean(orv[int(SR * 0.4):int(SR * 0.41)] ** 2))
check("混響 100% 力度夠大", 20 * np.log10(aft / ref) > -8, "%.1f dB" % (20 * np.log10(aft / ref)))
check("混響 100% 仍會慢慢消失（不會無限延長）", np.sqrt(np.mean(orv[-4800:] ** 2)) < aft * 0.3)

# ---------- 爆電（金屬共鳴最強）：換音、停頓時不能有「啵啵啵」
def notes_with_gaps():
    n = SR * 3; xx = np.zeros(n)
    nts = [57, 61, 64, 59]
    for k in range(int(3 / 0.25)):
        i0 = int(k * 0.25 * SR); i1 = i0 + int(0.2 * SR)
        f = hz(nts[k % 4]); tt = np.arange(i1 - i0) / SR
        env = np.minimum(1, np.minimum(tt / 0.01, (tt[-1] - tt) / 0.01))
        xx[i0:i1] = (0.3 * np.sin(2 * np.pi * f * tt) + 0.12 * np.sin(4 * np.pi * f * tt)) * env
    return xx.astype(np.float32)
xg = notes_with_gaps()
fx = VoiceFx(SR); fx.set(comp=0.0, verb=0.0, card=True, mode="electro", strength=1.0, speed=0.0, electro_fx=0.9, metal=1.0)
og = np.concatenate([fx.process(xg[s0:s0 + 256]) for s0 in range(0, len(xg) // 256 * 256, 256)]).astype(np.float64)
jump = np.abs(np.diff(og))[SR // 2:].max() / np.abs(np.diff(xg.astype(np.float64))).max()
check("爆電：換音和停頓時波形不會突然跳（不會啵啵啵）", jump < 2.0, "最大跳動是原音的 %.2f 倍（舊版 7 倍）" % jump)

# ---------- 自然顫音（Auto-Tune Natural Vibrato）
def vib_std(db, mode="natural"):
    tv2 = np.arange(SR * 3) / SR
    fv2 = 220 * 2 ** ((0.3 * np.sin(2 * np.pi * 5.0 * tv2)) / 12)
    pv2 = 2 * np.pi * np.cumsum(fv2) / SR
    xv2 = (0.3 * np.sin(pv2) + 0.15 * np.sin(2 * pv2)).astype(np.float32)
    fx = VoiceFx(SR); fx.set(comp=0.0, verb=0.0, mode=mode, strength=1.0, speed=0.15, vibrato=db)
    o = np.concatenate([fx.process(xv2[s0:s0 + 256]) for s0 in range(0, len(xv2) // 256 * 256, 256)])
    return float(np.std(pitch_track(o)))
v0, vup, vdn = vib_std(0.0), vib_std(6.0), vib_std(-12.0)
check("自然顫音 +6 dB：顫音變大", vup > v0 * 1.5, "%.0f → %.0f cents" % (v0, vup))
check("自然顫音 -12 dB：顫音變小", vdn < v0 * 0.6, "%.0f → %.0f cents" % (v0, vdn))

# ---------- 多層混響：每一層都能細調
def rv_run(sec=2.0, **kw):
    fx = VoiceFx(SR); fx.set(comp=0.0, verb=0.0, **kw)
    burst = np.zeros(int(SR * sec), np.float32)
    rg = np.random.default_rng(3)
    burst[2000:4400] = (0.4 * rg.standard_normal(2400)).astype(np.float32)
    outs, sides = [], []
    for s0 in range(0, len(burst) // 256 * 256, 256):
        outs.append(fx.process(burst[s0:s0 + 256])); sides.append(np.array(fx.side[:256]))
    return burst, np.concatenate(outs).astype(np.float64), np.concatenate(sides).astype(np.float64)
def tail_db(o, t0, t1):
    a, b = 4400 + int(t0 * SR), 4400 + int(t1 * SR)
    return 10 * np.log10(np.mean(o[a:b] ** 2) + 1e-20)
_, o_room, _ = rv_run(rv1_type=2, rv1_mix=0.6, rv1_size=0.45)
_, o_church, _ = rv_run(rv1_type=5, rv1_mix=0.6, rv1_size=0.9)
check("混響類型：教堂的殘響比房間長很多", tail_db(o_church, 0.8, 1.2) - tail_db(o_room, 0.8, 1.2) > 15,
      "%.0f dB" % (tail_db(o_church, 0.8, 1.2) - tail_db(o_room, 0.8, 1.2)))
_, o_s1, _ = rv_run(rv1_type=4, rv1_mix=0.6, rv1_size=0.3)
_, o_s2, _ = rv_run(rv1_type=4, rv1_mix=0.6, rv1_size=0.95)
check("殘響長度：拉越大尾巴越長", tail_db(o_s2, 0.6, 1.0) - tail_db(o_s1, 0.6, 1.0) > 10)
_, ob1, _ = rv_run(rv1_type=4, rv1_mix=0.6, rv1_bright=1.0)
_, ob0, _ = rv_run(rv1_type=4, rv1_mix=0.6, rv1_bright=0.0)
hl = lambda o: band_energy(o[4400 + SR // 5:4400 + SR // 5 + 16384], 5000, 12000) / band_energy(o[4400 + SR // 5:4400 + SR // 5 + 16384], 300, 1500)
check("明亮度：調暗後尾巴的高頻少很多", hl(ob0) < hl(ob1) * 0.3, "%.3f 倍" % (hl(ob0) / hl(ob1)))
_, op0, _ = rv_run(rv1_type=3, rv1_mix=0.6, rv1_pre=0)
_, op8, _ = rv_run(rv1_type=3, rv1_mix=0.6, rv1_pre=80)
early = lambda o: tail_db(o, 0.002, 0.05)
check("預延遲：80 ms 時混響晚一點才出來", early(op8) < early(op0) - 15, "%.0f dB" % (early(op8) - early(op0)))
_, ow0, sw0 = rv_run(rv1_type=4, rv1_mix=0.6, rv1_width=0.0)
_, ow1, sw1 = rv_run(rv1_type=4, rv1_mix=0.6, rv1_width=1.0)
check("立體寬度 0：左右一樣", np.max(np.abs(sw0)) < 1e-6)
check("立體寬度 100：耳機左右有差別", np.sqrt(np.mean(sw1[4400:] ** 2)) > 0.1 * np.sqrt(np.mean(ow1[4400:] ** 2)))
check("立體寬度不影響直播（單聲道）的聲音", np.max(np.abs(ow0 - ow1)) < 1e-6)
_, ol0, _ = rv_run(rv1_type=4, rv1_mix=0.6, rv1_lowcut=0)
_, ol4, _ = rv_run(rv1_type=4, rv1_mix=0.6, rv1_lowcut=500)
lo = lambda o: band_energy(o[4400 + SR // 10:4400 + SR // 10 + 16384], 60, 200)
check("低頻清除：混響的低頻變少（比較不悶）", lo(ol4) < lo(ol0) * 0.2, "%.3f 倍" % (lo(ol4) / lo(ol0)))
_, o1l, _ = rv_run(rv1_type=4, rv1_mix=0.5)
_, o2l, _ = rv_run(rv1_type=4, rv1_mix=0.5, rv2_type=3, rv2_mix=0.5)
check("兩層混響疊加：比一層更濃", tail_db(o2l, 0.1, 0.4) > tail_db(o1l, 0.1, 0.4) + 1.5)
_, o_off, _ = rv_run(rv1_type=4, rv1_mix=0.0, rv2_type=3, rv2_mix=0.0)
check("混響全部 0：完全沒有尾巴", tail_db(o_off, 0.1, 0.5) < -120)
check("混響輸出有效", all(np.all(np.isfinite(o)) and np.max(np.abs(o)) <= 1.0 for o in (o_church, o2l, ob0, op8)))

# ---------- CD 質感：新混響類型、加寬、厚度、響度、清晰度
_, o_cls, _ = rv_run(rv1_type=1, rv1_mix=0.6)
from dsp import REVERB_TYPES as _RT, REVERB_NAMES as _RN
_sc, _sz, _br, _pr, _wd, _lc = _RT["CD 絲滑板式"]
_, o_cd, _ = rv_run(rv1_type=_RN.index("CD 絲滑板式") + 1, rv1_mix=0.6, rv1_size=_sz, rv1_bright=_br, rv1_pre=_pr, rv1_width=_wd, rv1_lowcut=_lc)
check("CD 絲滑板式：尾巴比經典暗很多（柔順）", hl(o_cd) < hl(o_cls) * 0.2, "%.3f 倍" % (hl(o_cd) / hl(o_cls)))
check("CD 絲滑板式：殘響夠長（約 2 秒，0.8 秒後還在）", tail_db(o_cd, 0.8, 1.0) > tail_db(o_cd, 0.0, 0.1) - 40)
def polish(sig, sec=3.0, **kw):
    fx = VoiceFx(SR); fx.set(comp=0.0, verb=0.0, **kw)
    xx = sig(np.arange(int(SR * sec)) / SR).astype(np.float32)
    outs, sides = [], []
    for s0 in range(0, len(xx) // 256 * 256, 256):
        outs.append(fx.process(xx[s0:s0 + 256])); sides.append(np.array(fx.side[:256]))
    return xx, np.concatenate(outs).astype(np.float64), np.concatenate(sides).astype(np.float64)
_, ow0, sw0 = polish(rich(220), widen=0.0)
_, ow1, sw1 = polish(rich(220), widen=1.0)
check("人聲加寬：耳機左右有寬度", np.sqrt(np.mean(sw1[SR:] ** 2)) > 0.05 * np.sqrt(np.mean(ow1[SR:] ** 2)))
check("人聲加寬：不影響直播（單聲道）的聲音", np.max(np.abs(ow0 - ow1)) < 1e-6)
_, ot1, _ = polish(rich(220), thick=1.0)
check("厚度：直播也聽得到、輸出有效", np.max(np.abs(ot1 - ow0)) > 0.01 and np.all(np.isfinite(ot1)) and np.max(np.abs(ot1)) <= 1.0)
quiet = lambda t: 0.018 * np.sin(2 * np.pi * 220 * t) + 0.008 * np.sin(4 * np.pi * 220 * t)
_, oq0, _ = polish(quiet, loud=0.0)
_, oq1, _ = polish(quiet, loud=1.0)
rms_db = lambda o: 20 * np.log10(np.sqrt(np.mean(o[2 * SR:] ** 2)) + 1e-12)
check("響度：小聲的人聲會被拉上來", rms_db(oq1) - rms_db(oq0) > 8, "%+.1f dB" % (rms_db(oq1) - rms_db(oq0)))
loudsig = lambda t: 0.6 * np.sin(2 * np.pi * 220 * t) + 0.3 * np.sin(4 * np.pi * 220 * t)
_, ol1, _ = polish(loudsig, loud=1.0)
check("響度：很大聲的會被壓住、不會破音", np.max(np.abs(ol1[SR:])) < 0.99 and rms_db(ol1) < rms_db(polish(loudsig, loud=0.0)[1]) - 2)
hiss = lambda t: 0.0003 * np.sin(2 * np.pi * 3000 * t)
_, on0, _ = polish(hiss, loud=0.0)
_, on1, _ = polish(hiss, loud=1.0)
check("響度：沒唱歌時不會把底噪放大", rms_db(on1) - rms_db(on0) < 1.0, "%+.1f dB" % (rms_db(on1) - rms_db(on0)))
def gain_at(f, **kw):
    fx0 = VoiceFx(SR); fx0.set(comp=0.0, verb=0.0)
    fx1 = VoiceFx(SR); fx1.set(comp=0.0, verb=0.0, **kw)
    t = np.arange(SR) / SR; xx = (0.1 * np.sin(2 * np.pi * f * t)).astype(np.float32)
    o0 = np.concatenate([fx0.process(xx[s0:s0 + 256]) for s0 in range(0, len(xx) // 256 * 256, 256)])
    o1 = np.concatenate([fx1.process(xx[s0:s0 + 256]) for s0 in range(0, len(xx) // 256 * 256, 256)])
    return 20 * np.log10(np.sqrt(np.mean(o1[SR // 2:] ** 2)) / np.sqrt(np.mean(o0[SR // 2:] ** 2)))
check("清晰度：空氣感（10 kHz 提升）", gain_at(10000, clarity=1.0) > 3, "%.1f dB" % gain_at(10000, clarity=1.0))
check("清晰度：去悶（250 Hz 降低）", gain_at(250, clarity=1.0) < -2, "%.1f dB" % gain_at(250, clarity=1.0))
check("清晰度 0：完全不影響", abs(gain_at(3000, clarity=0.0)) < 0.05)

# ---------- 延遲守門：任何功能都不能讓人聲變慢
def onset_ms(**kw):
    fx = VoiceFx(SR, low_latency=True); fx.tuner.set_window(15.0); fx.set(comp=0.0, verb=0.0, **kw)
    t = np.arange(SR) / SR
    xx = np.zeros(SR, np.float32)
    seg = slice(SR // 2, SR // 2 + 4800)
    xx[seg] = (0.4 * np.sin(2 * np.pi * 220 * t[:4800]) + 0.2 * np.sin(4 * np.pi * 220 * t[:4800])).astype(np.float32)
    o = np.concatenate([fx.process(xx[s0:s0 + 128]) for s0 in range(0, SR // 128 * 128, 128)]).astype(np.float64)
    env = np.abs(o)
    first = int(np.nonzero(env[SR // 2 - 2000:] > 0.1 * env.max())[0][0]) + SR // 2 - 2000
    return (first - SR // 2) / SR * 1000
polish_all = dict(loud=1.0, clarity=1.0, thick=1.0, widen=1.0, rv1_type=6, rv1_mix=0.5, rv1_pre=50, rv2_type=4, rv2_mix=0.5, deess=0.5)
check("人聲質感、混響、加寬全開：人聲本身完全不延遲", onset_ms(**polish_all) < 0.5, "%.2f ms" % onset_ms(**polish_all))
check("說話模式＋變聲：延遲 < 5 ms", onset_ms(card=True) < 5.0, "%.1f ms" % onset_ms(card=True))
check("電音＋變聲＋全部質感：延遲 < 5 ms", onset_ms(card=True, mode="electro", **polish_all) < 5.0,
      "%.1f ms" % onset_ms(card=True, mode="electro", **polish_all))

# ---------- 機器人聲：音高固定（像機器）＋環形調變
tg = np.arange(SR * 2) / SR
fg = 200 * 2 ** (np.linspace(0, 4, len(tg)) / 12)          # 一直往上滑 4 個半音
pg = 2 * np.pi * np.cumsum(fg) / SR
xg = (0.3 * np.sin(pg) + 0.15 * np.sin(2 * pg)).astype(np.float32)
fx = VoiceFx(SR); fx.set(comp=0.0, verb=0.0, mode="robot", robot=57, strength=1.0, speed=0.0, robot_fx=0.0)
og = np.concatenate([fx.process(xg[s0:s0 + 256]) for s0 in range(0, len(xg) // 256 * 256, 256)])
pa = est_freq(og[:SR]); pb = est_freq(og[SR:])
check("機器人：唱的音在滑，輸出音高固定不動", abs(pa - pb) < 4 and abs(pa - 220) < 5, "%.1f / %.1f Hz" % (pa, pb))
fx = VoiceFx(SR); fx.set(comp=0.0, verb=0.0, mode="robot", robot=57, strength=1.0, speed=0.0, robot_fx=1.0)
og2 = np.concatenate([fx.process(rich(220)(np.arange(SR * 2) / SR).astype(np.float32)[s0:s0 + 256]) for s0 in range(0, SR * 2 // 256 * 256, 256)])
sp = np.abs(np.fft.rfft(og2[SR:SR + 32768] * np.hanning(32768))); fr = np.fft.rfftfreq(32768, 1 / SR)
side = sp[np.abs(fr - 275) < 3].max() / sp[np.abs(fr - 220) < 3].max()
check("機器人：有金屬機械感（環形調變的邊頻）", side > 0.2, "邊頻/主音 %.2f" % side)
check("機器人：輸出有效", np.all(np.isfinite(og2)) and np.max(np.abs(og2)) <= 1.0)

# ---------- 全效果同時開 + 效能
fx = VoiceFx(SR)
fx.set(mode="scale", key=0, scale="major", strength=1.0, phone=True, machine=True, comp=0.5, verb=0.5, eq_lo=3, eq_mid=2, eq_hi=3)
xx = rich(226)(np.arange(SR * 3) / SR).astype(np.float32)
t0 = time.perf_counter()
o = run_fx(fx, xx)
el = time.perf_counter() - t0
per_block = el / (len(xx) // BLOCK) * 1000
check("全效果開啟：輸出有效", np.all(np.isfinite(o)) and np.max(np.abs(o)) <= 1.0)
check("全效果開啟：每區塊耗時遠低於區塊長度(5.3ms)", per_block < 2.5, f"每區塊 {per_block:.2f} ms")

# ---------- 降噪：沒出聲時把細小雜音壓掉，出聲時不動、不延遲
rng = np.random.default_rng(1)
hiss = (rng.standard_normal(SR * 2) * 10 ** (-60 / 20)).astype(np.float32)        # -60 dB 的沙沙聲
tone = (0.3 * np.sin(2 * np.pi * 220 * np.arange(SR) / SR)).astype(np.float32)
sig = np.concatenate([hiss, tone + hiss[:SR], hiss])
def run_gate(amt):
    f = VoiceFx(SR); f.set(comp=0.0, verb=0.0, gate=amt)
    return np.concatenate([f.process(sig[i:i + 256]) for i in range(0, len(sig) // 256 * 256, 256)])
o0, o1 = run_gate(0.0), run_gate(0.7)
rms = lambda a: float(np.sqrt(np.mean(a.astype(float) ** 2)) + 1e-12)
q0, q1 = rms(o0[SR:int(SR * 1.9)]), rms(o1[SR:int(SR * 1.9)])
check("降噪：沒出聲時雜音明顯變小（至少 15 dB）", 20 * np.log10(q0 / q1) > 15, "壓了 %.1f dB" % (20 * np.log10(q0 / q1)))
v0, v1 = rms(o0[int(SR * 2.1):int(SR * 2.9)]), rms(o1[int(SR * 2.1):int(SR * 2.9)])
check("降噪：出聲時音量不變", abs(20 * np.log10(v1 / v0)) < 0.3, "差 %.2f dB" % (20 * np.log10(v1 / v0)))
on = int(np.argmax(np.abs(o1[SR * 2 - 2048:]) > 0.05)); on0 = int(np.argmax(np.abs(o0[SR * 2 - 2048:]) > 0.05))
check("降噪：一出聲立刻打開（不慢於關閉時 3 ms）", on - on0 <= 144, "差 %d 樣本" % (on - on0))
check("降噪拉 0：和沒有降噪完全一樣", np.allclose(o0, run_gate(0.0)))

# ---------- 響度：沒出聲時不會把雜音越拉越大（底噪 -44 dB，以前會被拉高到 +12 dB）；C++／Python 都測
from dsp import Limiter
def run_loud(x, amt):
    f = VoiceFx(SR); f.set(comp=0.0, verb=0.0, deess=0.0, loud=amt)
    return np.concatenate([f.process(x[i:i + 256].astype(np.float32)) for i in range(0, len(x) - 255, 256)])
noise = rng.standard_normal(SR * 6) * 10 ** (-44 / 20)
g_late = 20 * np.log10(rms(run_loud(noise, 0.6)[-SR:]) / rms(run_loud(noise, 0.0)[-SR:]))
check("響度：只有雜音時不會越拉越大（6 秒後多不到 1 dB）", g_late <= 1.0, "%.1f dB" % g_late)
sing = 10 ** (-30 / 20) * np.sin(2 * np.pi * 300 * np.arange(SR * 3) / SR) * np.sqrt(2) + noise[:SR * 3]
g_sing = 20 * np.log10(rms(run_loud(sing, 0.6)[-SR:]) / rms(run_loud(sing, 0.0)[-SR:]))
check("響度：小聲唱歌時照樣會拉上來", g_sing > 3.0, "%.1f dB" % g_sing)
# ---------- 自然修音：容許誤差＝已經夠準的不去拉（不會有電音感）
def natural_out(cents, **kw):
    f0 = 220 * 2 ** (cents / 1200)
    tt = np.arange(SR * 2) / SR
    x = (0.3 * np.sin(2 * np.pi * f0 * tt) + 0.1 * np.sin(4 * np.pi * f0 * tt)).astype(np.float32)
    f = VoiceFx(SR); f.set(mode="natural", card=False, speed=0.3, flex=0.2, humanize=1.0, verb=0.0, comp=0.0, **kw)
    y = np.concatenate([f.process(x[i:i + 256]) for i in range(0, len(x) - 255, 256)])
    seg = y[SR:]; sp = np.abs(np.fft.rfft(seg * np.hanning(len(seg)))); fr = np.fft.rfftfreq(len(seg), 1 / SR)
    m = (fr > 150) & (fr < 300)
    return 1200 * np.log2(fr[m][np.argmax(sp[m])] / 220)
c10 = natural_out(10, strength=0.7, tol=0.2)
check("自然修音：只偏 10 音分（夠準）就不修，保留原本的味道", abs(c10 - 10) < 4, "%+.0f 音分" % c10)
c60 = natural_out(60, strength=0.7, tol=0.2)
check("自然修音：偏很多時拉近一部分，不會一口氣拉到死準", 60 < c60 < 95, "%+.0f 音分" % c60)

# ---------- 混響的部分另外記下來（耳機可以單獨調小）：人聲＝乾聲＋混響
f = VoiceFx(SR); f.set(comp=0.0, deess=0.0, rv1_type=4, rv1_mix=0.5)
xx = (0.3 * np.sin(2 * np.pi * 300 * np.arange(SR) / SR)).astype(np.float32)
outs, wets = [], []
for i in range(0, SR - 255, 256):
    outs.append(f.process(xx[i:i + 256])); wets.append(np.array(f.rv_wet[:256], np.float32))
wet = np.concatenate(wets)
check("混響的部分有記下來（給耳機混響用）", float(np.sqrt(np.mean(wet[SR // 2:] ** 2))) > 0.01)

# ---------- 去電流聲：60 Hz 嗡聲（含 120／180／240／300）壓掉，人聲幾乎不變
tt = np.arange(SR * 2) / SR
hum = sum(0.02 / (k + 1) * np.sin(2 * np.pi * 60 * (k + 1) * tt) for k in range(2))
vox = 0.2 * (np.sin(2 * np.pi * 240 * tt) + np.sin(2 * np.pi * 185 * tt) + np.sin(2 * np.pi * 330 * tt)) / 3   # 低音 B3／F#3、E4 也不能被吃掉
def run_hum(sig, on):
    f = VoiceFx(SR); f.set(comp=0.0, verb=0.0, deess=0.0, dehum=on)
    return np.concatenate([f.process(sig[i:i + 256].astype(np.float32)) for i in range(0, len(sig) - 255, 256)])
h0, h1 = rms(run_hum(hum, False)[SR:]), rms(run_hum(hum, True)[SR:])
check("去電流聲：嗡聲小 20 dB 以上", 20 * np.log10(h0 / h1) > 20, "%.1f dB" % (20 * np.log10(h0 / h1)))
v0, v1 = rms(run_hum(vox, False)[SR:]), rms(run_hum(vox, True)[SR:])
check("去電流聲：人聲幾乎不變（差不到 1 dB）", abs(20 * np.log10(v1 / v0)) < 1.0, "%.2f dB" % (20 * np.log10(v1 / v0)))

print("\n全部通過" if not fails else f"\n{fails} 項失敗")
sys.exit(1 if fails else 0)
