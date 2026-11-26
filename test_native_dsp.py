"""用 Python 版完全相同的 test_dsp.py 來測 C++ 版（只把 VoiceFx 換成 C++ 的）。
執行：python test_native_dsp.py <放 test_dsp.py 與 dsp.py 的資料夾>"""
import os
import sys

here = os.path.dirname(os.path.abspath(__file__))
app_dir = sys.argv[1] if len(sys.argv) > 1 else here
sys.path.insert(0, here)
sys.path.insert(1, app_dir)

import dsp            # Python 版（只拿濾波器設計、音名等共用工具）
import native_fx

SR = 48000


class NativeVoiceFx(native_fx.VoiceFx):
    def __init__(self, sr=48000, low_latency=False):
        super().__init__(sr, low_latency)
        # 測試會直接拿聲卡變音的濾波器來量頻率響應；係數與 C++ 版相同
        self.card_eq = [dsp.Biquad("lowshelf", 350, sr, -6.0), dsp.Biquad("peaking", 3200, sr, -5.0, q=1.4)]


dsp.VoiceFx = NativeVoiceFx
src = open(os.path.join(app_dir, "test_dsp.py"), encoding="utf8").read()
print("==== 用 C++ 版跑 Python 版的全部測試 ====")
sys.argv = [sys.argv[0]]
exec(compile(src, "test_dsp.py(native)", "exec"), {"__name__": "__main__"})
