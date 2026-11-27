// 怕怕調音台 - C++ 聲音處理核心（由 dsp.py 移植）
// 所有「每一段」的行為都改成以時間換算，區塊大小改變時手感一致。
#pragma once
#include <algorithm>
#include <cmath>
#include <cstring>
#include <vector>
#include <string>

namespace papa {

static const double PI = 3.14159265358979323846;
static const double REF_BLOCK = 256.0;   // Python 版的參考區塊大小

// ---------------------------------------------------------------- 濾波器
enum BqKind { BQ_LOWSHELF, BQ_HIGHSHELF, BQ_PEAKING, BQ_HP, BQ_LP };

struct Biquad {
    BqKind kind = BQ_PEAKING;
    double f0 = 1000, sr = 48000, q = 0.707, gain = 0;
    double b0 = 1, b1 = 0, b2 = 0, a1 = 0, a2 = 0;
    double z1 = 0, z2 = 0;
    bool bypass = false;

    Biquad() {}
    Biquad(BqKind k, double f, double s, double g = 0.0, double qq = 0.707) : kind(k), f0(f), sr(s), q(qq) { set_gain(g); }

    void set_gain(double g) {
        gain = g;
        bypass = (kind == BQ_LOWSHELF || kind == BQ_HIGHSHELF || kind == BQ_PEAKING) && std::fabs(g) < 0.05;
        double A = std::pow(10.0, g / 40.0);
        double w0 = 2 * PI * f0 / sr, cw = std::cos(w0), sw = std::sin(w0);
        double alpha = sw / (2 * q);
        double B0, B1, B2, A0, A1, A2;
        if (kind == BQ_LOWSHELF) {
            double sA = std::sqrt(A);
            B0 = A * ((A + 1) - (A - 1) * cw + 2 * sA * alpha);
            B1 = 2 * A * ((A - 1) - (A + 1) * cw);
            B2 = A * ((A + 1) - (A - 1) * cw - 2 * sA * alpha);
            A0 = (A + 1) + (A - 1) * cw + 2 * sA * alpha;
            A1 = -2 * ((A - 1) + (A + 1) * cw);
            A2 = (A + 1) + (A - 1) * cw - 2 * sA * alpha;
        } else if (kind == BQ_HIGHSHELF) {
            double sA = std::sqrt(A);
            B0 = A * ((A + 1) + (A - 1) * cw + 2 * sA * alpha);
            B1 = -2 * A * ((A - 1) + (A + 1) * cw);
            B2 = A * ((A + 1) + (A - 1) * cw - 2 * sA * alpha);
            A0 = (A + 1) - (A - 1) * cw + 2 * sA * alpha;
            A1 = 2 * ((A - 1) - (A + 1) * cw);
            A2 = (A + 1) - (A - 1) * cw - 2 * sA * alpha;
        } else if (kind == BQ_PEAKING) {
            B0 = 1 + alpha * A; B1 = -2 * cw; B2 = 1 - alpha * A;
            A0 = 1 + alpha / A; A1 = -2 * cw; A2 = 1 - alpha / A;
        } else if (kind == BQ_HP) {
            B0 = (1 + cw) / 2; B1 = -(1 + cw); B2 = (1 + cw) / 2;
            A0 = 1 + alpha; A1 = -2 * cw; A2 = 1 - alpha;
        } else {
            B0 = (1 - cw) / 2; B1 = 1 - cw; B2 = (1 - cw) / 2;
            A0 = 1 + alpha; A1 = -2 * cw; A2 = 1 - alpha;
        }
        b0 = B0 / A0; b1 = B1 / A0; b2 = B2 / A0; a1 = A1 / A0; a2 = A2 / A0;
    }
    void reset() { z1 = z2 = 0; }
    inline double tick(double x) {
        double y = b0 * x + z1;
        z1 = b1 * x - a1 * y + z2;
        z2 = b2 * x - a2 * y;
        return y;
    }
    void run(double* x, int n) {
        if (bypass) return;
        for (int i = 0; i < n; i++) x[i] = tick(x[i]);
    }
};

// ---------------------------------------------------------------- 判斷調
static const int SCALE_MAJOR[7] = {0, 2, 4, 5, 7, 9, 11};
static const int SCALE_MINOR[7] = {0, 2, 3, 5, 7, 8, 10};
static const int SCALE_PENT[5] = {0, 2, 4, 7, 9};
static const int SCALE_BLUES[6] = {0, 3, 5, 6, 7, 10};
static const double MAJ_PROFILE[12] = {6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88};

struct KeyTracker {
    double h[12] = {0};
    double half_life = 20.0;
    int key = -1;
    double sung = 0;
    void add(double midi, double dt) {
        double d = std::pow(0.5, dt / half_life);
        for (double& v : h) v *= d;
        int k = ((int)std::lround(midi) % 12 + 12) % 12;
        h[k] += dt;
        sung += dt;
    }
    static double corr(const double* x, const double* y) {
        double mx = 0, my = 0;
        for (int i = 0; i < 12; i++) { mx += x[i]; my += y[i]; }
        mx /= 12; my /= 12;
        double sxy = 0, sxx = 0, syy = 0;
        for (int i = 0; i < 12; i++) {
            double a = x[i] - mx, b = y[i] - my;
            sxy += a * b; sxx += a * a; syy += b * b;
        }
        return (sxx > 0 && syy > 0) ? sxy / std::sqrt(sxx * syy) : 0.0;
    }
    int update() {
        double s = 0;
        for (double v : h) s += v;
        if (sung < 2.0 || s <= 0) return key;
        double x[12], prof[12], sc[12];
        for (int i = 0; i < 12; i++) x[i] = h[i] / s;
        for (int k = 0; k < 12; k++) {
            for (int i = 0; i < 12; i++) prof[(i + k) % 12] = MAJ_PROFILE[i];
            sc[k] = corr(x, prof);
        }
        int best = 0;
        for (int k = 1; k < 12; k++) if (sc[k] > sc[best]) best = k;
        if (key < 0 || sc[best] > sc[key] + 0.05) key = best;
        return key;
    }
};

// ---------------------------------------------------------------- 電音／變調
enum Mode { M_OFF = 0, M_SCALE = 1, M_CHROM = 2, M_ROBOT = 3, M_NATURAL = 4, M_ELECTRO = 5 };

struct TunerParams {
    int mode = M_OFF;
    int key = 0;
    int scale = 0;          // 0 major 1 minor 2 pent 3 blues
    double strength = 1.0, speed = 0.0, shift = 0.0;
    double robot = 57, card_shift = 0.0;
    double flex = 0.0, humanize = 0.0, sticky = 0.7;
    double tol = 0.0;       // 自然修音：容許誤差（半音）。已經夠準的就不修，聽起來不會被「拉平」
    double predict = 2.0;   // 以「參考區塊（256）」為單位
    double vibrato = 0.0;   // 自然顫音（dB）
};

struct Tuner {
    static const int DEC = 4, R = 512, H = 256, CORR_LEN = 384, SEARCH = 480, L = 16384;
    // 變調：單一讀取頭＋接合時短交叉淡化（見 dsp.py Tuner 說明）
    static constexpr double DMIN = 3.0;
    static const int JFLOOR = 96;
    static const int SHIFT_LAT = 120;   // 變調平均延遲估計（樣本）
    static const int LOOKAHEAD = 0;     // 不額外等待（任何功能都不能增加延遲）
    double sr, srD;
    int W;
    TunerParams p;
    bool allowed[12];
    // 音高偵測
    double dbuf[R];
    int tau_max, tau_min;
    double acc = 0; int accN = 0;
    std::vector<double> dtmp, cm;
    // 狀態
    double cur = 0, raw_prev = 0, wet = 0, det = 0, target = 0, f = 0;
    bool voiced = false;
    double unv_t = 1.0;           // 連續沒偵測到音高的時間（秒）
    bool has_held = false; double held = 0;
    KeyTracker keyt;
    double key_timer = 0;
    double dsm = 0;
    double vc = 0, vt = 0;        // 顫音中心（約 0.25 秒）、自然顫音額外加的量
    double hold_t = 0; bool has_prev_target = false; double prev_target = 0;
    // 變調延遲線
    std::vector<double> hist;
    int w = 0;
    int X = 360;                 // 交叉淡化長度
    double da = DMIN + JFLOOR;   // 目前讀取頭延遲
    double db = 0;               // 淡入中的新讀取頭延遲
    bool fading = false;
    int prog = 0;
    double xk = 1.0;
    long splices = 0;
    std::vector<double> cbuf;

