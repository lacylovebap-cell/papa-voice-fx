// 怕怕調音台 - 底層即時音訊引擎（miniaudio / WASAPI）
// 收音＋耳機同一條線（duplex），處理在音訊執行緒內完成；直播（CABLE）與伴奏各自有對時緩衝。
#ifndef NOMINMAX
#define NOMINMAX
#endif
#define MA_NO_DECODING
#define MA_NO_ENCODING
#define MA_NO_GENERATION
#define MA_NO_RESOURCE_MANAGER
#define MA_NO_NODE_GRAPH
#define MA_NO_ENGINE
#define MINIAUDIO_IMPLEMENTATION
#include "third_party/miniaudio.h"

#include "dsp.hpp"

#include <atomic>
#include <chrono>
#include <cstdio>
#include <mutex>
#include <string>
#include <utility>
#include <vector>

#ifdef _WIN32
#define PAPA_API extern "C" __declspec(dllexport)
#else
#define PAPA_API extern "C" __attribute__((visibility("default")))
#endif

using namespace papa;

static const int SR = 48000;

// ---------------------------------------------------------------- 對時緩衝（單一寫入／單一讀取）
// 不夠時淡出而不是突然變 0；累積過多時用極小幅度重新取樣追上，不會整段丟棄（不會「啵」）。
struct Ring {
    std::vector<float> buf;
    size_t cap, r = 0, w = 0, n = 0;
    double frac = 0;
    bool primed = false;
    long underruns = 0;
    std::mutex m;
    explicit Ring(size_t c = 1 << 16) : buf(c, 0.f), cap(c) {}
    void clear() { std::lock_guard<std::mutex> g(m); r = w = n = 0; frac = 0; primed = false; }
    void write(const float* x, size_t k) {
        std::lock_guard<std::mutex> g(m);
        if (k >= cap) { x += k - cap; k = cap; }
        if (n + k > cap) { size_t over = n + k - cap; r = (r + over) % cap; n -= over; }
        for (size_t i = 0; i < k; i++) { buf[w] = x[i]; w = (w + 1) % cap; }
        n += k;
    }
    // 讀 m 個樣本，以 target 為目標緩衝量
    void read(float* out, size_t mcount, size_t target) {
        std::lock_guard<std::mutex> g(m);
        if (!primed) {
            if (n < target + mcount) { std::fill(out, out + mcount, 0.f); return; }
            primed = true;
        }
        if (n < mcount + 4) {
            size_t k = std::min(n > 0 ? n - 1 : 0, mcount);
            for (size_t i = 0; i < k; i++) { out[i] = buf[r] * (1.f - (float)i / (float)std::max<size_t>(k, 1)); r = (r + 1) % cap; }
            n -= k;
            std::fill(out + k, out + mcount, 0.f);
            underruns++;
            primed = false;
            return;
        }
        if (n > target * 4 + mcount) { size_t drop = n - target; r = (r + drop) % cap; n -= drop; }
        double ratio = 1.0 + std::max(-0.005, std::min(0.005, ((double)n - (double)target) / std::max<double>(target, 1) * 0.005));
        double pos = frac;
        for (size_t i = 0; i < mcount; i++) {
            size_t i0 = (size_t)pos;
            double fr = pos - (double)i0;
            if (i0 + 1 >= n) { i0 = n >= 2 ? n - 2 : 0; fr = 0; }
            float a = buf[(r + i0) % cap], b = buf[(r + i0 + 1) % cap];
            out[i] = (float)(a * (1 - fr) + b * fr);
            pos += ratio;
        }
        size_t used = std::min((size_t)pos, n);
        frac = pos - (double)(size_t)pos;
        r = (r + used) % cap;
        n -= used;
    }
};

// ---------------------------------------------------------------- 引擎
struct Engine {
    ma_context ctx;
    bool ctx_ok = false;
    bool use_null = false;
    ma_device main_dev, cable_dev, bgm_dev;
    bool main_on = false, cable_on = false, bgm_on_dev = false;
    std::atomic<bool> running{false};
    VoiceFx* fx = nullptr;
    int block = 128;
    double window_ms = 15.0;

