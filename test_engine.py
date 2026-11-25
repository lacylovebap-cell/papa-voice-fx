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
eng.bgm_on = False
inp.cb(np.zeros((BLOCK, 1), np.float32), BLOCK, None, None)
eng.bgm_ring = None
eng.set_params(card=False)

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

# Ring 行為
r = engine.Ring(4096)
r.write(np.arange(1000, dtype=np.float32))
a = r.read(100, 500)
check("Ring 先進先出", abs(a[0]) <= 1 and abs(a[99] - 99) < 0.6, f"{a[0]} {a[99]}")
r.write(np.arange(9000, dtype=np.float32))
check("Ring 超出容量時不會壞掉", r.n <= 4096)
r2 = engine.Ring(1024)
b = r2.read(64, 256)
check("Ring 未存夠時輸出 0（預先緩衝）", np.all(b == 0))
r2.write(np.ones(400, np.float32)); r2.read(64, 256); r2.read(400, 256)
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

print("\n全部通過" if not fails else f"\n{fails} 項失敗")
sys.exit(1 if fails else 0)
