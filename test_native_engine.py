"""底層引擎整條流程測試（用 miniaudio 的空裝置，不需要真的聲卡；用內建測試音代替麥克風）。"""
import os
import sys
import tempfile
import time
import wave

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import native_engine as ne

ne.LIB.papa_use_null_backend(1)
fails = 0


def check(name, ok, d=""):
    global fails
    fails += (not ok)
    print(("PASS  " if ok else "FAIL  ") + name + ("  " + d if d else ""))


ins, outs = ne.list_devices()
check("讀得到裝置清單", len(ins) >= 1 and len(outs) >= 1, "%s / %s" % (ins, outs))
e = ne.Engine()
e.set_params(mode="off", card=True, comp=0.0, verb=0.0, deess=0.4, color="原本")
for g in ("inp", "mon", "out", "bgm"):
    e.set_gain(g, 1.0)
ne.LIB.papa_set(b"test_tone_hz", 220.0)
e.start(ins[0][0], outs[0][0], outs[0][0], block=128, backing=outs[0][1])
check("引擎啟動", e.running)
time.sleep(1.5)
lv = e.lv
check("麥克風、耳機、直播三條都有聲音", lv["inp"] > 0.1 and lv["mon"] > 0.1 and lv["out"] > 0.1, str({k: round(v, 3) for k, v in lv.items()}))
f, det, tgt = e.info
check("偵測到音高（測試音 220Hz）", abs(f - 220) < 3, "%.1f Hz" % f)
check("聲卡變音生效（移動 +1.5 半音）", abs(e.fx.tuner.cur - 1.5) < 0.05, "%.2f" % e.fx.tuner.cur)
check("延遲估計合理（< 80 ms）", 0 < e.latency_ms < 80, "%.1f ms" % e.latency_ms)

# 即時改參數：開電音
e.set_params(mode="electro", strength=1.0, speed=0.0)
time.sleep(0.5)
check("即時切到電音（有目標音）", e.info[2] is not None, str(e.info))

# 錄音 → 檢查錄到的聲音是升調後的音高
e.start_recording()
time.sleep(1.2)
secs = e.stop_recording()
check("錄音約 1.2 秒", 1.0 < secs < 1.5, "%.2f s" % secs)
wet = e._rec("wet")
dry = e._rec("dry")


def est(x):
    x = x[len(x) // 4: len(x) // 4 + 8192].astype(float)
    lo, hi = 48000 // 900, 48000 // 70
    ac = np.array([np.dot(x, x) / len(x)] + [np.dot(x[:-t], x[t:]) / (len(x) - t) for t in range(1, hi + 2)])
    b = lo + int(np.argmax(ac[lo:hi + 1]))
    for t in range(lo, b):
        if ac[t] > 0.85 * ac[b] and ac[t] >= ac[t - 1] and ac[t] >= ac[t + 1]:
            b = t
            break
    return 48000 / b


fw, fd = est(wet), est(dry)
check("錄到的原聲是 220Hz、直播出去的聲音是升調後", abs(fd - 220) < 4 and fw > 228, "原聲 %.1f／處理後 %.1f" % (fd, fw))
p = os.path.join(tempfile.gettempdir(), "papa_native.wav")
e.save_wav("wet", p)
with wave.open(p) as w:
    check("WAV 存檔正確", w.getframerate() == 48000 and w.getnframes() == len(wet))
check("可以在耳機播放錄音", e.play("wet") and e.playing)
time.sleep(0.3)
e.stop_play()
time.sleep(0.05)
check("停止播放", not e.playing)

# 暫存多段：取出錄音、播放任一段
take = e.get_recording("wet")
check("取出錄音（暫存用）", len(take) == len(wet))
check("播放暫存的某一段", e.play_array(take[:24000]) and e.playing)
time.sleep(0.8)
check("那一段播完自動停", not e.playing)

# 停止、再開（不同緩衝大小）
for blk in (64, 256, 512):
    e.stop()
    e.start(ins[0][0], outs[0][0], outs[0][0], block=blk)
    ok = False
    for _ in range(40):
        time.sleep(0.05)
        e._cache = None
        if e.lv["mon"] > 0.1 and e.lv["out"] > 0.1:
            ok = True
            break
    check("緩衝 %d 正常運作" % blk, ok)
# 伴奏緩衝：耳機每次要 480（Realtek 常見）時伴奏不能斷；舊的固定緩衝會斷（確認測試真的測得到）
for r, w in ((128, 128), (128, 480), (480, 480), (480, 128), (512, 480)):
    n_new = ne.LIB.papa_test_bgm_sim(128, r, w, 80.0, 30.0, 1)
    check("伴奏不斷：耳機每次 %d、伴奏每次 %d" % (r, w), n_new == 0, "斷 %d 次" % n_new)
check("舊的固定緩衝在 Realtek 情況會斷（測試有效）", ne.LIB.papa_test_bgm_sim(128, 480, 480, 80.0, 30.0, 0) > 0)
check("伴奏斷掉次數讀得到", e.bgm_underruns >= 0 and e.bgm_buffer_ms > 0, "%.1f ms" % e.bgm_buffer_ms)
check("沒有處理錯誤", ne.LIB is not None and e._st()[12] == 0)
e.stop()
check("停止後不再運作", ne.LIB.papa_running() == 0)
print("\n全部通過" if not fails else "\n%d 項失敗" % fails)
sys.exit(1 if fails else 0)