    // 參數（介面執行緒寫入，音訊執行緒在區塊開頭套用）
    std::mutex pm;
    std::vector<std::pair<std::string, double>> pending;
    double g_inp = 1, g_mon = 1, g_dry = 0, g_out = 1, g_bgm = 0.8;
    bool bgm_on = true;
    double bgm_trim_ms = 0;
    double test_tone_hz = 0, test_tone_ph = 0;   // 測試用：用合成音代替麥克風

    Ring cable_ring{1 << 16};
    Ring bgm_ring{1 << 16};
    size_t cable_target = 256;
    std::vector<double> bgm_fifo;  // 伴奏延後用
    size_t bgm_fifo_len = 0;
    long bgm_comp = 0;

    Limiter lim_out{SR}, lim_mon{SR};
    std::vector<float> tmp_in, tmp_y, tmp_bg, tmp_mon, tmp_out;
    std::vector<double> d_om, d_mon, d_bgmix;
    std::vector<float> cable_tmp = std::vector<float>(16384), bgm_tmp = std::vector<float>(16384);
    void ensure(int frames) {
        if ((int)tmp_in.size() < frames) {
            tmp_in.resize(frames); tmp_y.resize(frames); tmp_bg.resize(frames); tmp_mon.resize(frames); tmp_out.resize(frames);
            d_om.resize(frames); d_mon.resize(frames); d_bgmix.resize(frames);
        }
    }

    // 狀態
    std::atomic<double> lv_inp{0}, lv_mon{0}, lv_out{0}, lv_bgm{0}, lv_bgm_cap{0};
    std::atomic<double> info[6];
    std::atomic<double> in_clip{0};
    std::atomic<long> xruns{0}, errors{0};
    double latency_ms = 0;
    std::chrono::steady_clock::time_point t_start;

    // 錄音／回放
    std::vector<float> rec_wet, rec_dry, rec_voice, rec_bgm;   // 直播混音／原聲／處理後人聲／伴奏（分開存才能事後對齊）
    std::atomic<size_t> rec_n{0};
    std::atomic<bool> recording{false};
    size_t rec_max = (size_t)SR * 60 * 5;
    size_t rec_len = 0;
    std::vector<float> play_buf;
    std::atomic<bool> playing{false};
    size_t play_pos = 0;
    int play_which = 0;

    std::string last_error;

    Engine() { for (auto& a : info) a.store(0); }

    bool init_ctx() {
        if (ctx_ok) return true;
        ma_context_config cc = ma_context_config_init();
        ma_backend nullb[1] = {ma_backend_null};
#ifdef _WIN32
        ma_backend wb[1] = {ma_backend_wasapi};
        ma_result r = use_null ? ma_context_init(nullb, 1, &cc, &ctx) : ma_context_init(wb, 1, &cc, &ctx);
#else
        ma_result r = ma_context_init(use_null ? nullb : nullptr, use_null ? 1 : 0, &cc, &ctx);
#endif
        if (r != MA_SUCCESS) { last_error = std::string("無法開啟音訊系統：") + ma_result_description(r); return false; }
        ctx_ok = true;
        return true;
    }
    void uninit_ctx() { if (ctx_ok) { ma_context_uninit(&ctx); ctx_ok = false; } }

    void apply_pending() {
        if (!pm.try_lock()) return;
        auto items = std::move(pending);
        pending.clear();
        pm.unlock();
        for (auto& kv : items) set_now(kv.first, kv.second);
    }

