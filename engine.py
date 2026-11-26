"""PAPA 調音台 - 即時音訊引擎（sounddevice）

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

from dsp import VoiceFx, Limiter

try:
    import soundcard as sc   # 用來擷取「別的程式正在播的聲音」（WASAPI loopback）
except Exception as e:
    sc = None
    SC_ERROR = e
else:
    SC_ERROR = None

SR = 48000


def _now():
    import time
    return time.monotonic()


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
        self.frac = 0.0
        self.primed = False

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

    def read(self, m, target):
        """讀 m 個樣本。以極小幅度的重新取樣追蹤緩衝量，避免時脈飄移時整段丟棄或補零（會「啵」一聲）。"""
        with self.lock:
            if not self.primed:
                if self.n < target + m:          # 先存夠緩衝再開始播（只多約 2 個區塊延遲）
                    return np.zeros(m, np.float32)
                self.primed = True
            if self.n < m + 4:
                # 真的不夠：把剩下的淡出到 0，而不是突然變 0
                out = np.zeros(m, np.float32)
                k = min(max(0, self.n - 1), m)
                if k:
                    idx = (self.r + np.arange(k)) % self.cap
                    out[:k] = self.buf[idx] * np.linspace(1, 0, k)
                    self.r = (self.r + k) % self.cap
                    self.n -= k
                self.underruns += 1
                self.primed = False
                return out
            if self.n > target * 4:      # 累積過多（例如剛啟動）：一次性跳過，只會發生一次
                drop = self.n - target
                self.r = (self.r + drop) % self.cap
                self.n -= drop
            ratio = 1.0 + max(-0.005, min(0.005, (self.n - target) / max(target, 1) * 0.005))
            pos = self.frac + np.arange(m) * ratio
            need = int(np.floor(pos[-1])) + 2
            need = min(need, self.n)
            idx = (self.r + np.arange(need)) % self.cap
            seg = self.buf[idx]
            i0 = np.minimum(np.floor(pos).astype(np.int64), need - 2)
            fr = pos - i0
            out = (seg[i0] * (1 - fr) + seg[i0 + 1] * fr).astype(np.float32)
            adv = pos[-1] + ratio
            used = int(np.floor(adv))
            self.frac = adv - used
            used = min(used, self.n)
            self.r = (self.r + used) % self.cap
            self.n -= used
            return out


def list_backing_sources():
    """可以擷取伴奏的來源：每個輸出裝置的「正在播放的聲音」。回傳名稱清單。"""
    if sc is None:
        return []
    try:
        return [m.name for m in sc.all_microphones(include_loopback=True) if getattr(m, "isloopback", False)]
    except Exception:
        return []


class BackingCapture:
    """在背景執行緒擷取某個輸出裝置正在播的聲音（伴奏），放進 Ring。"""

    def __init__(self, name, ring, block):
        self.name, self.ring, self.block = name, ring, block
        self.running = False
        self.error = None
        self.level = 0.0
        self.max_chunk = 0
        self.th = None

    def start(self):
        if sc is None:
            raise RuntimeError("缺少 soundcard 套件：%s" % SC_ERROR)
        self.running = True
        self.th = threading.Thread(target=self._run, daemon=True)
        self.th.start()

    def _run(self):
        try:
            try:
                import ctypes
                ctypes.windll.ole32.CoInitializeEx(None, 0)   # 背景執行緒要先初始化 COM
            except Exception:
                pass
            mic = next(m for m in sc.all_microphones(include_loopback=True)
                       if getattr(m, "isloopback", False) and m.name == self.name)
            with mic.recorder(samplerate=SR, channels=2, blocksize=self.block) as rec:
                while self.running:
                    d = rec.record(numframes=self.block)
                    if d is None or len(d) == 0:
                        continue
                    self.max_chunk = max(self.max_chunk, len(d))
                    mono = d.mean(axis=1).astype(np.float32) if d.ndim > 1 else d.astype(np.float32)
                    self.level = float(np.max(np.abs(mono)))
                    self.ring.write(mono)
        except Exception as e:
            self.error = repr(e)
            self.running = False

    def stop(self):
        self.running = False
        if self.th is not None:
            self.th.join(timeout=1.0)
        self.th = None


def rescan():
    """重新讀取 Windows 的音效裝置（插拔裝置、安裝 VB-Cable 之後，舊的編號會失效）。"""
    if sd is None:
        return
    try:
        sd._terminate()
        sd._initialize()
    except Exception:
        pass


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
        self.gains = dict(inp=1.0, mon=1.0, dry=0.0, out=1.0, bgm=0.8)
        self.backing = None
        self.bgm_ring = None
        self.bgm_fifo = np.zeros(0)
        self.backing_error = None
        self.in_clip = 0.0
        self.bgm_on = True
        self.duplex = False
        self._mon_now = None
        self.xruns = 0
        self._t_started = None
        self.cable_target = 512
        self.bgm_target = 768
        self.bgm_underruns = 0
        self.out_target = 512          # 輸出緩衝目標（樣本）
        self.bgm_comp = 0              # 伴奏延後量（樣本），讓直播裡伴奏和人聲對齊
        self.bgm_trim_ms = 0.0         # 使用者微調
        self.duck_db = 0.0             # 伴奏避讓目前壓低的量
        self.bgm_fifo = np.zeros(0)
        self.level = 0.0
        self.lv = dict(inp=0.0, mon=0.0, out=0.0, bgm=0.0)   # 各管道音量（給畫面的跳動波形用）
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
        self.rec_voice = []
        self.rec_bgm = []
        self.rec_samples = 0
        self.rec_max = SR * 60 * 5  # 最長 5 分鐘
        self.rec_data = {"wet": None, "dry": None}
        self.play_data = None
        self.play_pos = 0

    # ---- 錄音／回放
    def start_recording(self):
        self.stop_play()
        self.rec_wet, self.rec_dry, self.rec_samples = [], [], 0
        self.rec_voice, self.rec_bgm = [], []
        self.recording = True

    def stop_recording(self):
        """停止錄音，回傳錄到的秒數。"""
        self.recording = False
        for k, lst in (("wet", self.rec_wet), ("dry", self.rec_dry), ("voice", self.rec_voice), ("bgm", self.rec_bgm)):
            self.rec_data[k] = np.concatenate(lst) if lst else None
        self.rec_wet, self.rec_dry, self.rec_voice, self.rec_bgm = [], [], [], []
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

    def get_recording(self, which):
        d = self.rec_data.get(which)
        return None if d is None else d.copy()

    def play_array(self, arr):
        if not self.running:
            return False
        self.play_pos = 0
        self.play_data = np.asarray(arr, np.float32)
        return True

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
            self.lv["inp"] = float(np.max(np.abs(indata[:, 0]))) if len(x) else 0.0
            if self.lv["inp"] > 0.97:          # 聲卡進來就已經太大聲（破音）
                self.in_clip = 1.0
            else:
                self.in_clip *= 0.995
            y = self.fx.process(x)
            self.info = self.fx.info
            self.level = float(np.max(np.abs(y))) if len(y) else 0.0
            # 伴奏：不經過任何變音處理，直接混進監聽與直播
            if self.bgm_ring is not None:
                # 伴奏緩衝跟著「耳機每次要多少」「伴奏每次送來多少」自動加大（Realtek 耳機常一次 480 個樣本）
                bk = self.backing
                w_max = bk.max_chunk if bk is not None else 0
                self.bgm_target = min(max(self.block * 3, len(y) * 2 + w_max, self.bgm_target), SR // 5)
                ub = self.bgm_ring.underruns
                bg = self.bgm_ring.read(len(y), self.bgm_target).astype(np.float64)
                if self.bgm_ring.underruns > ub:
                    self.bgm_underruns += 1
                    if self.bgm_target < SR // 10:
                        self.bgm_target += max(self.block, len(y))
                bg *= self.gains["bgm"] if self.bgm_on else 0.0
                # 伴奏延後：你是聽著耳機裡的伴奏唱的，人聲回到電腦時已經晚了「一圈延遲」，
                # 所以把伴奏同樣延後，直播裡兩者才會剛好對齊
                want = max(0, int(self.bgm_comp + self.bgm_trim_ms * SR / 1000))
                fifo = np.concatenate((self.bgm_fifo, bg))
                if len(fifo) < want + len(y):
                    fifo = np.concatenate((np.zeros(want + len(y) - len(fifo)), fifo))
                elif len(fifo) > want + len(y):
                    fifo = fifo[len(fifo) - (want + len(y)):]
                bg_mix = fifo[:len(y)]
                self.bgm_fifo = fifo[len(y):]
                bg_mon = bg          # 耳機裡的伴奏不延後（越早聽到越好）
                self.lv["bgm"] = float(np.max(np.abs(bg))) if len(bg) else 0.0
            else:
                bg_mix = bg_mon = 0.0
            # 伴奏避讓：依人聲大小把伴奏壓低一點
            duck = float(self.params.get("duck", 0.0))
            if self.bgm_ring is not None and (duck > 0.001 or abs(self.duck_db) > 1e-4):
                vdb = 20 * np.log10(np.sqrt(np.mean(y * y)) + 1e-9)
                k = min(1.0, max(0.0, (vdb + 45.0) / 20.0))
                want = -6.0 * duck * k
                tau = 0.02 if want < self.duck_db else 0.3
                nw = self.duck_db + (want - self.duck_db) * (1 - np.exp(-len(y) / (SR * tau)))
                g = 10 ** ((self.duck_db + (nw - self.duck_db) * np.arange(1, len(y) + 1) / len(y)) / 20)
                self.duck_db = float(nw)
                bg_mix = bg_mix * g
                bg_mon = bg_mon * g
            out_mix = self.lim_out.process(y * self.gains["out"] + bg_mix)
            if self.recording and self.rec_samples < self.rec_max:
                self.rec_wet.append(out_mix.astype(np.float32))
                self.rec_dry.append(dry.copy())
                self.rec_voice.append((y * self.gains["out"]).astype(np.float32))
                self.rec_bgm.append((np.zeros(len(y)) + bg_mix).astype(np.float32))
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
                mon = self.lim_mon.process((y + bg_mon) * self.gains["mon"] + dry * (self.gains["dry"] * self.gains["mon"]))
                if int(self.params.get("mon_mode", 0)) == 2:     # 試聽：手機外放
                    if getattr(self, "_phone_sim", None) is None:
                        from dsp import Biquad
                        self._phone_sim = [Biquad("hp", 380, SR, q=0.8), Biquad("hp", 380, SR, q=0.8),
                                           Biquad("peaking", 2800, SR, 4.0, q=1.0), Biquad("lp", 9000, SR, q=0.7)]
                    for f in self._phone_sim:
                        mon = f(mon)
                    mon = self.lim_mon.process(mon * 1.4)
            self.lv["mon"] = float(np.max(np.abs(mon))) if len(mon) else 0.0
            mon = np.clip(mon, -1.0, 1.0).astype(np.float32)
            if self.duplex:
                self._mon_now = mon                 # 同一條線：直接送到耳機，不經過緩衝
            else:
                self.mon_ring.write(mon)
            if self.out_ring is not None:
                o_ = out_mix
                self.lv["out"] = float(np.max(np.abs(o_))) if len(o_) else 0.0
                self.out_ring.write(o_.astype(np.float32))
        except Exception as e:  # 回呼內不可丟例外，否則串流會停
            self.last_error = repr(e)
            self.error_count = getattr(self, "error_count", 0) + 1
            if self.error_count == 1:
                try:
                    import traceback, os, datetime
                    base = os.environ.get("APPDATA") or os.path.expanduser("~")
                    os.makedirs(os.path.join(base, "PaPaVoiceFx"), exist_ok=True)
                    with open(os.path.join(base, "PaPaVoiceFx", "error.log"), "a", encoding="utf8") as f:
                        f.write("[%s] 處理錯誤\n%s\n" % (datetime.datetime.now().isoformat(timespec="seconds"), traceback.format_exc()))
                except Exception:
                    pass

    def _duplex_cb(self, indata, outdata, frames, time_info, status):
        """聲卡收音＋耳機輸出在同一條線（同一個時鐘）：處理完直接送耳機，延遲最低、不會因時鐘不同步爆音。"""
        if status and (getattr(status, "input_overflow", False) or getattr(status, "output_underflow", False)):
            if time_info is None or self._t_started is None or _now() - self._t_started > 1.5:
                self.xruns += 1
        self._mon_now = None
        self._in_cb(indata, frames, time_info, None)
        m = self._mon_now
        if m is None or len(m) != frames:
            outdata.fill(0)
            return
        outdata[:, 0] = m
        if outdata.shape[1] > 1:
            outdata[:, 1] = m

    def _cable_cb(self, outdata, frames, time_info, status):
        """送直播（CABLE）：不夠時只讓這條多存一點，耳機不受影響。"""
        r = self.out_ring
        before = r.underruns
        y = r.read(frames, self.cable_target)
        if r.underruns > before and self.cable_target < self.block * 8:
            self.cable_target += self.block
        outdata[:, 0] = y
        if outdata.shape[1] > 1:
            outdata[:, 1] = y

    def _make_out_cb(self, ring):
        def cb(outdata, frames, time_info, status):
            y = ring.read(frames, self.out_target)
            outdata[:, 0] = y
            if outdata.shape[1] > 1:
                outdata[:, 1] = y
        return cb

    # ---- 啟動／停止
    def start(self, in_dev, mon_dev, out_dev, block=256, exclusive=False, low_latency=False, backing=None, ultra=False):
        if sd is None:
            raise RuntimeError("找不到 sounddevice 套件：%s" % SD_ERROR)
        self.stop()
        self.block = int(block)
        self.last_error = None
        self.error_count = 0
        self.out_target = (self.block + 64) if ultra else self.block * 2
        self.fx = VoiceFx(SR, low_latency or ultra)
        self.status_msgs = 0
        self.lim_out = Limiter(SR)
        self.lim_mon = Limiter(SR)
        self.in_clip = 0.0
        self.fx.set(**self.params)
        cap = self.block * 16
        self.mon_ring = Ring(cap)
        self.out_ring = Ring(max(cap, self.block * 24)) if out_dev is not None else None
        self.cable_target = self.block * 2
        self.xruns = 0
        self._t_started = None
        self.duplex = False

        def extra(excl):
            try:
                return sd.WasapiSettings(exclusive=excl, auto_convert=not excl)
            except Exception:
                return None

        opened = []
        try:
            main = None
            try:
                # 優先：收音＋耳機同一條線
                main = sd.Stream(device=(in_dev, mon_dev), channels=(1, 2), samplerate=SR, blocksize=self.block,
                                 dtype="float32", latency="low", callback=self._duplex_cb,
                                 extra_settings=(extra(exclusive), extra(exclusive)))
                self.duplex = True
            except Exception:
                main = None
                self.duplex = False
            if main is not None:
                opened.append(main)
            else:
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
                                      dtype="float32", latency="low", callback=self._cable_cb,
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
        self._t_started = _now()
        self.running = True
        self.backing_error = None
        self.bgm_target = self.block * 3
        self.bgm_underruns = 0
        if backing:
            try:
                self.bgm_ring = Ring(max(self.block * 32, SR // 2))
                self.backing = BackingCapture(backing, self.bgm_ring, self.block)
                self.backing.start()
            except Exception as e:
                self.backing_error = str(e)
                self.backing = None
                self.bgm_ring = None
        self.bgm_fifo = np.zeros(0)
        try:
            shifter = self.fx.tuner.SHIFT_LAT
            if self.duplex:
                li, lo = opened[0].latency
                rt = (li + lo) * SR + shifter
            else:
                rt = (opened[0].latency + opened[1].latency) * SR + self.out_target + shifter
            self.latency_ms = rt * 1000.0 / SR
            self.bgm_comp = int(rt)
        except Exception:
            self.latency_ms = 0.0

    def stop(self):
        self.running = False
        self.recording = False
        if self.backing is not None:
            self.backing.stop()
        self.backing = None
        self.bgm_ring = None
        self.bgm_fifo = np.zeros(0)
        self.play_data = None
        for s in self.streams:
            try:
                s.stop()
                s.close()
            except Exception:
                pass
        self.streams = []
        self.level = 0.0
        for k in self.lv:
            self.lv[k] = 0.0

    @property
    def underruns(self):
        """耳機那條真的來不及的次數（直播那條會自己調整，不算在這裡）。"""
        if self.duplex:
            return self.xruns
        n = self.status_msgs if isinstance(self.status_msgs, int) else 0
        if self.mon_ring is not None:
            n += self.mon_ring.underruns
        return n

    @property
    def bgm_buffer_ms(self):
        return self.bgm_target * 1000.0 / SR

    @property
    def cable_underruns(self):
        return self.out_ring.underruns if self.out_ring is not None else 0