    Tuner(double sr_ = 48000, double window_ms = 21.0) : sr(sr_), srD(sr_ / DEC) {
        set_window(window_ms);
        cbuf.assign(SEARCH + 1, 0.0);
        std::memset(dbuf, 0, sizeof(dbuf));
        tau_max = std::min(R - H - 1, (int)(srD / 70));
        tau_min = std::max(2, (int)(srD / 900));
        dtmp.assign(tau_max + 2, 0.0);
        cm.assign(tau_max + 2, 1.0);
        hist.assign(L, 0.0);
        update_scale();
    }
    void set_window(double ms) {
        W = (int)(sr * ms / 1000);
        X = std::max(192, W / 2);
        da = DMIN + JFLOOR; fading = false; prog = 0; xk = 1.0;
    }
    int jmin(double r) const { return (int)std::max((double)JFLOOR, std::ceil(2.0 * X * std::fabs(r - 1))); }

    void update_scale() {
        const int* iv = SCALE_MAJOR; int n = 7;
        if (p.scale == 1) { iv = SCALE_MINOR; n = 7; }
        else if (p.scale == 2) { iv = SCALE_PENT; n = 5; }
        else if (p.scale == 3) { iv = SCALE_BLUES; n = 6; }
        for (int i = 0; i < 12; i++) allowed[i] = false;
        for (int i = 0; i < n; i++) allowed[(iv[i] + p.key) % 12] = true;
    }

    double sticky_note(double t, double x) {
        if (has_held && std::fabs(x - held) < p.sticky && t != held) return held;
        held = t; has_held = true;
        return t;
    }

    static int pmod(int a, int m) { return ((a % m) + m) % m; }

    double quantize(double d) {
        int m = p.mode;
        if (m == M_NATURAL || m == M_ELECTRO) {
            int k = keyt.key;
            if (k < 0) return (double)std::lround(d);
            bool al[12] = {false};
            for (int i = 0; i < 7; i++) al[(SCALE_MAJOR[i] + k) % 12] = true;
            int c0 = (int)std::lround(d), best = c0; double bd = 1e9;
            for (int c = c0 - 6; c <= c0 + 6; c++)
                if (al[pmod(c, 12)] && std::fabs(c - d) < bd) { bd = std::fabs(c - d); best = c; }
            return best;
        }
        if (m == M_CHROM) return (double)std::lround(d);
        if (m == M_ROBOT) return p.robot + 12 * std::round((d - p.robot) / 12);
        int c0 = (int)std::lround(d), best = c0; double bd = 1e9;
        for (int c = c0 - 6; c <= c0 + 6; c++)
            if (allowed[pmod(c, 12)] && std::fabs(c - d) < bd) { bd = std::fabs(c - d); best = c; }
        return best;
    }

    double detect() {
        const int tm = tau_max;
        const double* seg = dbuf + (R - (H + tm + 1));
        double e = 0;
        for (int j = 0; j < H; j++) { double v = seg[tm + 1 + j]; e += v * v; }
        if (std::sqrt(e / H) < 0.008) return 0.0;
        // 差分函數 d(tau) = sum (a[j] - seg[j+tau])^2，a = seg[0..H)
        double run = 0;
        cm[0] = 1;
        for (int tau = 1; tau <= tm; tau++) {
            double s = 0;
            for (int j = 0; j < H; j++) { double df = seg[j] - seg[j + tau]; s += df * df; }
            dtmp[tau] = s;
            run += s;
            cm[tau] = run > 0 ? s * tau / run : 1.0;
        }
        int t = -1;
        for (int k = tau_min; k < tm; k++) if (cm[k] < 0.15) { t = k; break; }
        if (t < 0) return 0.0;
        while (t + 1 < tm && cm[t + 1] < cm[t]) t++;
        double tr = t;
        if (t > 1 && t < tm) {
            double a0 = cm[t - 1], b0 = cm[t], c0 = cm[t + 1];
            double den = a0 - 2 * b0 + c0;
            if (std::fabs(den) > 1e-9) tr = t + (a0 - c0) / (2 * den);
        }
        double ff = srD / tr;
        return (ff >= 70 && ff <= 900) ? ff : 0.0;
    }