    void set_now(const std::string& k, double v) {
        if (k == "g_inp") g_inp = v;
        else if (k == "g_mon") g_mon = v;
        else if (k == "g_dry") g_dry = v;
        else if (k == "g_out") g_out = v;
        else if (k == "g_bgm") g_bgm = v;
        else if (k == "bgm_on") bgm_on = v > 0.5;
        else if (k == "bgm_trim_ms") bgm_trim_ms = v;
        else if (k == "test_tone_hz") test_tone_hz = v;
        else if (fx) {
            TunerParams& t = fx->tuner.p;
            FxParams& p = fx->p;
            bool sc = false;
            if (k == "mode") t.mode = (int)v;
            else if (k == "key") { t.key = (int)v; sc = true; }
            else if (k == "scale") { t.scale = (int)v; sc = true; }
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
            else if (fx->set_reverb_key(k, v)) return;
            if (sc) fx->tuner.update_scale();
            fx->apply();
        }
    }

    // ---- 主處理（在收音＋耳機那條線的回呼裡）
    void process(const float* in, float* out_stereo, int frames) {
        apply_pending();
        ensure(frames);
        float pk_in = 0;
        for (int i = 0; i < frames; i++) {
            float v = in ? in[i] : 0.f;
            if (test_tone_hz > 0) {
                test_tone_ph += 2 * PI * test_tone_hz / SR;
                if (test_tone_ph > 2 * PI) test_tone_ph -= 2 * PI;
                v = (float)(0.3 * std::sin(test_tone_ph) + 0.15 * std::sin(2 * test_tone_ph));
            }
            pk_in = std::max(pk_in, std::fabs(v));
            tmp_in[i] = (float)(v * g_inp);
            tmp_y[i] = tmp_in[i];
        }
        lv_inp.store(pk_in);
        if (pk_in > 0.97f) in_clip.store(1.0); else in_clip.store(in_clip.load() * 0.995);

        fx->process(tmp_y.data(), frames);
        const Tuner& t = fx->tuner;
        info[0].store(t.f);
        info[1].store(t.voiced ? t.det : -1);
        info[2].store((t.voiced && t.p.mode != M_OFF) ? t.target : -1);
        info[3].store(t.keyt.key);
        info[4].store(t.cur);
        info[5].store(fx->deesser.reduced);

        // 伴奏（不經過任何變音）
        bool have_bgm = bgm_on_dev;
        float pk_bgm = 0;
        if (have_bgm) {
            bgm_ring.read(tmp_bg.data(), frames, (size_t)block * 3);
            float gb = bgm_on ? (float)g_bgm : 0.f;
            for (int i = 0; i < frames; i++) { tmp_bg[i] *= gb; pk_bgm = std::max(pk_bgm, std::fabs(tmp_bg[i])); }
        } else std::fill(tmp_bg.begin(), tmp_bg.begin() + frames, 0.f);
        lv_bgm.store(pk_bgm);

        // 直播用的伴奏要延後「一圈延遲」才會和人聲對齊
        size_t want = (size_t)std::max(0.0, (double)bgm_comp + bgm_trim_ms * SR / 1000.0);
        size_t need = want + frames;
        if (need + frames + 1 > bgm_fifo.size()) { need = bgm_fifo.size() - frames - 1; want = need - frames; }
        // bgm_fifo 當作線性佇列：[0, bgm_fifo_len) 有效（開始時已配好 1 秒）
        for (int i = 0; i < frames; i++) bgm_fifo[bgm_fifo_len + i] = tmp_bg[i];
        bgm_fifo_len += frames;
        if (bgm_fifo_len < need) {   // 不夠：前面補 0
            size_t pad = need - bgm_fifo_len;
            std::memmove(bgm_fifo.data() + pad, bgm_fifo.data(), sizeof(double) * bgm_fifo_len);
            std::fill(bgm_fifo.begin(), bgm_fifo.begin() + pad, 0.0);
            bgm_fifo_len = need;
        } else if (bgm_fifo_len > need) {   // 太多：丟掉最舊的
            size_t drop = bgm_fifo_len - need;
            std::memmove(bgm_fifo.data(), bgm_fifo.data() + drop, sizeof(double) * need);
            bgm_fifo_len = need;
        }
        // 取出最前面的 frames 個
        std::vector<double>& F = bgm_fifo;
        double* bg_mix = d_bgmix.data();
        for (int i = 0; i < frames; i++) bg_mix[i] = F[i];
        std::memmove(F.data(), F.data() + frames, sizeof(double) * (bgm_fifo_len - frames));
        bgm_fifo_len -= frames;

        // 直播輸出
        double* om = d_om.data();
        for (int i = 0; i < frames; i++) om[i] = tmp_y[i] * g_out + bg_mix[i];
        lim_out.process(om, frames);
        float pk_out = 0;
        for (int i = 0; i < frames; i++) { tmp_out[i] = (float)om[i]; pk_out = std::max(pk_out, std::fabs(tmp_out[i])); }
        lv_out.store(cable_on ? pk_out : 0.0);
        if (cable_on) cable_ring.write(tmp_out.data(), frames);

        // 錄音（錄直播出去的聲音＋原聲）
        if (recording.load()) {
            size_t n0 = rec_n.load();
            size_t k = std::min((size_t)frames, rec_max - std::min(rec_max, n0));
            for (size_t i = 0; i < k; i++) {
                rec_wet[n0 + i] = tmp_out[i]; rec_dry[n0 + i] = tmp_in[i];
                rec_voice[n0 + i] = (float)(tmp_y[i] * g_out); rec_bgm[n0 + i] = (float)bg_mix[i];
            }
            rec_n.store(n0 + k);
        }

        // 耳機
        double* mon = d_mon.data();
        if (playing.load()) {
            for (int i = 0; i < frames; i++) {
                mon[i] = play_pos < play_buf.size() ? play_buf[play_pos] * g_mon : 0.0;
                play_pos++;
            }
            if (play_pos >= play_buf.size()) playing.store(false);
        } else {
            for (int i = 0; i < frames; i++)
                mon[i] = (tmp_y[i] + tmp_bg[i]) * g_mon + tmp_in[i] * g_dry * g_mon;
        }
        lim_mon.process(mon, frames);
        float pk_mon = 0;
        const float* sd = fx->side.data();
        bool use_side = !playing.load() && (int)fx->side.size() >= frames;
        for (int i = 0; i < frames; i++) {
            float v = (float)mon[i];
            pk_mon = std::max(pk_mon, std::fabs(v));
            float w = use_side ? (float)(sd[i] * g_mon) : 0.f;   // 混響左右寬度（只有耳機聽得到）
            out_stereo[2 * i] = std::max(-1.f, std::min(1.f, v + w));
            out_stereo[2 * i + 1] = std::max(-1.f, std::min(1.f, v - w));
        }
        lv_mon.store(pk_mon);
    }

