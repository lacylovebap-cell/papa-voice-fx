"""在打包機上拍畫面截圖（給 Claude 檢查版面用；失敗不影響打包）。"""
import os
import sys
import time
import tkinter as tk
import app

os.makedirs("shots", exist_ok=True)
try:
    from PIL import ImageGrab
except Exception as e:
    print("沒有 PIL，略過截圖", e)
    sys.exit(0)
if app.NATIVE:
    app.engine.LIB.papa_use_null_backend(1)
    app.engine.LIB.papa_set(b"test_tone_hz", 220.0)
root = tk.Tk()
a = app.App(root)
root.geometry("1040x900+0+0")
a.refresh_devices(True)
a.start_engine(0)
a.scenes = {"怕怕專屬一": a.scene_snapshot(), "怕怕說唱": a.scene_snapshot()}
a.current_scene = "怕怕專屬一"
a.refresh_scene_bar()
a.preset("electro")
a.rv[1]["type"].set("大廳"); a.on_reverb_type(1)


def shot(name):
    for _ in range(20):
        root.update(); time.sleep(0.05)
    x, y = root.winfo_rootx(), root.winfo_rooty()
    w, h = root.winfo_width(), root.winfo_height()
    print(name, "screen", root.winfo_screenwidth(), root.winfo_screenheight(), "win", x, y, w, h)
    ImageGrab.grab(bbox=(x, y, x + w, y + h), all_screens=True).save(os.path.join("shots", name + ".png"))


root.lift(); root.attributes("-topmost", True)
shot("1_simple")
a.canvas.yview_moveto(1.0); shot("2_simple_bottom")
a.open_mixer("fx"); shot("3_mixer_electro")
a.open_mixer("rv", 1); shot("4_mixer_reverb")
a.open_mixer("align"); shot("5_mixer_align")
a.on_close()
