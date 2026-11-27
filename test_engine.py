"""用假的 sounddevice 測試引擎的資料流（不需要聲卡）。"""
import sys
import types
import numpy as np

SR = 48000
BLOCK = 256


class FakeStream:
    def __init__(self, **kw):
        self.kw = kw
        self.cb = kw["callback"]
        self.latency = 0.01
        self.started = False
        self.closed = False

    def start(self):
        self.started = True

    def stop(self):
        self.started = False

    def close(self):
        self.closed = True


fake = types.ModuleType("sounddevice")
fake.InputStream = FakeStream
fake.Stream = None   # 先測「分開的線」；後面再測「同一條線」
fake.OutputStream = FakeStream
fake.WasapiSettings = lambda **kw: ("wasapi", kw)
fake.query_devices = lambda: [
    {"name": "麥克風 (聲卡)", "max_input_channels": 2, "max_output_channels": 0, "hostapi": 0},
    {"name": "喇叭 (聲卡)", "max_input_channels": 0, "max_output_channels": 2, "hostapi": 0},
    {"name": "CABLE Input", "max_input_channels": 0, "max_output_channels": 2, "hostapi": 0},
    {"name": "MME 重複項", "max_input_channels": 2, "max_output_channels": 2, "hostapi": 1},
]
fake.query_hostapis = lambda: [{"name": "Windows WASAPI"}, {"name": "MME"}]
fake.default = types.SimpleNamespace(device=(0, 1))
sys.modules["sounddevice"] = fake

import engine  # noqa: E402

fails = 0


def check(name, ok, detail=""):
    global fails
    if not ok:
        fails += 1
    print(("PASS  " if ok else "FAIL  ") + name + ("  " + detail if detail else ""))


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
    return SR / best


# 裝置列表只列 WASAPI
ins, outs = engine.list_devices()
check("裝置清單只含 WASAPI 項目", [n for _, n in ins] == ["麥克風 (聲卡)"] and len(outs) == 2, str(outs))

eng = engine.Engine()
eng.set_params(mode="chrom", strength=1.0, speed=0.0, comp=0.0, verb=0.0)
eng.start(0, 1, 2, block=BLOCK)
check("三條串流都已建立並啟動", len(eng.streams) == 3 and all(s.started for s in eng.streams))
inp, mon, out = eng.streams