    static void main_cb(ma_device* d, void* out, const void* in, ma_uint32 frames) {
        Engine* e = (Engine*)d->pUserData;
        try {
            e->process((const float*)in, (float*)out, (int)frames);
        } catch (...) {
            e->errors++;
            std::memset(out, 0, sizeof(float) * 2 * frames);
        }
    }
    static void cable_cb(ma_device* d, void* out, const void*, ma_uint32 frames) {
        Engine* e = (Engine*)d->pUserData;
        float* o = (float*)out;
        if (e->cable_tmp.size() < frames) { std::memset(out, 0, sizeof(float) * 2 * frames); return; }
        float* y = e->cable_tmp.data();
        long before = e->cable_ring.underruns;
        e->cable_ring.read(y, frames, e->cable_target);
        if (e->cable_ring.underruns > before && e->cable_target < (size_t)e->block * 12) e->cable_target += e->block;
        for (ma_uint32 i = 0; i < frames; i++) { o[2 * i] = y[i]; o[2 * i + 1] = y[i]; }
    }
    static void bgm_cb(ma_device* d, void*, const void* in, ma_uint32 frames) {
        Engine* e = (Engine*)d->pUserData;
        const float* x = (const float*)in;
        if (e->bgm_tmp.size() < frames) return;
        float* m = e->bgm_tmp.data();
        float pk = 0;
        for (ma_uint32 i = 0; i < frames; i++) { m[i] = 0.5f * (x[2 * i] + x[2 * i + 1]); pk = std::max(pk, std::fabs(m[i])); }
        e->lv_bgm_cap.store(pk);
        e->bgm_ring.write(m, frames);
    }

