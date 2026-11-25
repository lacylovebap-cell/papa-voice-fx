"""怕怕調音台 - 即時音訊引擎（sounddevice）

麥克風 → VoiceFx → 兩路輸出：監聽（耳機）與直播（虛擬麥克風）。
兩路輸出各自用一條 Ring 緩衝，避免不同裝置的時脈不同步造成爆音。
"""
import threading
import wave
import numpy as np

try:
    import sounddevice as sd
except Exception as e:  # 讓 GUI 能顯示錯誤，而不是直接閃退
    sd = None
    SD_ERROR = e
else:
    SD_ERROR = None

from dsp import VoiceFx

SR = 48000


class Ring:
    """單一生產者/單一消費者的環形緩衝。超出時丟棄最舊的，不足時補 0。"""

    def __init__(self, cap):
        self.cap = cap
        self.buf = np.zeros(cap, np.float32)
        self.r = 0
        self.w = 0
        self.n = 0
        self.lock = threading.Lock()
        self.underruns = 0

    def write(self, x):
        with self.lock:
            m = len(x)
            if m >= self.cap:
                x = x[-self.cap:]
                m = self.cap
            over = self.n + m - self.cap
            if over > 0:
                self.r = (self.r + over) % self.cap
                self.n -= over
            idx = (self.w + np.arange(m)) % self.cap
            self.buf[idx] = x
            self.w = (self.w + m) % self.cap
            self.n += m

    def read(self, m, max_lag):
        with self.lock:
            if self.n > max_lag:  # 累積太多（延遲變大）→ 丟掉舊的
                drop = self.n - max_lag
                self.r = (self.r + drop) % self.cap
                self.n -= drop
            out = np.zeros(m, np.float32)
            k = min(m, self.n)
            if k:
                idx = (self.r + np.arange(k)) % self.cap
                out[:k] = self.buf[idx]
                self.r = (self.r + k) % self.cap
                self.n -= k
            if k < m:
                self.underruns += 1
            return out


def list_devices():
    """回傳 (輸入清單, 輸出清單)，每項 (index, 名稱)。Windows 只列 WASAPI，避免重複。"""
    if sd is None:
        return [], []
    devs = sd.query_devices()
    apis = sd.query_hostapis()
    wasapi = [i for i, a in enumerate(apis) if "WASAPI" in a["name"]]

    def pick(key):
        res = []
        for i, d in enumerate(devs):
            if d[key] > 0 and (not wasapi or d["hostapi"] in wasapi):
                res.append((i, d["name"]))
        return res

    return pick("max_input_channels"), pick("max_output_channels")


def default_devices():
    try:
        di, do = sd.default.device
        return (di if di is not None and di >= 0 else None), (do if do is not None and do >= 0 else None)
    except Exception:
        return None, None


