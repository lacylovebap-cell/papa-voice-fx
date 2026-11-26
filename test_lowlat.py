"""小緩衝／短變調視窗下，效果是否仍正確（延遲最低的設定）。"""
import sys, numpy as np
sys.path.insert(0, '.')
import native_fx
SR = 48000
fails = 0
def check(name, ok, d=""):
    global fails
    fails += (not ok); print(("PASS  " if ok else "FAIL  ") + name + ("  " + d if d else ""))
def est(o):
    x = o[len(o)//2:len(o)//2+8192].astype(float); lo, hi = SR//900, SR//70
    ac = np.array([np.dot(x, x)/len(x)] + [np.dot(x[:-t], x[t:])/(len(x)-t) for t in range(1, hi+2)])
    b = lo + int(np.argmax(ac[lo:hi+1]))
    for t in range(lo, b):
        if ac[t] > 0.85*ac[b] and ac[t] >= ac[t-1] and ac[t] >= ac[t+1]: b = t; break
    a0, b0, c0 = ac[b-1], ac[b], ac[b+1]; return SR/(b + (a0-c0)/(2*(a0-2*b0+c0)))
def hn(o, f):
    seg = o[SR:SR+65536].astype(float)*np.blackman(65536); sp = np.abs(np.fft.rfft(seg))**2; fr = np.fft.rfftfreq(65536, 1/SR)
    m = np.zeros_like(fr, bool)
    for k in range(1, int(8000/f)): m |= np.abs(fr-k*f) < 3
    bb = (fr > 80) & (fr < 8000); return 10*np.log10(sp[bb&m].sum()/sp[bb&~m].sum())
def pitch_sd(o, N=1024):
    ps = []
    for s in range(SR, len(o)-N, 240):
        seg = o[s:s+N].astype(float); ac = np.correlate(seg, seg, 'full')[N-1:]
        lo, hi = SR//500, SR//120; k = lo+np.argmax(ac[lo:hi]); a, b, c = ac[k-1], ac[k], ac[k+1]
        ps.append(1200*np.log2(SR/(k+(a-c)/(2*(a-2*b+c)))/220))
    return np.std(ps)
def run(x, block, win, **kw):
    fx = native_fx.VoiceFx(SR, window_ms=win); fx.set(comp=0.0, verb=0.0, **kw)
    n = len(x)//block*block
    return np.concatenate([fx.process(x[s:s+block]) for s in range(0, n, block)])
t = np.arange(int(SR*2.5))/SR
rich = lambda f: (0.3*np.sin(2*np.pi*f*t)+0.15*np.sin(4*np.pi*f*t)+0.08*np.sin(6*np.pi*f*t)).astype(np.float32)
fv = 220*2**((0.4*np.sin(2*np.pi*5.5*t))/12); pv = 2*np.pi*np.cumsum(fv)/SR
vib = (0.3*np.sin(pv)+0.15*np.sin(2*pv)).astype(np.float32)
import time
for block in (64, 128):
    for win in (12.0, 15.0, 21.0):
        tag = "區塊 %d、視窗 %.0f ms" % (block, win)
        o = run(rich(226), block, win, mode="chrom")
        check(tag + "：半音吸附 226→220", abs(est(o)-220) < 3, "%.1f" % est(o))
        o = run(rich(220), block, win, card=True)
        h = hn(o, 220*2**(1.5/12))
        check(tag + "：聲卡變音乾淨度 > 35 dB", h > 35, "%.0f dB" % h)
        o = run(vib, block, win, mode="chrom")
        sd = pitch_sd(o)
        check(tag + "：電音壓平顫音（< 1/3）", sd < 28/3, "28 → %.1f cents" % sd)
# 速度：全效果、64 區塊
fx = native_fx.VoiceFx(SR, window_ms=15.0)
fx.set(mode="electro", card=True, comp=0.5, verb=0.5, deess=0.4, electro_fx=0.9, metal=1.0, phone=True, machine=True, color="人聲修飾")
x = rich(226); t0 = time.perf_counter(); nb = 0
for s in range(0, len(x)//64*64, 64):
    fx.process(x[s:s+64]); nb += 1
ms = (time.perf_counter()-t0)/nb*1000
check("全效果、64 區塊：每區塊處理時間遠小於區塊長度（1.33 ms）", ms < 0.4, "%.3f ms（含 Python 呼叫成本）" % ms)
print("\n全部通過" if not fails else "\n%d 項失敗" % fails)
sys.exit(1 if fails else 0)