    // ---- 裝置
    bool devices(ma_device_info** pb, ma_uint32* nb, ma_device_info** cp, ma_uint32* nc) {
        if (!init_ctx()) return false;
        return ma_context_get_devices(&ctx, pb, nb, cp, nc) == MA_SUCCESS;
    }

    int start(int in_idx, int mon_idx, int out_idx, int bgm_idx, int blk, double win_ms, int exclusive) {
        stop();
        last_error.clear();
        ma_device_info *pb, *cp; ma_uint32 nb, nc;
        if (!devices(&pb, &nb, &cp, &nc)) { if (last_error.empty()) last_error = "讀取裝置失敗"; return -1; }
        if (in_idx < 0 || in_idx >= (int)nc || mon_idx < 0 || mon_idx >= (int)nb) { last_error = "裝置編號無效（請按「重新尋找裝置」）"; return -2; }
        block = std::max(32, blk);
        window_ms = win_ms;
        delete fx;
        fx = new VoiceFx(SR, window_ms);
        {   // 先套用開始前就設定好的參數
            std::lock_guard<std::mutex> g(pm);
            for (auto& kv : pending) set_now(kv.first, kv.second);
            pending.clear();
        }
        for (auto& kv : saved) set_now(kv.first, kv.second);
        lim_out = Limiter(SR); lim_mon = Limiter(SR);
        cable_ring.clear(); bgm_ring.clear();
        cable_target = (size_t)block * 2;
        bgm_fifo.assign((size_t)SR, 0.0); bgm_fifo_len = 0;
        xruns = 0; errors = 0;
        rec_wet.assign(rec_max, 0.f); rec_dry.assign(rec_max, 0.f);
        rec_voice.assign(rec_max, 0.f); rec_bgm.assign(rec_max, 0.f);
        ensure(8192);
        fx->t1.resize(8192); fx->t2.resize(8192);

        // 收音＋耳機（同一條線）
        ma_device_config c = ma_device_config_init(ma_device_type_duplex);
        c.sampleRate = SR;
        c.periodSizeInFrames = (ma_uint32)block;
        c.performanceProfile = ma_performance_profile_low_latency;
        c.capture.pDeviceID = &cp[in_idx].id;
        c.capture.format = ma_format_f32; c.capture.channels = 1;
        c.capture.shareMode = exclusive ? ma_share_mode_exclusive : ma_share_mode_shared;
        c.playback.pDeviceID = &pb[mon_idx].id;
        c.playback.format = ma_format_f32; c.playback.channels = 2;
        c.playback.shareMode = exclusive ? ma_share_mode_exclusive : ma_share_mode_shared;
        c.dataCallback = main_cb;
        c.pUserData = this;
        c.wasapi.noAutoConvertSRC = MA_FALSE;
        ma_result r = ma_device_init(&ctx, &c, &main_dev);
        if (r != MA_SUCCESS && exclusive) {   // 獨佔失敗就用一般模式
            c.capture.shareMode = c.playback.shareMode = ma_share_mode_shared;
            r = ma_device_init(&ctx, &c, &main_dev);
        }
        if (r != MA_SUCCESS) { last_error = std::string("開啟麥克風／耳機失敗：") + ma_result_description(r); return -3; }
        main_on = true;

        // 直播（CABLE）
        if (out_idx >= 0 && out_idx < (int)nb) {
            ma_device_config o = ma_device_config_init(ma_device_type_playback);
            o.sampleRate = SR;
            o.periodSizeInFrames = (ma_uint32)block;
            o.performanceProfile = ma_performance_profile_low_latency;
            o.playback.pDeviceID = &pb[out_idx].id;
            o.playback.format = ma_format_f32; o.playback.channels = 2;
            o.dataCallback = cable_cb; o.pUserData = this;
            if (ma_device_init(&ctx, &o, &cable_dev) == MA_SUCCESS) cable_on = true;
            else last_error = "直播輸出開啟失敗";
        }
        // 伴奏（擷取某個輸出裝置正在播的聲音）
        if (bgm_idx >= 0 && bgm_idx < (int)nb) {
#ifdef _WIN32
            ma_device_config b = ma_device_config_init(ma_device_type_loopback);
            b.capture.pDeviceID = &pb[bgm_idx].id;
#else
            ma_device_config b = ma_device_config_init(ma_device_type_capture);
            b.capture.pDeviceID = nullptr;
#endif
            b.sampleRate = SR;
            b.capture.format = ma_format_f32; b.capture.channels = 2;
            b.periodSizeInFrames = (ma_uint32)block;
            b.dataCallback = bgm_cb; b.pUserData = this;
            if (ma_device_init(&ctx, &b, &bgm_dev) == MA_SUCCESS) bgm_on_dev = true;
            else last_error = "伴奏擷取失敗";
        }

        // 延遲估計：收音＋耳機的內部緩衝＋變調
        double cap_frames = (double)main_dev.capture.internalPeriodSizeInFrames;
        double play_frames = (double)main_dev.playback.internalPeriodSizeInFrames * std::max<ma_uint32>(1, main_dev.playback.internalPeriods);
        double shifter = Tuner::SHIFT_LAT;
        double rt = cap_frames + play_frames + block + shifter;
        latency_ms = rt * 1000.0 / SR;
        bgm_comp = (long)rt;

        // 先開輸出，再開輸入
        if (cable_on) ma_device_start(&cable_dev);
        ma_device_start(&main_dev);
        if (bgm_on_dev) ma_device_start(&bgm_dev);
        t_start = std::chrono::steady_clock::now();
        running = true;
        return 0;
    }