class Engine:
    def __init__(self):
        self.fx = None
        self.streams = []
        self.running = False
        self.params = {}
        self.gains = dict(inp=1.0, mon=1.0, dry=0.0, out=1.0)
        self.level = 0.0
        self.info = (0.0, None, None)
        self.block = 256
        self.mon_ring = None
        self.out_ring = None
        self.latency_ms = 0.0
        self.status_msgs = 0
        # 錄音／回放
        self.recording = False
        self.rec_wet = []
        self.rec_dry = []
        self.rec_samples = 0
        self.rec_max = SR * 60 * 5  # 最長 5 分鐘
        self.rec_data = {"wet": None, "dry": None}
        self.play_data = None
        self.play_pos = 0

    # ---- 錄音／回放
    def start_recording(self):
        self.stop_play()
        self.rec_wet, self.rec_dry, self.rec_samples = [], [], 0
        self.recording = True

    def stop_recording(self):
        """停止錄音，回傳錄到的秒數。"""
        self.recording = False
        for k, lst in (("wet", self.rec_wet), ("dry", self.rec_dry)):
            self.rec_data[k] = np.concatenate(lst) if lst else None
        self.rec_wet, self.rec_dry = [], []
        d = self.rec_data["wet"]
        return 0.0 if d is None else len(d) / SR

    def has_recording(self):
        return self.rec_data["wet"] is not None

    def play(self, which="wet"):
        """在監聽（耳機）播放錄音。which: 'wet'＝處理後，'dry'＝原聲。"""
        d = self.rec_data.get(which)
        if d is None or not self.running:
            return False
        self.play_pos = 0
        self.play_data = d
        return True

    def stop_play(self):
        self.play_data = None
        self.play_pos = 0

    @property
    def playing(self):
        return self.play_data is not None

    def save_wav(self, which, path):
        d = self.rec_data.get(which)
        if d is None:
            raise RuntimeError("沒有錄音可存")
        pcm = (np.clip(d, -1.0, 1.0) * 32767).astype("<i2")
        with wave.open(path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(SR)
            w.writeframes(pcm.tobytes())

    # ---- 參數
    def set_params(self, **kw):
        self.params.update(kw)
        if self.fx is not None:
            self.fx.set(**kw)

    def set_gain(self, name, value):
        self.gains[name] = float(value)

    # ---- 回呼
    def _in_cb(self, indata, frames, time_info, status):
        if status:
            self.status_msgs += 1
        try:
            x = indata[:, 0].astype(np.float64) * self.gains["inp"]
            dry = x.astype(np.float32)
            y = self.fx.process(x)
            self.info = self.fx.info
            self.level = float(np.max(np.abs(y))) if len(y) else 0.0
            if self.recording and self.rec_samples < self.rec_max:
                self.rec_wet.append((y * self.gains["out"]).astype(np.float32))
                self.rec_dry.append(dry.copy())
                self.rec_samples += len(y)
            pd = self.play_data
            if pd is not None:
                # 回放中：耳機只聽錄音，不聽即時聲音（直播輸出不受影響）
                chunk = pd[self.play_pos:self.play_pos + len(y)]
                self.play_pos += len(y)
                mon = np.zeros(len(y), np.float32)
                mon[:len(chunk)] = chunk * self.gains["mon"]
                if self.play_pos >= len(pd):
                    self.play_data = None
            else:
                mon = y * self.gains["mon"] + dry * (self.gains["dry"] * self.gains["mon"])
            self.mon_ring.write(np.clip(mon, -1.0, 1.0).astype(np.float32))
            if self.out_ring is not None:
                self.out_ring.write((y * self.gains["out"]).astype(np.float32))
        except Exception as e:  # 回呼內不可丟例外，否則串流會停
            self.last_error = repr(e)

    def _make_out_cb(self, ring):
        def cb(outdata, frames, time_info, status):
            y = ring.read(frames, self.block * 3)
            outdata[:, 0] = y
            if outdata.shape[1] > 1:
                outdata[:, 1] = y
        return cb

    # ---- 啟動／停止
    def start(self, in_dev, mon_dev, out_dev, block=256, exclusive=False, low_latency=False):
        if sd is None:
            raise RuntimeError("找不到 sounddevice 套件：%s" % SD_ERROR)
        self.stop()
        self.block = int(block)
        self.last_error = None
        self.fx = VoiceFx(SR, low_latency)
        self.fx.set(**self.params)
        cap = self.block * 16
        self.mon_ring = Ring(cap)
        self.out_ring = Ring(cap) if out_dev is not None else None

        def extra(excl):
            try:
                return sd.WasapiSettings(exclusive=excl, auto_convert=not excl)
            except Exception:
                return None

        opened = []
        try:
            inp = sd.InputStream(device=in_dev, channels=1, samplerate=SR, blocksize=self.block,
                                 dtype="float32", latency="low", callback=self._in_cb,
                                 extra_settings=extra(exclusive))
            opened.append(inp)
            mon = sd.OutputStream(device=mon_dev, channels=2, samplerate=SR, blocksize=self.block,
                                  dtype="float32", latency="low", callback=self._make_out_cb(self.mon_ring),
                                  extra_settings=extra(exclusive))
            opened.append(mon)
            if out_dev is not None:
                out = sd.OutputStream(device=out_dev, channels=2, samplerate=SR, blocksize=self.block,
                                      dtype="float32", latency="low", callback=self._make_out_cb(self.out_ring),
                                      extra_settings=extra(False))
                opened.append(out)
            # 先啟動輸出，再啟動輸入
            for s in reversed(opened):
                s.start()
        except Exception:
            for s in opened:
                try:
                    s.close()
                except Exception:
                    pass
            raise
        self.streams = opened
        self.running = True
        try:
            self.latency_ms = (opened[0].latency + opened[1].latency) * 1000 + 1000.0 * self.block / SR
        except Exception:
            self.latency_ms = 0.0

    def stop(self):
        self.running = False
        self.recording = False
        self.play_data = None
        for s in self.streams:
            try:
                s.stop()
                s.close()
            except Exception:
                pass
        self.streams = []
        self.level = 0.0

    @property
    def underruns(self):
        n = 0
        for r in (self.mon_ring, self.out_ring):
            if r is not None:
                n += r.underruns
        return n
