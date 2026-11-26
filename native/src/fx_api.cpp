// 離線處理介面：給測試（以及引擎內部）使用
#include "dsp.hpp"
#include <cstring>
#include <string>

#ifdef _WIN32
#define PAPA_API extern "C" __declspec(dllexport)
#else
#define PAPA_API extern "C" __attribute__((visibility("default")))
#endif

using namespace papa;

PAPA_API void* papa_fx_create(double sr, double window_ms) { return new VoiceFx(sr, window_ms); }
PAPA_API void papa_fx_destroy(void* h) { delete (VoiceFx*)h; }

// 參數（數值）。名稱與 Python 版 VoiceFx.set 相同。
PAPA_API int papa_fx_set(void* h, const char* key, double v) {
    VoiceFx* fx = (VoiceFx*)h;
    std::string k(key);
    TunerParams& t = fx->tuner.p;
    FxParams& p = fx->p;
    bool scale_changed = false;
    if (k == "mode") t.mode = (int)v;
    else if (k == "key") { t.key = (int)v; scale_changed = true; }
    else if (k == "scale") { t.scale = (int)v; scale_changed = true; }
    else if (k == "strength") t.strength = v;
    else if (k == "speed") t.speed = v;
    else if (k == "shift") t.shift = v;
    else if (k == "robot") t.robot = v;
    else if (k == "flex") t.flex = v;
    else if (k == "humanize") t.humanize = v;
    else if (k == "sticky") t.sticky = v;
    else if (k == "predict") t.predict = v;
    else if (k == "vibrato") t.vibrato = v;
    else if (k == "phone") p.phone = v > 0.5;
    else if (k == "phone_dist") p.phone_dist = v;
    else if (k == "machine") p.machine = v > 0.5;
    else if (k == "eq_lo") p.eq_lo = v;
    else if (k == "eq_mid") p.eq_mid = v;
    else if (k == "eq_hi") p.eq_hi = v;
    else if (k == "comp") p.comp = v;
    else if (k == "card") p.card = v > 0.5;
    else if (k == "electro_fx") p.electro_fx = v;
    else if (k == "color") p.color = (int)v;
    else if (k == "deess") p.deess = v;
    else if (k == "metal") p.metal = v;
    else if (k == "robot_fx") p.robot_fx = v;
    else if (k == "loud") p.loud = v;
    else if (k == "clarity") p.clarity = v;
    else if (k == "thick") p.thick = v;
    else if (k == "widen") p.widen = v;
    else if (k == "gate") p.gate = v;
    else if (k == "window_ms") fx->tuner.set_window(v);
    else if (fx->set_reverb_key(k, v)) return 1;
    else return 0;
    if (scale_changed) fx->tuner.update_scale();
    fx->apply();
    return 1;
}

PAPA_API void papa_fx_process(void* h, float* io, int n) { ((VoiceFx*)h)->process(io, n); }
// 上一個區塊混響的左右差（耳機立體寬度用）
PAPA_API void papa_fx_side(void* h, float* out, int n) {
    VoiceFx* fx = (VoiceFx*)h;
    for (int i = 0; i < n; i++) out[i] = i < (int)fx->side.size() ? fx->side[i] : 0.f;
}

// out[0]=偵測到的頻率 out[1]=唱的音(MIDI，沒聲音時 -1) out[2]=目標音(-1) out[3]=判斷的調(-1)
// out[4]=目前移動量(半音) out[5]=去齒音壓了幾 dB
PAPA_API void papa_fx_info(void* h, double* out) {
    VoiceFx* fx = (VoiceFx*)h;
    const Tuner& t = fx->tuner;
    out[0] = t.f;
    out[1] = t.voiced ? t.det : -1;
    out[2] = (t.voiced && t.p.mode != M_OFF) ? t.target : -1;
    out[3] = t.keyt.key;
    out[4] = t.cur;
    out[5] = fx->deesser.reduced;
}

// 測試用：讀變調內部狀態（讀取頭延遲、淡入中的讀取頭、接合次數）
PAPA_API void papa_fx_shift_state(void* h, double* out) {
    VoiceFx* fx = (VoiceFx*)h;
    out[0] = fx->tuner.da;
    out[1] = fx->tuner.fading ? fx->tuner.db : -1;
    out[2] = (double)fx->tuner.splices;
}