# 模擬 1.5 秒：每個區塊先呼叫輸入回呼，再呼叫兩個輸出回呼
t = np.arange(int(SR * 1.5) // BLOCK * BLOCK) / SR
sig = (0.3 * np.sin(2 * np.pi * 226 * t) + 0.15 * np.sin(4 * np.pi * 226 * t) + 0.08 * np.sin(6 * np.pi * 226 * t)).astype(np.float32)
mon_out, cable_out = [], []
for s in range(0, len(sig), BLOCK):
    indata = sig[s:s + BLOCK].reshape(-1, 1)
    inp.cb(indata, BLOCK, None, None)
    o1 = np.zeros((BLOCK, 2), np.float32)
    o2 = np.zeros((BLOCK, 2), np.float32)
    mon.cb(o1, BLOCK, None, None)
    out.cb(o2, BLOCK, None, None)
    mon_out.append(o1[:, 0])
    cable_out.append(o2[:, 0])
mon_out = np.concatenate(mon_out)
cable_out = np.concatenate(cable_out)
f1, f2 = est_freq(mon_out), est_freq(cable_out)
check("監聽輸出已吸附成 220Hz", abs(f1 - 220) < 3, f"{f1:.1f}")
check("直播輸出已吸附成 220Hz", abs(f2 - 220) < 3, f"{f2:.1f}")
check("沒有 underrun（資料流穩定）", eng.underruns <= 2, f"underruns={eng.underruns}")
check("回呼內沒有錯誤", getattr(eng, "last_error", None) is None, str(getattr(eng, "last_error", None)))
check("電平與音高資訊有更新", eng.level > 0.05 and eng.info[0] > 0)

# 即時改參數：關掉電音後，輸出應回到原本音高
eng.set_params(mode="off")
mon_out = []
for s in range(0, len(sig), BLOCK):
    inp.cb(sig[s:s + BLOCK].reshape(-1, 1), BLOCK, None, None)
    o1 = np.zeros((BLOCK, 2), np.float32); mon.cb(o1, BLOCK, None, None); mon_out.append(o1[:, 0])
    o2 = np.zeros((BLOCK, 2), np.float32); out.cb(o2, BLOCK, None, None)
f3 = est_freq(np.concatenate(mon_out))
check("執行中關掉電音：回到原音高 226Hz", abs(f3 - 226) < 3, f"{f3:.1f}")

# 錄音與回放
import os, tempfile, wave as _wave
eng.set_params(mode="chrom")
eng.start_recording()
for s in range(0, SR // BLOCK * BLOCK, BLOCK):  # 約 1 秒
    inp.cb(sig[s:s + BLOCK].reshape(-1, 1), BLOCK, None, None)
    for st in (mon, out):
        z = np.zeros((BLOCK, 2), np.float32); st.cb(z, BLOCK, None, None)
secs = eng.stop_recording()
check("錄音長度約 1 秒", abs(secs - (SR // BLOCK * BLOCK) / SR) < 0.01, f"{secs:.2f}s")
check("處理後錄音是 220Hz、原聲錄音是 226Hz",
      abs(est_freq(eng.rec_data["wet"]) - 220) < 3 and abs(est_freq(eng.rec_data["dry"]) - 226) < 3,
      f"wet={est_freq(eng.rec_data['wet']):.1f} dry={est_freq(eng.rec_data['dry']):.1f}")
tmp = os.path.join(tempfile.gettempdir(), "pa_test.wav")
eng.save_wav("wet", tmp)
with _wave.open(tmp) as w:
    ok = w.getframerate() == SR and w.getnchannels() == 1 and w.getnframes() == len(eng.rec_data["wet"])
check("WAV 存檔格式正確", ok)
check("可開始回放", eng.play("wet") and eng.playing)
played = []
for s in range(0, 2 * SR // BLOCK * BLOCK, BLOCK):  # 比錄音長，播完會自動停
    inp.cb(np.zeros((BLOCK, 1), np.float32), BLOCK, None, None)
    z = np.zeros((BLOCK, 2), np.float32); mon.cb(z, BLOCK, None, None); played.append(z[:, 0].copy())
    z2 = np.zeros((BLOCK, 2), np.float32); out.cb(z2, BLOCK, None, None)
played = np.concatenate(played)
check("回放內容 = 錄到的處理後聲音", abs(est_freq(played[:SR // 2 + 4096]) - 220) < 3 or abs(est_freq(played) - 220) < 6)
check("播完自動停止", not eng.playing)
eng.set_params(mode="off")

# 伴奏：直接混入，不被變音
eng.set_params(mode="off", card=True)
eng.bgm_ring = engine.Ring(BLOCK * 32)
eng.set_gain("bgm", 1.0)
tb = np.arange(len(sig)) / SR
bgm = (0.2 * np.sin(2 * np.pi * 500 * tb)).astype(np.float32)
mon_out, cab = [], []
for s in range(0, len(sig), BLOCK):
    eng.bgm_ring.write(bgm[s:s + BLOCK])
    inp.cb(sig[s:s + BLOCK].reshape(-1, 1), BLOCK, None, None)
    o1 = np.zeros((BLOCK, 2), np.float32); mon.cb(o1, BLOCK, None, None); mon_out.append(o1[:, 0])
    o2 = np.zeros((BLOCK, 2), np.float32); out.cb(o2, BLOCK, None, None); cab.append(o2[:, 0])
cab = np.concatenate(cab)[SR // 2:]
sp = np.abs(np.fft.rfft(cab * np.hanning(len(cab)))); fr = np.fft.rfftfreq(len(cab), 1 / SR)
pk = lambda f: sp[np.abs(fr - f) < 3].max()
check("伴奏 500Hz 原樣出現在直播輸出（沒被變音）", pk(500) > 10 * pk(500 * 2 ** (1.5 / 12)), f"{pk(500):.0f} vs {pk(500*2**(1.5/12)):.0f}")
check("人聲仍有聲卡變音（226→升 1.5 半音）", pk(226 * 2 ** (1.5 / 12)) > 5 * pk(226))
# 耳機伴奏：只有耳機裡的伴奏變大，直播（錄音）不變
def run_bgm(monbgm):
    eng.set_gain("monbgm", monbgm)
    eng.bgm_ring = engine.Ring(BLOCK * 32)
    m_, c_ = [], []
    for s in range(0, len(sig), BLOCK):
        eng.bgm_ring.write(bgm[s:s + BLOCK])
        inp.cb(np.zeros((BLOCK, 1), np.float32), BLOCK, None, None)
        o1 = np.zeros((BLOCK, 2), np.float32); mon.cb(o1, BLOCK, None, None); m_.append(o1[:, 0])
        o2 = np.zeros((BLOCK, 2), np.float32); out.cb(o2, BLOCK, None, None); c_.append(o2[:, 0])
    r = lambda x: float(np.sqrt(np.mean(np.concatenate(x)[SR // 2:] ** 2)))
    return r(m_), r(c_)
m1, c1 = run_bgm(1.0)
m2, c2 = run_bgm(2.0)
check("耳機伴奏 200%：耳機裡的伴奏變大", m2 > 1.6 * m1, "%.3f → %.3f" % (m1, m2))
check("耳機伴奏 200%：直播的伴奏不變", abs(c2 - c1) < 0.01 * max(c1, 1e-6) + 1e-4, "%.3f → %.3f" % (c1, c2))
eng.set_gain("monbgm", 1.0)
# 伴奏雜訊門：伴奏來源只有底噪／嗡嗡聲（-66 dB）時，直播和耳機都靜音
hum_only = (0.0005 * np.sin(2 * np.pi * 60 * np.arange(len(sig)) / SR)).astype(np.float32)
bgm_save = bgm
bgm = hum_only
mh, ch = run_bgm(1.0)
bgm = bgm_save
check("伴奏只有嗡嗡聲（沒在放歌）時：直播聽不到", ch < 1e-5, "%.6f" % ch)
# 耳機混響：只有耳機裡的混響變小，直播不變
eng.bgm_ring = None
eng.set_params(rv1_type=5, rv1_mix=0.6)
def run_rv(k):
    eng.set_gain("monrv", k)
    m_, c_ = [], []
    for s in range(0, len(sig), BLOCK):
        inp.cb(sig[s:s + BLOCK].reshape(-1, 1), BLOCK, None, None)
        o1 = np.zeros((BLOCK, 2), np.float32); mon.cb(o1, BLOCK, None, None); m_.append(o1[:, 0])
        o2 = np.zeros((BLOCK, 2), np.float32); out.cb(o2, BLOCK, None, None); c_.append(o2[:, 0])
    return np.concatenate(m_), np.concatenate(c_)
run_rv(1.0)                       # 先跑一次讓混響、緩衝都穩定
m_full, c_full = run_rv(1.0)
m_none, c_none = run_rv(0.0)
r_ = lambda x: float(np.sqrt(np.mean(x[SR // 2:] ** 2)))
db_mon = 20 * np.log10(r_(m_none) / r_(m_full)); db_out = 20 * np.log10(r_(c_none) / r_(c_full))
# 測試音是穩定的正弦波，混響和原音的相位會互相抵消一部分，所以拿掉混響不一定變小聲；只確認耳機有明顯改變
check("耳機混響 0%：耳機裡的混響拿掉了（耳機明顯改變）", abs(db_mon) > 1.0, "%.1f dB" % db_mon)
check("耳機混響 0%：直播的混響不變", abs(db_out) < 0.3, "%.2f dB" % db_out)
eng.set_gain("monrv", 1.0); eng.set_params(rv1_mix=0.0)
eng.bgm_on = False
inp.cb(np.zeros((BLOCK, 1), np.float32), BLOCK, None, None)
eng.bgm_ring = None
eng.set_params(card=False)

# 伴奏對齊：直播輸出的伴奏應延後 bgm_comp 個樣本，耳機的不延後
eng.set_params(mode="off", card=False)
eng.bgm_ring = engine.Ring(BLOCK * 32)
eng.bgm_on = True
eng.bgm_comp = 1000
eng.bgm_fifo = np.zeros(0)
eng.mon_ring = engine.Ring(BLOCK * 16); eng.out_ring = engine.Ring(BLOCK * 16)
imp = np.zeros(SR, np.float32); imp[5000] = 1.0
silent = np.zeros((BLOCK, 1), np.float32)
st_mix = []
for s in range(0, len(imp) // BLOCK * BLOCK, BLOCK):
    eng.bgm_ring.write(imp[s:s + BLOCK])
    eng.out_ring = engine.Ring(BLOCK * 16)
    inp.cb(silent, BLOCK, None, None)
    st_mix.append(eng.out_ring.buf[:BLOCK].copy())
st_mix = np.concatenate(st_mix)
d = int(np.argmax(np.abs(st_mix))) - 5000
check("直播裡伴奏延後量 = 計算值（與人聲對齊）", abs(d - 1000 - BLOCK * 3) <= BLOCK * 3 + 2, f"延後 {d} 樣本（含伴奏緩衝）")
eng.bgm_ring = None
eng.mon_ring = engine.Ring(BLOCK * 16); eng.out_ring = engine.Ring(BLOCK * 16)

# 限制器：太大聲時平滑壓下，不硬削波
from dsp import Limiter
lim = Limiter(SR)
tl = np.arange(SR) / SR
loud = 1.6 * np.sin(2 * np.pi * 300 * tl)
ol = np.concatenate([lim.process(loud[s:s + 256]) for s in range(0, SR, 256)])
seg = ol[SR // 2:]
sp = np.abs(np.fft.rfft(seg * np.hanning(len(seg)))); fr = np.fft.rfftfreq(len(seg), 1 / SR)
h1 = sp[np.abs(fr - 300) < 5].max(); h3 = sp[np.abs(fr - 900) < 5].max()
check("限制器：音量壓在 1 以下", np.max(np.abs(ol)) <= 0.9)
check("限制器：不會破音（3 次諧波 < -40 dB）", 20 * np.log10(h3 / h1) < -40, "%.0f dB" % (20 * np.log10(h3 / h1)))
hc = np.clip(loud, -1, 1)[SR // 2:]
sp2 = np.abs(np.fft.rfft(hc * np.hanning(len(hc))))
print("INFO  對照：硬削波的 3 次諧波 %.0f dB" % (20 * np.log10(sp2[np.abs(fr - 900) < 5].max() / sp2[np.abs(fr - 300) < 5].max())))

# 增益
eng.set_gain("mon", 1.0)
eng.set_gain("mon", 0.0)
for _ in range(8):   # 先把緩衝裡的舊聲音播完
    inp.cb(sig[:BLOCK].reshape(-1, 1), BLOCK, None, None)
    o1 = np.zeros((BLOCK, 2), np.float32); mon.cb(o1, BLOCK, None, None)
check("監聽音量 0 時監聽靜音", float(np.max(np.abs(o1))) < 1e-6)

# 停止
eng.stop()
check("停止後所有串流都關閉", all(s.closed for s in eng.streams + [inp, mon, out]) and not eng.running)

# 不使用直播輸出
eng2 = engine.Engine()
eng2.start(0, 1, None, block=BLOCK)
check("不接直播輸出時只開兩條串流", len(eng2.streams) == 2 and eng2.out_ring is None)
eng2.stop()

# 每種緩衝大小 × 全部效果都要能處理（防止自動加大緩衝後出錯）
full = dict(mode="electro", strength=1.0, speed=0.05, card=True, comp=0.5, verb=0.91, deess=0.4,
            phone=True, machine=True, electro_fx=0.6, color="人聲修飾", flex=0.2, humanize=0.6)
for blk in (128, 256, 512, 1024, 2048):
    e2 = engine.Engine(); e2.set_params(**full); e2.start(0, 1, 2, block=blk)
    i2, m2, o2 = e2.streams
    for s in range(0, SR // 2 // blk * blk, blk):
        i2.cb(sig[s:s + blk].reshape(-1, 1), blk, None, None)
    check("緩衝 %d：全部效果都正常輸出" % blk, e2.last_error is None and e2.lv["out"] > 0.01 and e2.lv["mon"] > 0.01,
          str(e2.last_error))
    e2.stop()

# 同一條線（duplex）：耳機直接輸出、不經過緩衝
class FakeDuplex(FakeStream):
    def __init__(self, **kw):
        super().__init__(**kw); self.latency = (0.01, 0.01)
fake.Stream = FakeDuplex
e3 = engine.Engine(); e3.set_params(mode="off", card=True, comp=0.0, verb=0.0); e3.start(0, 1, 2, block=256)
check("收音＋耳機合成同一條線", e3.duplex and len(e3.streams) == 2)
d3, c3 = e3.streams
mo = []
for s in range(0, len(sig) // 256 * 256, 256):
    od = np.zeros((256, 2), np.float32)
    d3.cb(sig[s:s + 256].reshape(-1, 1), od, 256, None, None)
    mo.append(od[:, 0].copy())
    oc = np.zeros((256, 2), np.float32); c3.cb(oc, 256, None, None)
mo = np.concatenate(mo)
check("同一條線：耳機有處理後的聲音（聲卡變音升調）", abs(est_freq(mo) - 226 * 2 ** (1.5 / 12)) < 4, "%.1f" % est_freq(mo))
check("同一條線：耳機沒有爆音計數", e3.underruns == 0)
# 直播那條來不及：只會自己加緩衝，不算耳機爆音
t0 = e3.cable_target
for _ in range(5):
    oc = np.zeros((256, 2), np.float32); c3.cb(oc, 256, None, None)
check("直播來不及時只加直播自己的緩衝", e3.cable_target > t0 and e3.underruns == 0, "%d → %d" % (t0, e3.cable_target))
e3.stop()
fake.Stream = None

# Ring 行為
r = engine.Ring(4096)
for k in range(6):
    r.write(np.arange(k * 100, k * 100 + 100, dtype=np.float32))
a = r.read(100, 500)
a2 = r.read(100, 500)
# 開始時存太多會先跳掉多的（只留 500），之後照順序、一個都不少
check("Ring 先進先出（開始時只留需要的量；第一段淡入）", abs(a[99] / 0.99 - 199) < 0.6 and abs(a2[0] - 200) < 0.6
      and abs(a2[99] - 299) < 0.6, f"{a[99]} {a2[0]} {a2[99]}")
r.write(np.arange(9000, dtype=np.float32))
check("Ring 超出容量時不會壞掉", r.n <= 4096)
r2 = engine.Ring(1024)
b = r2.read(64, 256)
check("Ring 未存夠時輸出 0（預先緩衝）", np.all(b == 0))
for k in range(6):
    r2.write(np.ones(100, np.float32))
r2.read(64, 256); r2.read(400, 256)
check("Ring 播到不夠時記錄 underrun", r2.underruns == 1)
# 時脈飄移：寫入比讀取快 0.3%，長時間下不應出現跳躍（爆音）
r3 = engine.Ring(1 << 15)
t_in = 0
outs = []
f = 440.0
for i in range(4000):
    nw = 257 if i % 3 == 0 else 256    # 平均快約 0.13%
    tt = (t_in + np.arange(nw)) / SR
    r3.write(np.sin(2 * np.pi * f * tt).astype(np.float32)); t_in += nw
    outs.append(r3.read(256, 512))
o = np.concatenate(outs)[SR:]
jump = np.max(np.abs(np.diff(o)))
check("時脈飄移時沒有跳躍（不會啵）", jump < 2 * np.pi * f / SR * 1.5 and r3.underruns == 0, f"最大差 {jump:.4f}, underruns={r3.underruns}")


# 對時緩衝不能讓音高抖：兩張卡的區塊時間錯開（寫 128、讀 480，回呼時間有抖動）
def ring_tone(r, w, ppm, secs=10.0, jit=0.0004, target=256):
    rng = np.random.default_rng(5)
    R = engine.Ring(1 << 16)
    pw, pr = w / SR, r / SR * (1 + ppm * 1e-6)
    kw = kr = 0
    tw, tr = pw, pr * 0.5 + 0.0003
    nin = 0
    outs = []
    while min(tw, tr) < secs:
        if tw <= tr:
            R.write(np.sin(2 * np.pi * 1000 * (nin + np.arange(w)) / SR).astype(np.float32))
            nin += w
            kw += 1
            tw = (kw + 1) * pw + rng.uniform(0, jit)
        else:
            outs.append(R.read(r, target))
            kr += 1
            tr = pr * 0.5 + 0.0003 + kr * pr + rng.uniform(0, jit)
    y = np.concatenate(outs).astype(np.float64)[SR * 5:]
    t = np.arange(len(y)) / SR
    z = np.convolve(y * np.exp(-2j * np.pi * 1000 * t), np.ones(48) / 48, mode="valid")
    fq = np.diff(np.unwrap(np.angle(z))) * SR / (2 * np.pi)
    win = SR // 4
    d = (fq - np.convolve(fq, np.ones(win) / win, mode="same"))[win:-win] / 1000 * 100
    return float(np.percentile(np.abs(d), 99.5)), float(np.min(np.abs(z[win:-win])))


for rr, ww in ((480, 128), (128, 480), (480, 480)):
    fl, amp = ring_tone(rr, ww, 60)
    check("對時緩衝音高不抖（讀 %d／寫 %d）" % (rr, ww), fl < 0.1 and amp > 0.45, "抖動 %.3f%%、最小音量 %.2f" % (fl, amp))

print("\n全部通過" if not fails else f"\n{fails} 項失敗")
sys.exit(1 if fails else 0)