    inline double hread(double delay) const {
        double pos = (double)w - delay;
        double fl = std::floor(pos);
        long i1 = (long)fl;
        double t = pos - fl;
        auto at = [&](long i) { return hist[(size_t)(((i % L) + L) % L)]; };
        double y0 = at(i1 - 1), y1 = at(i1), y2 = at(i1 + 1), y3 = at(i1 + 2);
        double c1 = 0.5 * (y2 - y0), c2 = y0 - 2.5 * y1 + 2 * y2 - 0.5 * y3, c3 = 0.5 * (y3 - y0) + 1.5 * (y1 - y2);
        return ((c3 * t + c2) * t + c1) * t + y1;
    }
    inline double hat(long i) const { return hist[(size_t)(((i % L) + L) % L)]; }

    // 接合：讀取頭往回（升調）或往前（降調）跳整數個週期，找波形最像的位置
    void splice(double r) {
        const int Lc = CORR_LEN, S = SEARCH;
        const int J0 = jmin(r);
        long src = (long)std::floor((double)w - da);
        double eref = 0;
        for (int j = 0; j < Lc; j++) { double v = hat(src - Lc + j); eref += v * v; }
        double nref = std::sqrt(eref) + 1e-9;
        // 候選段 J：升調 [src-J-Lc, src-J)；降調 [src+J-Lc, src+J)
        long st = (r > 1) ? src - J0 - Lc : src + J0 - Lc;
        int dir = (r > 1) ? -1 : 1;
        double esum = 0;
        for (int j = 0; j < Lc; j++) { double v = hat(st + j); esum += v * v; }
        double cm = -1e18;
        for (int k = 0; k <= S; k++) {
            long b0 = st + (long)dir * k;
            if (k > 0) {
                if (dir < 0) { double add = hat(b0), rem = hat(b0 + Lc); esum += add * add - rem * rem; }
                else { double add = hat(b0 + Lc - 1), rem = hat(b0 - 1); esum += add * add - rem * rem; }
            }
            double c = 0;
            for (int j = 0; j < Lc; j++) c += hat(src - Lc + j) * hat(b0 + j);
            double v = c / ((std::sqrt(std::max(esum, 0.0) + 1e-12)) * nref);
            cbuf[k] = v;
            if (v > cm) cm = v;
        }
        int j = 0;
        while (j < S && cbuf[j] < cm - 0.03) j++;
        while (j + 1 <= S && cbuf[j + 1] > cbuf[j]) j++;
        int J = J0 + j;
        db = (r > 1) ? da + J : da - J;
        xk = std::min(1.0, std::max(0.0, (cbuf[j] - 0.3) / 0.4));
        prog = 0; fading = true; splices++;
    }

