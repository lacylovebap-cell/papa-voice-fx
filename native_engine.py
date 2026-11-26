"""PAPA 調音台 - 底層引擎（C++／miniaudio）的 Python 包裝。
介面與舊的 engine.py 相同，畫面（app.py）不用改就能換用。"""
import ctypes
import os
import sys
import time
import wave

import numpy as np

SR = 48000
SD_ERROR = None


def _find_lib():
    here = os.path.dirname(os.path.abspath(__file__))
    base = getattr(sys, "_MEIPASS", here)
    names = ["papa_engine.dll"] if os.name == "nt" else ["libpapa_engine.so"]
    for d in (base, here, os.path.join(here, "build")):
        for n in names:
            p = os.path.join(d, n)
            if os.path.exists(p):
                return p
    raise OSError("找不到 papa_engine 函式庫")


LIB = ctypes.CDLL(_find_lib())
_D = ctypes.c_double
_I = ctypes.c_int
LIB.papa_use_null_backend.argtypes = [_I]
LIB.papa_rescan.argtypes = []
LIB.papa_list_devices.argtypes = [_I, ctypes.c_char_p, _I]
LIB.papa_list_devices.restype = _I
LIB.papa_start.argtypes = [_I, _I, _I, _I, _I, _D, _I]
LIB.papa_start.restype = _I
LIB.papa_stop.argtypes = []
LIB.papa_running.restype = _I
LIB.papa_set.argtypes = [ctypes.c_char_p, _D]
LIB.papa_last_error.argtypes = [ctypes.c_char_p, _I]
LIB.papa_last_error.restype = _I
LIB.papa_status.argtypes = [ctypes.POINTER(_D)]
LIB.papa_rec_start.argtypes = []
LIB.papa_rec_stop.restype = _D
LIB.papa_rec_has.restype = _I
LIB.papa_play.argtypes = [_I]
LIB.papa_play.restype = _I
LIB.papa_rec_copy.argtypes = [_I, ctypes.POINTER(ctypes.c_float), _I]
LIB.papa_rec_copy.restype = _I
LIB.papa_rec_len.restype = _I
LIB.papa_play_buffer.argtypes = [ctypes.POINTER(ctypes.c_float), _I]
LIB.papa_play_buffer.restype = _I

sd = LIB   # app.py 用「engine.sd is None」判斷套件是否正常

COLOR_IDS = {"原本": 0, "人聲修飾": 1, "少女甜嗓": 2, "御姐煙嗓": 3, "古風戲腔": 4, "ASMR": 5}
MODE_IDS = {"off": 0, "scale": 1, "chrom": 2, "robot": 3, "natural": 4, "electro": 5}
SCALE_IDS = {"major": 0, "minor": 1, "pent": 2, "blues": 3}

WINDOW_MS = 15.0     # 變調視窗：15 ms（延遲低、仍然乾淨）


def _names(kind):
    buf = ctypes.create_string_buffer(65536)
    r = LIB.papa_list_devices(kind, buf, len(buf))
    if r != 0:
        return [], None
    out, default = [], None
    for i, line in enumerate(buf.value.decode("utf8", "replace").split("\n")):
        if not line:
            continue
        if line.startswith("*"):
            line = line[1:]
            default = len(out)
        out.append(line)
    return out, default


def rescan():
    LIB.papa_rescan()


def list_devices():
    """(輸入清單, 輸出清單)，每項 (index, 名稱)。"""
    ins, _ = _names(1)
    outs, _ = _names(0)
    return list(enumerate(ins)), list(enumerate(outs))


def default_devices():
    _, di = _names(1)
    _, do = _names(0)
    return di, do


def list_backing_sources():
    """可擷取伴奏的來源：每個輸出裝置正在播的聲音。"""
    outs, _ = _names(0)
    return outs


def write_wav(path, data):
    pcm = (np.clip(np.asarray(data, np.float64), -1.0, 1.0) * 32767).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())


class _Backing:
    def __init__(self, eng):
        self._e = eng
        self.error = None

    @property
    def level(self):
        return self._e._st()[17]


class _KeyT:
    def __init__(self, eng):
        self._e = eng

    @property
    def key(self):
        k = int(self._e._st()[7])
        return None if k < 0 else k


class _Tuner:
    def __init__(self, eng):
        self._e = eng
        self.keyt = _KeyT(eng)

    @property
    def cur(self):
        return self._e._st()[8]


class _Fx:
    def __init__(self, eng):
        self.tuner = _Tuner(eng)


