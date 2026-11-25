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

# ---------- 換相時不爆音：每次重新開始的取樣點音量必須是 0
fx = VoiceFx(SR); fx.set(comp=0.0, verb=0.0, card=True)
xx = melody(np.arange(SR * 3) / SR).astype(np.float32)
bad = 0; cnt = 0
for s0 in range(0, len(xx) // 256 * 256, 256):
    pb = list(fx.tuner.tph); fx.process(xx[s0:s0 + 256])
    for tp in (0, 1):
        if abs(fx.tuner.tph[tp] - pb[tp]) > 0.3:
            cnt += 1
            # 回推換相當下的相位：升調時應接近 1（增益 0）
            if not (fx.tuner.tph[tp] > 0.95 or fx.tuner.tph[tp] < 0.05):
                bad += 1
check("換相時從 0 音量開始（不會啵）", cnt > 5 and bad == 0, f"換相 {cnt} 次，異常 {bad}")

# ---------- 電音質感層
x, o0, _ = run(noise, seconds=1.0, mode="chrom")
x, o1, _ = run(noise, seconds=1.0, mode="chrom", electro_fx=0.8)
seg0, seg1 = o0[SR // 2:SR // 2 + 16384], o1[SR // 2:SR // 2 + 16384]
hf = lambda s: band_energy(s, 8000, 14000) / band_energy(s, 500, 2000)
check("電音質感：高頻更亮", hf(seg1) > hf(seg0) * 1.5, f"{hf(seg1) / hf(seg0):.2f} 倍")
check("電音質感：輸出有效", np.all(np.isfinite(o1)) and np.max(np.abs(o1)) <= 1.0)

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

print("\n全部通過" if not fails else f"\n{fails} 項失敗")
sys.exit(1 if fails else 0)