    void process(double* x, int n) {
        const double dt = n / sr;
        const double blocks = n / REF_BLOCK;
        bool active = p.mode != M_OFF;
        // 降取樣餵偵測器
        for (int i = 0; i < n; i++) {
            acc += x[i];
            if (++accN == DEC) {
                std::memmove(dbuf, dbuf + 1, sizeof(double) * (R - 1));
                dbuf[R - 1] = acc / DEC;
                acc = 0; accN = 0;
            }
        }
        double ff = detect();
        f = ff;
        if (ff > 0) {
            double raw = 69 + 12 * std::log2(ff / 440.0);
            double pred = raw;
            if (unv_t <= 0 && std::fabs(raw - raw_prev) < 0.7 * std::max(1.0, blocks)) {
                double slope_per_ref = (raw - raw_prev) / std::max(blocks, 1e-6);
                pred = raw + slope_per_ref * p.predict;
            }
            raw_prev = raw;
            unv_t = 0;
            det = pred;
            keyt.add(raw, dt);
            key_timer += dt;
            if (key_timer >= 40 * REF_BLOCK / sr) { key_timer = 0; keyt.update(); }
            double a = 1 - std::exp(-dt / 0.08);
            dsm = (std::fabs(raw - dsm) > 1.0) ? raw : dsm + (raw - dsm) * a;
            double a2 = 1 - std::exp(-dt / 0.25);
            vc = (std::fabs(raw - vc) > 1.5) ? raw : vc + (raw - vc) * a2;
            double cs = p.card_shift;
            if (p.mode == M_ROBOT) target = quantize(det) + cs;
            else if (p.mode == M_NATURAL) target = sticky_note(quantize(dsm + cs), dsm + cs);
            else target = active ? sticky_note(quantize(det + cs), det + cs) : det + cs;
            voiced = true;
        } else {
            unv_t += dt;
            if (unv_t > 3 * REF_BLOCK / sr) { voiced = false; has_held = false; }
        }

        double want, alpha;
        if (active && voiced) {
            if (has_prev_target && target == prev_target) hold_t += dt; else hold_t = 0;
            prev_target = target; has_prev_target = true;
            double cs = p.card_shift;
            if (p.mode == M_NATURAL) {
                double err = target - (dsm + cs);
                double ae = std::fabs(err);
                // 容許誤差：差不到 tol 的不修；超過的只修「超過的部分」（柔和過渡，不會突然跳）
                double e2 = ae <= p.tol ? 0.0 : (err > 0 ? 1 : -1) * (ae - p.tol);
                want = cs + e2 * p.strength + p.shift;
            }
            else want = cs + (target - (det + cs)) * p.strength + p.shift;
            double wgt = 1.0;
            if (p.flex > 0) {
                double ref = (p.mode == M_NATURAL) ? dsm : det;
                double dev = std::fabs(target - (ref + cs));
                double thr = 0.5 - 0.4 * p.flex;
                wgt = std::min(1.0, std::max(0.0, (thr + 0.12 - dev) / 0.12));
                want = cs + p.shift + (want - cs - p.shift) * wgt;
            }
            // 自然顫音（dB）：放大／縮小本身的顫音；直接作用，不經過修正速度的平滑
            vt = 0;
            if (std::fabs(p.vibrato) > 0.01 && p.mode != M_ROBOT) {
                double v0 = (std::pow(10.0, p.vibrato / 20) - 1) * std::min(1.5, std::max(-1.5, det - vc));
                vt = (p.mode == M_NATURAL) ? v0 : v0 * (1 - p.strength * wgt);
            }
            double tau = p.speed * 0.4 + p.humanize * 0.3 * std::min(1.0, hold_t / 0.35);
            alpha = tau > 0.0005 ? 1 - std::exp(-n / (sr * tau)) : 1.0;
        } else {
            want = p.shift + p.card_shift;
            alpha = 1 - std::pow(1 - 0.15, blocks);
            vt = 0;
        }
        if (!voiced) { has_prev_target = false; hold_t = 0; }
        if (voiced && unv_t > 0) want = cur;
        cur += (want - cur) * alpha;

        double wet_t = (active || std::fabs(p.shift + p.card_shift) > 0.001) ? 1.0 : 0.0;
        if (wet < 0.0005 && wet_t == 0.0) {
            for (int i = 0; i < n; i++) { hist[w] = x[i]; w = (w + 1) % L; }
            wet = 0;
            return;
        }
        double r = std::pow(2.0, (cur + vt) / 12.0);
        double dr = r - 1.0;
        bool flat_mode = p.mode != M_OFF && p.mode != M_NATURAL;
        const double D0 = DMIN + (flat_mode ? LOOKAHEAD : 0);   // 讀取頭工作範圍下緣
        for (int i = 0; i < n; i++) {
            double xi = x[i];
            hist[w] = xi;
            w = (w + 1) % L;
            wet = wet_t + (wet - wet_t) * 0.997;
            if (!fading) {
                bool trig = false;
                if (dr > 1e-9) trig = da <= D0 + X * dr;
                else if (dr < -1e-9) trig = da >= D0 + jmin(r) + SEARCH + 2;
                if (trig) splice(r);
            }
            double s;
            if (fading) {
                double t = (prog + 0.5) / X;
                double eg = 0.5 - 0.5 * std::cos(PI * t);
                double gin = xk * eg + (1 - xk) * std::sin(0.5 * PI * t);
                double gout = xk * (1 - eg) + (1 - xk) * std::cos(0.5 * PI * t);
                s = gout * hread(da) + gin * hread(db);
                prog++;
                da = std::max(DMIN, da - dr);
                db = std::max(DMIN, db - dr);
                if (prog >= X) { da = db; fading = false; }
            } else {
                s = hread(da);
                da = std::max(DMIN, da - dr);
            }
            x[i] = xi * (1 - wet) + s * wet;
        }
    }
};

// ---------------------------------------------------------------- 壓縮
struct Compressor {
    double gain_db = 0, amount = 0.4;
    void process(double* x, int n) {
        double c = amount;
        double blocks = n / REF_BLOCK;
        if (c <= 0.001) {
            gain_db *= std::pow(0.9, blocks);
            if (std::fabs(gain_db) < 0.01) return;
        }
        double e = 0;
        for (int i = 0; i < n; i++) e += x[i] * x[i];
        double level = 20 * std::log10(std::max(std::sqrt(e / n), 1e-5));
        double thr = -6 - 30 * c, ratio = 1 + 7 * c;
        double tgt = c > 0.001 ? std::min(0.0, (thr - level) * (1 - 1 / ratio)) : 0.0;
        double coef = tgt < gain_db ? 1 - std::pow(0.4, blocks) : 1 - std::pow(0.92, blocks);
        double nw = gain_db + (tgt - gain_db) * coef;
        double mk = 1.0 + 0.4 * c;
        for (int i = 0; i < n; i++) {
            double gdb = gain_db + (nw - gain_db) * i / n;
            x[i] *= std::pow(10.0, gdb / 20) * mk;
        }
        gain_db = nw;
    }
};

// ---------------------------------------------------------------- 混響
// 類型：(延遲倍率, 殘響長度, 明亮度, 預延遲 ms, 立體寬度, 低頻清除 Hz)，順序與 dsp.py REVERB_TYPES 相同
struct ReverbType { double scale, size, bright, pre, width, lowcut; };
static const int REVERB_NTYPES = 6;
static const ReverbType REVERB_TYPES[REVERB_NTYPES] = {
    {1.00, 0.62, 1.00, 0.0, 1.0, 0.0},     // 經典
    {0.55, 0.45, 0.60, 4.0, 0.7, 150.0},   // 房間
    {0.80, 0.60, 0.85, 12.0, 1.0, 200.0},  // 人聲板
    {1.25, 0.75, 0.55, 25.0, 1.0, 120.0},  // 大廳
    {1.55, 0.90, 0.45, 45.0, 1.0, 100.0},  // 教堂
    {0.85, 0.72, 0.30, 25.0, 1.0, 0.0},    // CD 絲滑板式（Valhalla Smooth Plate／1970s 風格）
};
static const int REVERB_LAYERS = 3;

// Freeverb 式混響核心（記憶體一開始就配好，換類型不會在聲音執行緒裡配置記憶體）
struct ReverbCore {
    static const int CMAX = 3200, AMAX = 1400;
    double sr = 48000; int spread = 0;
    std::vector<double> cb[8], ab[4];
    int lc[8] = {0}, la[4] = {0}, ic[8] = {0}, ia[4] = {0};
    double fl[8] = {0};
    double g = 0.82, damp = 0.0;
    void init(double s, double scale, int spr) {
        sr = s; spread = spr;
        for (auto& v : cb) v.assign(CMAX, 0.0);
        for (auto& v : ab) v.assign(AMAX, 0.0);
        set_scale(scale);
    }
    void set_scale(double scale) {
        static const int COMB[8] = {1116, 1188, 1277, 1356, 1422, 1491, 1557, 1617};
        static const int ALLP[4] = {347, 441, 556, 701};
        double s = sr / 44100.0 * scale;
        int sp = (int)(spread * sr / 44100.0);
        for (int k = 0; k < 8; k++) { lc[k] = std::min(CMAX, (int)(COMB[k] * s) + sp); ic[k] = 0; fl[k] = 0; std::fill(cb[k].begin(), cb[k].end(), 0.0); }
        for (int k = 0; k < 4; k++) { la[k] = std::min(AMAX, (int)(ALLP[k] * s) + sp); ia[k] = 0; std::fill(ab[k].begin(), ab[k].end(), 0.0); }
    }
    inline double tick(double x) {
        double accv = 0;
        for (int k = 0; k < 8; k++) {
            double& slot = cb[k][ic[k]];
            fl[k] = slot * (1 - damp) + fl[k] * damp;
            double y = x + g * fl[k];
            slot = y;
            if (++ic[k] >= lc[k]) ic[k] = 0;
            accv += y;
        }
        double y = accv * 0.1;
        for (int k = 0; k < 4; k++) {
            double& slot = ab[k][ia[k]];
            double wh = slot;
            double ww = y + 0.5 * wh;
            y = -0.5 * ww + wh;
            slot = ww;
            if (++ia[k] >= la[k]) ia[k] = 0;
        }
        return y;
    }
};

// 一層混響（混音台上的一條 FX 通道）
struct ReverbLayer {
    double sr = 48000;
    ReverbCore L, R;
    int type = 0;
    double mix = 0, size = 0.6, bright = 1.0, pre_ms = 0, width = 1.0, lowcut = 0;
    std::vector<double> pbuf;
    int pw = 0, pre = 0;
    Biquad hp; bool hp_on = false; double hp_f = -1;
    Biquad lpL, lpR; bool lp_on = false; double lp_f = -1;   // 高頻柔化
    void init(double s) {
        sr = s;
        L.init(s, 1.0, 0); R.init(s, 1.0, 23);
        pbuf.assign((size_t)(s * 0.12), 0.0);
    }
    void set_type(int t) {
        t = std::max(0, std::min(REVERB_NTYPES, t));
        if (t == type) return;
        type = t;
        if (type > 0) {
            L.set_scale(REVERB_TYPES[type - 1].scale); R.set_scale(REVERB_TYPES[type - 1].scale);
            std::fill(pbuf.begin(), pbuf.end(), 0.0);
        }
    }
    void update() {
        auto cl = [](double v) { return std::min(1.0, std::max(0.0, v)); };
        for (ReverbCore* c : {&L, &R}) { c->g = 0.75 + 0.22 * cl(size); c->damp = (1.0 - cl(bright)) * 0.7; }
        pre = (int)(std::min(100.0, std::max(0.0, pre_ms)) * sr / 1000);
        double f = std::min(800.0, std::max(0.0, lowcut));
        if (f != hp_f) {
            hp_f = f;
            hp_on = f >= 20;
            if (hp_on) hp = Biquad(BQ_HP, f, sr, 0, 0.707);
        }
        double b = cl(bright);
        double fl = 3000.0 + 15000.0 * b * b;
        if (fl != lp_f) {
            lp_f = fl;
            lp_on = fl < 17500;
            if (lp_on) { lpL = Biquad(BQ_LP, fl, sr, 0, 0.707); lpR = Biquad(BQ_LP, fl, sr, 0, 0.707); }
        }
    }
    bool active() const { return type > 0 && mix > 0.001; }
    inline void tick(double y, double& m, double& sd) {
        double x = hp_on ? hp.tick(y) : y;
        int P = (int)pbuf.size();
        pbuf[pw] = x;
        double xin = pbuf[(pw - pre + P) % P];
        if (++pw >= P) pw = 0;
        double l = L.tick(xin), r = R.tick(xin);
        if (lp_on) { l = lpL.tick(l); r = lpR.tick(r); }
        double k = mix * 1.6 * std::sqrt(2.0);
        m = (l + r) * 0.5 * k;
        sd = (l - r) * 0.5 * k * std::min(1.0, std::max(0.0, width));
    }
};

// ---------------------------------------------------------------- 人聲質感（CD 質感）
// 人聲加寬：四個稍微延遲、音高微微飄動的副本，左右分開（像 Doubler 外掛）
struct Doubler {
    double sr; std::vector<double> buf; int L = 1 << 13, w = 0; long long t = 0;
    Doubler(double s = 48000) : sr(s), buf(1 << 13, 0.0) {}
    inline void tick(double y, double thick, double widen, double& m, double& sd) {
        static const double V[4][4] = {{9.4, 0.25, 0.31, -1.0}, {16.0, 0.30, 0.23, 1.0}, {23.7, 0.35, 0.37, -0.8}, {21.0, 0.28, 0.27, 0.8}};
        buf[w] = y;
        double tt = (double)t / sr, mid = 0, side = 0;
        for (int k = 0; k < 4; k++) {
            double d = (V[k][0] + V[k][1] * std::sin(2 * PI * V[k][2] * tt + V[k][0])) * sr / 1000.0;
            double pos = (double)w - d;
            double fl = std::floor(pos); long i0 = (long)fl; double fr = pos - fl;
            double a = buf[(size_t)(((i0 % L) + L) % L)], b = buf[(size_t)((((i0 + 1) % L) + L) % L)];
            double v = a * (1 - fr) + b * fr;
            mid += v; side += v * V[k][3];
        }
        w = (w + 1) % L; t++;
        m = mid * 0.18 * thick; sd = side * 0.18 * widen;
    }
};

// 響度：自動把人聲穩定在好聽的大小，再柔和收頂；沒聲音時不放大底噪
struct Leveler {
    double sr, gdb = 0, nf = -60;   // nf＝目前的底噪大小（自動追蹤）
    Leveler(double s = 48000) : sr(s) {}
    void process(double* y, int n, double amt) {
        if (amt <= 0.001) {
            gdb *= 0.9;
            if (std::fabs(gdb) < 0.01) { gdb = 0; return; }
        }
        double e = 0;
        for (int i = 0; i < n; i++) e += y[i] * y[i];
        double lvl = 20 * std::log10(std::sqrt(e / n + 1e-12));
        double target = -20.0 + 4.0 * amt, want, tau;
        // 底噪追蹤：比底噪小就立刻跟下去，比底噪大就每秒慢慢升 3 dB（最多到 -42 dB，所以 -32 dB 以上的歌聲一定會被拉上來）
        if (lvl < nf) nf = lvl; else nf = std::min(-42.0, nf + 3.0 * n / sr);
        // 比底噪大不到 10 dB＝沒出聲：不放大，慢慢回到 0 dB（以前會把雜音越拉越大）
        if (lvl < std::max(-48.0, nf + 10.0) || amt <= 0.001) { want = std::min(0.0, gdb); tau = 0.3; }
        else {
            want = std::max(-9.0, std::min(12.0, target - lvl)) * amt;
            tau = want < gdb ? 0.12 : 0.9;
        }
        double dt = n / sr;
        double nw = gdb + (want - gdb) * (1 - std::exp(-dt / tau));
        for (int i = 0; i < n; i++) {
            double g = std::pow(10.0, (gdb + (nw - gdb) * (i + 1) / n) / 20);
            double z = y[i] * g, a = std::fabs(z);
            if (a > 0.7) z = (z > 0 ? 1 : -1) * (0.7 + 0.28 * std::tanh((a - 0.7) / 0.28));
            y[i] = z;
        }
        gdb = nw;
    }
};

// ---------------------------------------------------------------- 電音質感（雙重唱＋亮度，不加回音）
struct ElectroLayer {
    double sr;
    std::vector<double> buf;
    int L = 1 << 15, w = 0;
    long long t = 0;
    Biquad air, pres;
    ElectroLayer(double s = 48000) : sr(s), buf(1 << 15, 0.0), air(BQ_HIGHSHELF, 9000, s, 5.0), pres(BQ_PEAKING, 4500, s, 2.5, 0.9) {}
    void process(double* y, int n, double amt) {
        for (int i = 0; i < n; i++) {
            buf[w] = y[i];
            double tt = (double)(t + i) / sr;
            double dbl = 0;
            const double P[2][4] = {{0.017, 0.0022, 0.37, 0.0}, {0.023, 0.0026, 0.29, 2.1}};
            for (int k = 0; k < 2; k++) {
                double d = (P[k][0] + P[k][1] * std::sin(2 * PI * P[k][2] * tt + P[k][3])) * sr;
                double pos = (double)w - d;
                double fl = std::floor(pos); long i0 = (long)fl; double fr = pos - fl;
                double a = buf[(size_t)(((i0 % L) + L) % L)], b = buf[(size_t)((((i0 + 1) % L) + L) % L)];
                dbl += a * (1 - fr) + b * fr;
            }
            dbl *= 0.5;
            w = (w + 1) % L;
            double wetv = y[i] + amt * 0.55 * dbl;
            double br = pres.tick(air.tick(wetv));
            y[i] = y[i] * (1 - amt) + br * amt;
        }
        t += n;
    }
};

// ---------------------------------------------------------------- 金屬共鳴
struct MetalComb {
    // 共鳴強度與延遲長度逐點平滑（約 8 ms／6 ms），開關或換音時不會「啵」
    double sr; std::vector<double> buf; int L = 4096, w = 0; double D = 200, Dt = 200, g = 0, kg, kd;
    MetalComb(double s = 48000) : sr(s), buf(4096, 0.0) {
        kg = 1 - std::exp(-1.0 / (0.008 * s)); kd = 1 - std::exp(-1.0 / (0.006 * s));
    }
    void process(double* x, int n, double amt, bool has_midi, double midi) {
        if (amt <= 0.001 && g < 1e-4) {
            for (int i = 0; i < n; i++) { buf[w] = x[i]; w = (w + 1) % L; }
            g = 0;
            return;
        }
        if (has_midi) {
            double ff = 440.0 * std::pow(2.0, (midi - 69) / 12);
            Dt = std::min((double)(L - 2), std::max(20.0, sr / ff));
        }
        double gt = (has_midi && amt > 0.001) ? (0.35 + 0.35 * amt) : 0.0;
        for (int i = 0; i < n; i++) {
            g += (gt - g) * kg;
            D += (Dt - D) * kd;
            int i0 = (int)D; double fr = D - i0;
            double a = buf[(size_t)(((w - i0) % L + L) % L)], b = buf[(size_t)(((w - i0 - 1) % L + L) % L)];
            double y = x[i] + g * (a * (1 - fr) + b * fr);
            buf[w] = y; w = (w + 1) % L;
            x[i] = x[i] * (1 - amt) + y * (1 - g) * amt;
        }
    }
};

// ---------------------------------------------------------------- 去齒音
struct DeEsser {
    Biquad hp1, hp2, shelf; double g = 1.0, reduced = 0;
    std::vector<double> tmp;
    DeEsser(double sr = 48000) : hp1(BQ_HP, 5000, sr, 0, 0.7), hp2(BQ_HP, 5000, sr, 0, 0.7), shelf(BQ_HIGHSHELF, 4500, sr, 0.0) {}
    void process(double* y, int n, double amt) {
        if ((int)tmp.size() < n) tmp.resize(n);
        double s = 0, t = 0;
        for (int i = 0; i < n; i++) {
            double hb = hp2.tick(hp1.tick(y[i]));
            s += hb * hb; t += y[i] * y[i];
        }
        if (amt <= 0.001) { g = 1.0; shelf.set_gain(0.0); return; }
        s = std::sqrt(s / n) + 1e-9; t = std::sqrt(t / n) + 1e-9;
        double rel = 20 * std::log10(s / t);
        double thr = -6.0 - 8.0 * amt;
        double excess = std::max(0.0, rel - thr);
        double gdb = excess > 0 ? -std::min(14.0 * amt + 2.0, excess * (0.6 + 0.4 * amt)) : 0.0;
        double want = std::pow(10.0, gdb / 20);
        double blocks = n / REF_BLOCK;
        double g_end = want < g ? want : g + (want - g) * (1 - std::pow(0.7, blocks));
        g = g_end;
        reduced = -20 * std::log10(std::max(g_end, 1e-6));
        shelf.set_gain(reduced > 0.05 ? -reduced : 0.0);
        shelf.run(y, n);
    }
};

// ---------------------------------------------------------------- 限制器
struct Limiter {
    double g = 1.0, ceiling = 0.89, rel, reduced = 0;
    Limiter(double sr = 48000, double c = 0.89, double release = 0.15) : ceiling(c) { rel = std::exp(-1.0 / (release * sr)); }
    void process(double* x, int n) {
        if (n <= 0) return;
        double peak = 0;
        for (int i = 0; i < n; i++) peak = std::max(peak, std::fabs(x[i]));
        double want = peak > 1e-9 ? std::min(1.0, ceiling / peak) : 1.0;
        double g_end = want < g ? want : want + (g - want) * std::pow(rel, n);
        bool ramp = g_end > g;
        double g0 = g;
        g = g_end;
        reduced = -20 * std::log10(std::max(g_end, 1e-6));
        const double c = ceiling;
        for (int i = 0; i < n; i++) {
            double gi = ramp ? g0 + (g_end - g0) * i / n : g_end;
            double v = x[i] * gi;
            double a = std::fabs(v);
            if (a > c) v = (v > 0 ? 1 : -1) * (c + (1 - c) * std::tanh((a - c) / (1 - c)));
            x[i] = v;
        }
    }
};

// ---------------------------------------------------------------- 聲線
struct ColorBand { double f, g, q; };
static const ColorBand COLORS[6][5] = {
    {{0, 0, 1}, {0, 0, 1}, {0, 0, 1}, {0, 0, 1}, {0, 0, 1}},                                   // 0 原本
    {{200, -1.5, 1.0}, {2000, 2.5, 1.8}, {4000, 1.5, 0.8}, {8000, -1.5, 1.0}, {16000, 1.0, 0.3}},  // 1 人聲修飾
    {{200, -3, 1.0}, {2000, 5, 1.8}, {4000, 3, 0.8}, {8000, -3, 1.0}, {16000, 2, 0.3}},            // 2 少女甜嗓
    {{80, 4, 0.7}, {250, 3, 1.2}, {800, -2, 2.0}, {3000, -3, 1.5}, {12000, 2, 0.5}},               // 3 御姐煙嗓
    {{80, -3, 1.2}, {250, -2, 1.0}, {1500, 3, 0.8}, {4000, 4, 1.0}, {10000, 3, 1.2}},              // 4 古風戲腔
    {{300, 6, 0.5}, {1200, -4, 2.0}, {4000, 3, 0.8}, {8000, 6, 1.5}, {12000, 5, 0.3}},             // 5 ASMR
};

// ---------------------------------------------------------------- 降噪（靜音時壓掉細小雜音）
// 向下擴展器：聲音低於門檻時慢慢壓低，一出聲立刻打開；不看未來的聲音，所以不增加延遲。
struct NoiseGate {
    double sr, env = 0, gdb = 0, hold = 0;
    explicit NoiseGate(double s = 48000) : sr(s) {}
    // amt 0～1：0＝不處理；越大門檻越高、壓得越多
    void process(double* y, int n, double amt) {
        if (n <= 0) return;
        double ss = 0;                                   // 用平均音量判斷（細小雜音的尖峰不會讓它打開）
        for (int i = 0; i < n; i++) ss += y[i] * y[i];
        double rms = std::sqrt(ss / n);
        env = std::max(rms, env * std::exp(-n / (sr * 0.05)));
        double tgt = 0;
        if (amt > 0.001) {
            double lvl = 20 * std::log10(env + 1e-9);
            double thr = -78.0 + 33.0 * amt;          // -78 dB ～ -45 dB（平均音量）
            double range = -(10.0 + 30.0 * amt);      // 最多壓 10 ～ 40 dB
            if (lvl >= thr) { hold = 0.15 * sr; tgt = 0; }
            else if (hold > 0) { hold -= n; tgt = 0; }
            else tgt = std::max(range, (lvl - thr) * 4.0);
        } else hold = 0;
        double g0db = gdb;
        if (tgt > gdb) gdb = tgt;                                        // 出聲：立刻打開
        else gdb += (tgt - gdb) * (1 - std::exp(-n / (sr * 0.08)));      // 安靜：慢慢壓下去
        if (g0db > -0.01 && gdb > -0.01) return;
        double g0 = std::pow(10.0, g0db / 20), g1 = std::pow(10.0, gdb / 20);
        for (int i = 0; i < n; i++) y[i] *= g0 + (g1 - g0) * (i + 1) / n;
    }
};

// ---------------------------------------------------------------- 整合
struct FxParams {
    bool phone = false; double phone_dist = 0.25; bool machine = false;
    double eq_lo = 0, eq_mid = 0, eq_hi = 0, comp = 0.4;
    bool card = false; double electro_fx = 0; int color = 0; double deess = 0, metal = 0, robot_fx = 0;
    double loud = 0, clarity = 0, thick = 0, widen = 0;   // 人聲質感（預設關閉）
    double gate = 0;                                      // 降噪（預設關閉）
};

struct VoiceFx {
    static constexpr double CARD_SHIFT = 1.5;
    double sr;
    Tuner tuner;
    FxParams p;
    Biquad card_eq[2];
    Biquad ph[4];
    double ph_mix = 0;
    Biquad mch[6];
    double m_mix = 0;
    Biquad eq[3];
    Compressor comp;
    ElectroLayer electro;
    DeEsser deesser;
    MetalComb metal;
    Biquad color_eq[5];
    int color_cur = -1;
    ReverbLayer layers[REVERB_LAYERS];
    Biquad clar[3];
    Doubler doubler;
    Leveler leveler;
    NoiseGate gate;
    std::vector<float> side;
    std::vector<float> rv_wet, rv_side;   // 混響的部分（耳機混響用）        // 混響的左右差（只給耳機做立體寬度）
    double ring_ph = 0;
    std::vector<double> t1, t2;