class Engine:
    def __init__(self):
        self.params = {}
        self.gains = dict(inp=1.0, mon=1.0, dry=0.0, out=1.0, bgm=0.8)
        self._bgm_on = True
        self._bgm_trim = 0.0
        self.running = False
        self.block = 128
        self.duplex = True
        self.backing = None
        self.backing_error = None
        self.last_error = None
        self.fx = _Fx(self)
        self._cache = None
        self._cache_t = 0.0
        self._rec_dry = None
        self._has_rec = False

    # ---- 狀態（每 20 ms 最多讀一次）
    def _st(self):
        now = time.monotonic()
        if self._cache is None or now - self._cache_t > 0.02:
            a = (_D * 20)()
            LIB.papa_status(a)
            self._cache = list(a)
            self._cache_t = now
        return self._cache

    @property
    def lv(self):
        s = self._st()
        return dict(inp=s[0], mon=s[1], out=s[2], bgm=s[3])

    @property
    def level(self):
        return self._st()[1]

    @property
    def info(self):
        s = self._st()
        return (s[4], None if s[5] < 0 else s[5], None if s[6] < 0 else s[6])

    @property
    def latency_ms(self):
        return self._st()[9]

    @property
    def cable_target(self):
        return self._st()[10] * SR / 1000.0

    @property
    def underruns(self):
        return int(self._st()[11])

    @property
    def in_clip(self):
        return self._st()[13]

    @property
    def recording(self):
        return self._st()[14] > 0.5

    @property
    def rec_samples(self):
        return int(self._st()[15] * SR)

    @property
    def playing(self):
        return self._st()[16] > 0.5

    # ---- 參數
    def set_params(self, **kw):
        for k, v in kw.items():
            self.params[k] = v
            if k == "mode":
                v = MODE_IDS.get(v, 0)
            elif k == "scale":
                v = SCALE_IDS.get(v, 0)
            elif k == "color":
                v = COLOR_IDS.get(v, 0)
            elif isinstance(v, bool):
                v = 1.0 if v else 0.0
            LIB.papa_set(k.encode(), float(v))

    def set_gain(self, name, value):
        self.gains[name] = float(value)
        LIB.papa_set(("g_" + name).encode(), float(value))

    @property
    def bgm_on(self):
        return self._bgm_on

    @bgm_on.setter
    def bgm_on(self, v):
        self._bgm_on = bool(v)
        LIB.papa_set(b"bgm_on", 1.0 if v else 0.0)

    @property
    def bgm_trim_ms(self):
        return self._bgm_trim

    @bgm_trim_ms.setter
    def bgm_trim_ms(self, v):
        self._bgm_trim = float(v)
        LIB.papa_set(b"bgm_trim_ms", float(v))

    # ---- 開始／停止
    def start(self, in_dev, mon_dev, out_dev, block=128, exclusive=False, low_latency=False, backing=None, ultra=False):
        self.stop()
        self.block = 64 if ultra else max(64, min(int(block), 512))
        bgm_idx = -1
        self.backing = None
        self.backing_error = None
        if backing:
            outs, _ = _names(0)
            if backing in outs:
                bgm_idx = outs.index(backing)
            else:
                self.backing_error = "找不到伴奏來源：" + backing
        r = LIB.papa_start(int(in_dev), int(mon_dev), -1 if out_dev is None else int(out_dev), bgm_idx,
                           self.block, WINDOW_MS, 1 if exclusive else 0)
        err = self._err()
        if r != 0:
            raise RuntimeError(err or ("啟動失敗（%d）" % r))
        if err:
            self.backing_error = err
        if bgm_idx >= 0:
            self.backing = _Backing(self)
        self.running = True
        self._cache = None
        self.last_error = None

    def _err(self):
        b = ctypes.create_string_buffer(4096)
        LIB.papa_last_error(b, len(b))
        return b.value.decode("utf8", "replace") or None

    def stop(self):
        LIB.papa_stop()
        self.running = False
        self._cache = None

    # ---- 錄音／回放
    def start_recording(self):
        LIB.papa_rec_start()
        self._cache = None

    def stop_recording(self):
        secs = LIB.papa_rec_stop()
        self._has_rec = secs > 0
        self._cache = None
        return secs

    def has_recording(self):
        return LIB.papa_rec_has() == 1

    def play(self, which="wet"):
        ok = LIB.papa_play(0 if which == "wet" else 1) == 1
        self._cache = None
        return ok

    def stop_play(self):
        LIB.papa_play_stop()
        self._cache = None

    def _rec(self, which):
        n = LIB.papa_rec_len()
        buf = np.zeros(max(n, 1), np.float32)
        k = LIB.papa_rec_copy({"wet": 0, "dry": 1, "voice": 2, "bgm": 3}[which], buf.ctypes.data_as(ctypes.POINTER(ctypes.c_float)), n)
        return buf[:k]

    def get_recording(self, which):
        """取出最近一次錄音：'wet' 直播混音／'dry' 原聲／'voice' 處理後人聲／'bgm' 伴奏。"""
        return self._rec(which).copy()

    def play_array(self, arr):
        """在耳機播放任意一段聲音（暫存清單用）。"""
        a = np.ascontiguousarray(np.asarray(arr, np.float32))
        self._play_keep = a
        ok = LIB.papa_play_buffer(a.ctypes.data_as(ctypes.POINTER(ctypes.c_float)), len(a)) == 1
        self._cache = None
        return ok

    def save_wav(self, which, path):
        d = self._rec(which)
        if len(d) == 0:
            raise RuntimeError("沒有錄音可存")
        pcm = (np.clip(d, -1.0, 1.0) * 32767).astype("<i2")
        with wave.open(path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(SR)
            w.writeframes(pcm.tobytes())