    void stop() {
        running = false;
        recording = false;
        playing = false;
        if (bgm_on_dev) { ma_device_uninit(&bgm_dev); bgm_on_dev = false; }
        if (main_on) { ma_device_uninit(&main_dev); main_on = false; }
        if (cable_on) { ma_device_uninit(&cable_dev); cable_on = false; }
        lv_inp = lv_mon = lv_out = lv_bgm = 0;
    }

    // 參數也要在重新開始時保留
    std::vector<std::pair<std::string, double>> saved;
    void set(const std::string& k, double v) {
        bool found = false;
        for (auto& kv : saved) if (kv.first == k) { kv.second = v; found = true; }
        if (!found) saved.emplace_back(k, v);
        std::lock_guard<std::mutex> g(pm);
        pending.emplace_back(k, v);
    }
};

static Engine* G = nullptr;
static Engine& E() { if (!G) G = new Engine(); return *G; }

// ---------------------------------------------------------------- C 介面
PAPA_API void papa_use_null_backend(int on) { E().use_null = on != 0; E().uninit_ctx(); }

PAPA_API void papa_rescan() { Engine& e = E(); if (!e.running) e.uninit_ctx(); }

// kind: 0=輸出（耳機/直播/伴奏來源） 1=輸入（麥克風）。回傳 UTF-8 名稱，以 \n 分隔；預設裝置前面加 *
PAPA_API int papa_list_devices(int kind, char* out, int cap) {
    ma_device_info *pb, *cp; ma_uint32 nb, nc;
    if (!E().devices(&pb, &nb, &cp, &nc)) return -1;
    std::string s;
    ma_device_info* L = kind == 0 ? pb : cp;
    ma_uint32 n = kind == 0 ? nb : nc;
    for (ma_uint32 i = 0; i < n; i++) {
        if (L[i].isDefault) s += "*";
        s += L[i].name;
        s += "\n";
    }
    if ((int)s.size() + 1 > cap) return (int)s.size() + 1;
    std::memcpy(out, s.c_str(), s.size() + 1);
    return 0;
}