    VoiceFx(double s = 48000, double window_ms = 21.0)
        : sr(s), tuner(s, window_ms), electro(s), deesser(s), metal(s), doubler(s), leveler(s), gate(s) {
        clar[0] = Biquad(BQ_PEAKING, 250, s, 0.0, 1.0); clar[1] = Biquad(BQ_PEAKING, 5000, s, 0.0, 1.0); clar[2] = Biquad(BQ_HIGHSHELF, 9000, s, 0.0);
        for (auto& L : layers) L.init(s);
        side.assign(8192, 0.f);
        card_eq[0] = Biquad(BQ_LOWSHELF, 350, s, -6.0);
        card_eq[1] = Biquad(BQ_PEAKING, 3200, s, -5.0, 1.4);
        ph[0] = Biquad(BQ_HP, 320, s, 0, 0.9); ph[1] = Biquad(BQ_HP, 320, s, 0, 0.9);
        ph[2] = Biquad(BQ_LP, 3300, s, 0, 0.9); ph[3] = Biquad(BQ_LP, 3300, s, 0, 0.9);
        mch[0] = Biquad(BQ_HP, 450, s, 0, 0.7); mch[1] = Biquad(BQ_HP, 450, s, 0, 0.7);
        mch[2] = Biquad(BQ_LP, 3400, s, 0, 0.7); mch[3] = Biquad(BQ_LP, 3400, s, 0, 0.7);
        mch[4] = Biquad(BQ_PEAKING, 1400, s, 5.0, 1.0); mch[5] = Biquad(BQ_PEAKING, 700, s, -2.0, 1.2);
        eq[0] = Biquad(BQ_LOWSHELF, 150, s, 0); eq[1] = Biquad(BQ_PEAKING, 2200, s, 0, 0.8); eq[2] = Biquad(BQ_HIGHSHELF, 7000, s, 0);
        apply();
    }

