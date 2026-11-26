"""C++ 聲音處理核心的 Python 包裝（介面與 dsp.VoiceFx 相同，方便直接換用與測試）。"""
import ctypes
import os
import sys
import numpy as np

COLOR_IDS = {"原本": 0, "人聲修飾": 1, "少女甜嗓": 2, "御姐煙嗓": 3, "古風戲腔": 4, "ASMR": 5}
MODE_IDS = {"off": 0, "scale": 1, "chrom": 2, "robot": 3, "natural": 4, "electro": 5}
SCALE_IDS = {"major": 0, "minor": 1, "pent": 2, "blues": 3}


def _load():
    here = os.path.dirname(os.path.abspath(__file__))
    base = getattr(sys, "_MEIPASS", here)
    names = ["papa_engine.dll"] if os.name == "nt" else ["libpapa_engine.so", "papa_engine.so"]
    for d in (base, here, os.path.join(here, "build")):
        for n in names:
            p = os.path.join(d, n)
            if os.path.exists(p):
                lib = ctypes.CDLL(p)
                lib.papa_fx_create.restype = ctypes.c_void_p
                lib.papa_fx_create.argtypes = [ctypes.c_double, ctypes.c_double]
                lib.papa_fx_destroy.argtypes = [ctypes.c_void_p]
                lib.papa_fx_set.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_double]
                lib.papa_fx_set.restype = ctypes.c_int
                lib.papa_fx_process.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_float), ctypes.c_int]
                lib.papa_fx_info.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_double)]
                lib.papa_fx_side.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_float), ctypes.c_int]
                lib.papa_fx_shift_state.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_double)]
                return lib
    raise OSError("找不到 papa_engine 函式庫")


LIB = _load()


class _KeyT:
    def __init__(self, fx):
        self._fx = fx

    @property
    def key(self):
        k = int(self._fx._info()[3])
        return None if k < 0 else k


class _TunerView:
    def __init__(self, fx):
        self._fx = fx
        self.keyt = _KeyT(fx)

    SHIFT_LAT = 120

    def _state(self):
        a = (ctypes.c_double * 3)()
        LIB.papa_fx_shift_state(self._fx.h, a)
        return a

    @property
    def da(self):
        return self._state()[0]

    @property
    def splices(self):
        return int(self._state()[2])

    @property
    def cur(self):
        return self._fx._info()[4]

    def set_window(self, ms):
        self._fx.set(window_ms=ms)


class VoiceFx:
    CARD_SHIFT = 1.5

    def __init__(self, sr=48000, low_latency=False, window_ms=None):
        if window_ms is None:
            window_ms = 12.0 if low_latency else 21.0
        self.h = LIB.papa_fx_create(float(sr), float(window_ms))
        self.sr = sr
        self.tuner = _TunerView(self)
        self.p = {}
        self.info = (0.0, None, None)

    def __del__(self):
        try:
            LIB.papa_fx_destroy(self.h)
        except Exception:
            pass

    def set(self, **kw):
        for k, v in kw.items():
            self.p[k] = v
            if k == "mode":
                v = MODE_IDS.get(v, 0)
            elif k == "scale":
                v = SCALE_IDS.get(v, 0)
            elif k == "color":
                v = COLOR_IDS.get(v, 0)
            elif isinstance(v, bool):
                v = 1.0 if v else 0.0
            LIB.papa_fx_set(self.h, k.encode(), float(v))

    def _info(self):
        a = (ctypes.c_double * 6)()
        LIB.papa_fx_info(self.h, a)
        return list(a)

    def process(self, x):
        buf = np.ascontiguousarray(np.asarray(x, dtype=np.float32)).copy()
        LIB.papa_fx_process(self.h, buf.ctypes.data_as(ctypes.POINTER(ctypes.c_float)), len(buf))
        inf = self._info()
        self.info = (inf[0], None if inf[1] < 0 else inf[1], None if inf[2] < 0 else inf[2])
        sd = np.zeros(len(buf), np.float32)
        LIB.papa_fx_side(self.h, sd.ctypes.data_as(ctypes.POINTER(ctypes.c_float)), len(buf))
        self.side = sd
        return buf