PAPA_API int papa_start(int in_idx, int mon_idx, int out_idx, int bgm_idx, int block, double window_ms, int exclusive) {
    return E().start(in_idx, mon_idx, out_idx, bgm_idx, block, window_ms, exclusive);
}
PAPA_API void papa_stop() { E().stop(); }
PAPA_API int papa_running() { return E().running ? 1 : 0; }
PAPA_API void papa_set(const char* key, double v) { E().set(key, v); }

PAPA_API int papa_last_error(char* out, int cap) {
    const std::string& s = E().last_error;
    if ((int)s.size() + 1 > cap) return (int)s.size() + 1;
    std::memcpy(out, s.c_str(), s.size() + 1);
    return 0;
}

// 狀態：0-3 電平(麥克風/耳機/直播/伴奏) 4 頻率 5 唱的音 6 目標音 7 調 8 移動量 9 延遲ms
// 10 直播緩衝ms 11 爆音次數 12 錯誤次數 13 麥克風太大聲 14 錄音中 15 已錄秒數 16 播放中 17 伴奏擷取電平 18 伴奏有開
PAPA_API void papa_status(double* o) {
    Engine& e = E();
    o[0] = e.lv_inp; o[1] = e.lv_mon; o[2] = e.lv_out; o[3] = e.lv_bgm;
    for (int i = 0; i < 5; i++) o[4 + i] = e.info[i];
    o[9] = e.latency_ms;
    o[10] = (double)e.cable_target * 1000.0 / SR;
    o[11] = (double)e.xruns;
    o[12] = (double)e.errors;
    o[13] = e.in_clip;
    o[14] = e.recording ? 1 : 0;
    o[15] = (double)e.rec_n / SR;
    o[16] = e.playing ? 1 : 0;
    o[17] = e.lv_bgm_cap;
    o[18] = e.bgm_on_dev ? 1 : 0;
}

PAPA_API void papa_rec_start() { Engine& e = E(); e.playing = false; e.rec_n = 0; e.recording = true; }
PAPA_API double papa_rec_stop() { Engine& e = E(); e.recording = false; e.rec_len = e.rec_n; return (double)e.rec_len / SR; }
PAPA_API int papa_rec_has() { return E().rec_len > 0 ? 1 : 0; }

// which: 0=處理後（直播出去的聲音） 1=原聲
PAPA_API int papa_play(int which) {
    Engine& e = E();
    if (!e.running || e.rec_len == 0) return 0;
    e.playing = false;
    const std::vector<float>& s = which == 0 ? e.rec_wet : e.rec_dry;
    e.play_buf.assign(s.begin(), s.begin() + e.rec_len);
    e.play_pos = 0;
    e.playing = true;
    return 1;
}
PAPA_API void papa_play_stop() { E().playing = false; }

// 播放任意一段（暫存清單裡的某一段錄音）
PAPA_API int papa_play_buffer(const float* data, int n) {
    Engine& e = E();
    if (!e.running || n <= 0) return 0;
    e.playing = false;
    e.play_buf.assign(data, data + n);
    e.play_pos = 0;
    e.playing = true;
    return 1;
}

// 取得錄音資料（給 Python 存 WAV）which: 0=直播混音 1=原聲 2=處理後人聲 3=伴奏
PAPA_API int papa_rec_copy(int which, float* out, int cap) {
    Engine& e = E();
    const std::vector<float>& s = which == 0 ? e.rec_wet : which == 1 ? e.rec_dry : which == 2 ? e.rec_voice : e.rec_bgm;
    int n = (int)std::min<size_t>(e.rec_len, (size_t)cap);
    std::memcpy(out, s.data(), sizeof(float) * n);
    return n;
}
PAPA_API int papa_rec_len() { return (int)E().rec_len; }