    void apply() {
        tuner.p.card_shift = p.card ? CARD_SHIFT : 0.0;
        if (p.color != color_cur) {
            color_cur = p.color;
            int c = std::max(0, std::min(5, p.color));
            for (int k = 0; k < 5; k++) {
                const ColorBand& b = COLORS[c][k];
                color_eq[k] = Biquad(BQ_PEAKING, b.f > 0 ? std::min(b.f, sr * 0.45) : 1000.0, sr, b.g, b.q);
            }
        }
        eq[0].set_gain(p.eq_lo); eq[1].set_gain(p.eq_mid); eq[2].set_gain(p.eq_hi);
        clar[0].set_gain(-3.0 * p.clarity); clar[1].set_gain(2.5 * p.clarity); clar[2].set_gain(5.0 * p.clarity);
        comp.amount = p.comp;
    }

    // 混響參數：rv1_type、rv2_mix …（與 dsp.py 相同）；舊的 verb＝第 1 層「經典」
    bool set_reverb_key(const std::string& k, double v) {
        if (k == "verb") {
            ReverbLayer& L = layers[0];
            L.set_type(1); L.mix = v; L.size = (0.05 + 0.13 * v) / 0.22; L.bright = 1.0; L.pre_ms = 0; L.width = 1.0; L.lowcut = 0;
            L.update();
            return true;
        }
        if (k.size() < 5 || k[0] != 'r' || k[1] != 'v' || k[3] != '_') return false;
        int i = k[2] - '1';
        if (i < 0 || i >= REVERB_LAYERS) return false;
        ReverbLayer& L = layers[i];
        std::string f = k.substr(4);
        if (f == "type") L.set_type((int)v);
        else if (f == "mix") L.mix = v;
        else if (f == "size") L.size = v;
        else if (f == "bright") L.bright = v;
        else if (f == "pre") L.pre_ms = v;
        else if (f == "width") L.width = v;
        else if (f == "lowcut") L.lowcut = v;
        else return false;
        L.update();
        return true;
    }

    // info
    bool info_voiced() const { return tuner.voiced; }

    void process(float* io, int n0) {
        int n = (n0 + 3) / 4 * 4;
        if ((int)t1.size() < n) { t1.resize(n); t2.resize(n); }
        double* y = t1.data();
        for (int i = 0; i < n; i++) y[i] = i < n0 ? io[i] : 0.0;
        gate.process(y, n0, p.gate);   // 降噪：先把靜音時的細小雜音壓掉
        tuner.process(y, n);

        // 電話音
        double pt = p.phone ? 1.0 : 0.0;
        if (pt > 0 || ph_mix > 0.001) {
            double* z = t2.data();
            double k = p.phone_dist * 40;
            for (int i = 0; i < n; i++) {
                double v = y[i];
                for (auto& b : ph) v = b.tick(v);
                v = ((1 + k) * v) / (1 + k * std::fabs(v));
                z[i] = v * 1.6;
            }
            for (int i = 0; i < n; i++) { double r = ph_mix + (pt - ph_mix) * i / n; y[i] = y[i] * (1 - r) + z[i] * r; }
        } else for (auto& b : ph) b.reset();
        ph_mix = pt;
        // 答錄機
        double mt = p.machine ? 1.0 : 0.0;
        if (mt > 0 || m_mix > 0.001) {
            double* z = t2.data();
            for (int i = 0; i < n; i++) { double v = y[i]; for (auto& b : mch) v = b.tick(v); z[i] = v * 1.8; }
            for (int i = 0; i < n; i++) { double r = m_mix + (mt - m_mix) * i / n; y[i] = y[i] * (1 - r) + z[i] * r; }
        }
        m_mix = mt;
        // 聲卡變音音色
        if (p.card) { card_eq[0].run(y, n); card_eq[1].run(y, n); }
        else { card_eq[0].reset(); card_eq[1].reset(); }
        bool has_t = tuner.voiced && tuner.p.mode != M_OFF;
        // 機器人聲：環形調變（55 Hz）
        if (p.robot_fx > 0.001) {
            double a = 0.7 * p.robot_fx, inc = 2 * PI * 55.0 / sr;
            for (int i = 0; i < n; i++) {
                ring_ph += inc;
                if (ring_ph > 2 * PI) ring_ph -= 2 * PI;
                y[i] = y[i] * (1 - a) + y[i] * std::sin(ring_ph) * a * 1.6;
            }
        }
        metal.process(y, n, p.metal, has_t, tuner.target);
        if (p.electro_fx > 0.001) electro.process(y, n, p.electro_fx);
        for (auto& b : color_eq) b.run(y, n);
        for (auto& b : eq) b.run(y, n);
        for (auto& b : clar) b.run(y, n);
        deesser.process(y, n, p.deess);
        comp.process(y, n);
        leveler.process(y, n, p.loud);
        // 人聲加寬／厚度
        if ((int)side.size() < n) side.resize(n);
        std::fill(side.begin(), side.begin() + n, 0.f);
        {
            double th = p.thick, wd = p.widen;
            for (int i = 0; i < n; i++) {
                double m, sd;
                doubler.tick(y[i], th, wd, m, sd);
                if (th > 0.001 || wd > 0.001) { y[i] += m; side[i] = (float)sd; }
            }
        }
        // 混響：每一層各自加上去（中間＝直播聽得到；兩側＝耳機的立體寬度）
        // rv_wet／rv_side 另外記下混響的部分，讓耳機可以單獨把混響調小（直播不變）
        bool any = false;
        for (auto& L : layers) any = any || L.active();
        if ((int)rv_wet.size() < n) { rv_wet.resize(n); rv_side.resize(n); }
        std::fill(rv_wet.begin(), rv_wet.begin() + n, 0.f);
        std::fill(rv_side.begin(), rv_side.begin() + n, 0.f);
        if (any) {
            for (int i = 0; i < n; i++) {
                double dry = y[i], m = 0, sd = side[i], rs = 0;
                for (auto& L : layers) {
                    if (!L.active()) continue;
                    double mm, ss;
                    L.tick(dry, mm, ss);
                    m += mm; rs += ss;
                }
                y[i] = dry + m;
                side[i] = (float)(sd + rs);
                rv_wet[i] = (float)m; rv_side[i] = (float)rs;
            }
        }
        for (int i = 0; i < n0; i++) io[i] = (float)std::max(-1.0, std::min(1.0, y[i]));
    }
};

}  // namespace papa
