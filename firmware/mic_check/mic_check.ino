#include <Arduino.h>
#include "driver/i2s_std.h"

// mic_check : finds the INMP441 wiring by its DATA PATTERN (not by loudness), then shows a live bar.
//
// Why: the INMP441 sends 24-bit samples inside a 32-bit slot, so the lowest 8 bits of every word are 0.
// A floating / wrong pin gives random low bits, a dead mic gives all-zero words.
// A working mic therefore shows:  low byte = 0 almost always  AND  the value keeps changing (std > 20).
// (this relies on the INMP441 data format as I know it; the printed numbers let you verify it)
//
// Wiring assumed: VDD -> 3V3, GND -> GND, L/R -> GND, SCK/WS/SD wires on ESP32 pins 39/40/41 (any order).
// Wrong orders drive the mic's SD pin for about 1 s each (3.3 V logic on both sides; low risk, not zero).
// Bench test only: electrodes OFF the body.  Serial Monitor: 115200.

constexpr int CAND[3] = {39, 40, 41};
constexpr uint32_t FS = 16000;
constexpr size_t FRAMES = 512;                 // frames per read (stereo: 2 x 32 bit each)

i2s_chan_handle_t rx = nullptr;
int32_t buf[FRAMES * 2];

// NOTE: declared BEFORE the first function (the Arduino IDE puts function prototypes above the first function).
struct Stat { double mean, std, changes, lowZero; };

bool startI2S(int bclk, int ws, int din) {
  i2s_chan_config_t chan_cfg = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_AUTO, I2S_ROLE_MASTER);
  if (i2s_new_channel(&chan_cfg, NULL, &rx) != ESP_OK) return false;
  i2s_std_config_t std_cfg = {
    .clk_cfg = I2S_STD_CLK_DEFAULT_CONFIG(FS),
    .slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_32BIT, I2S_SLOT_MODE_STEREO),
    .gpio_cfg = {
      .mclk = I2S_GPIO_UNUSED,
      .bclk = (gpio_num_t)bclk,
      .ws = (gpio_num_t)ws,
      .dout = I2S_GPIO_UNUSED,
      .din = (gpio_num_t)din,
      .invert_flags = {
        .mclk_inv = false,
        .bclk_inv = false,
        .ws_inv = false,
      },
    },
  };
  if (i2s_channel_init_std_mode(rx, &std_cfg) != ESP_OK || i2s_channel_enable(rx) != ESP_OK) {
    i2s_del_channel(rx);
    rx = nullptr;
    return false;
  }
  return true;
}

void stopI2S() {
  if (!rx) return;
  i2s_channel_disable(rx);
  i2s_del_channel(rx);
  rx = nullptr;
}

bool readBlock() {
  size_t bytes = 0;
  esp_err_t e = i2s_channel_read(rx, buf, sizeof(buf), &bytes, 1000);
  return e == ESP_OK && bytes == sizeof(buf);
}

void measure(Stat& L, Stat& R, int blocks) {
  double s[2] = {0, 0}, q[2] = {0, 0};
  uint32_t ch[2] = {0, 0}, lz[2] = {0, 0}, n = 0;
  int32_t prev[2] = {0, 0};
  for (int b = 0; b < blocks; b++) {
    if (!readBlock()) continue;
    for (size_t i = 0; i < FRAMES; i++) {
      for (int side = 0; side < 2; side++) {
        int32_t raw = buf[2 * i + side];
        int32_t v = raw >> 8;                              // top 24 bits
        s[side] += v; q[side] += (double)v * v;
        if (v != prev[side]) ch[side]++;
        if ((raw & 0xFF) == 0) lz[side]++;
        prev[side] = v;
      }
      n++;
    }
  }
  if (n == 0) n = 1;
  Stat* out[2] = {&L, &R};
  for (int side = 0; side < 2; side++) {
    out[side]->mean = s[side] / n;
    out[side]->std = sqrt(max(0.0, q[side] / n - out[side]->mean * out[side]->mean));
    out[side]->changes = (double)ch[side] / n;
    out[side]->lowZero = (double)lz[side] / n;
  }
}

bool looksLikeMic(const Stat& st) {
  return st.lowZero >= 0.98 && st.std >= 20 && st.changes >= 0.2;
}

void setup() {
  Serial.begin(115200);
  unsigned long t0 = millis();
  while (!Serial && millis() - t0 < 3000) { delay(10); }
  delay(500);
  Serial.println("\n=== mic_check start (takes about 15 s) ===");

  const int perm[6][3] = {{0,1,2},{0,2,1},{1,0,2},{1,2,0},{2,0,1},{2,1,0}};
  int best = -1; bool bestRight = false; double bestStd = 1e18;
  for (int p = 0; p < 6; p++) {
    int bclk = CAND[perm[p][0]], ws = CAND[perm[p][1]], din = CAND[perm[p][2]];
    if (!startI2S(bclk, ws, din)) { Serial.printf("SCK=%d WS=%d SD=%d : I2S start failed\n", bclk, ws, din); continue; }
    readBlock(); readBlock();
    Stat L, R;
    measure(L, R, 24);
    stopI2S();
    pinMode(bclk, INPUT); pinMode(ws, INPUT); pinMode(din, INPUT);
    Serial.printf("SCK=%d WS=%d SD=%d | LEFT std=%.0f low8bits=0 in %.0f%% changes %.0f%% %s | RIGHT std=%.0f low8bits=0 in %.0f%% changes %.0f%% %s\n",
                  bclk, ws, din,
                  L.std, L.lowZero * 100, L.changes * 100, looksLikeMic(L) ? "<== MIC PATTERN" : "",
                  R.std, R.lowZero * 100, R.changes * 100, looksLikeMic(R) ? "<== MIC PATTERN" : "");
    for (int side = 0; side < 2; side++) {
      const Stat& st = side ? R : L;
      if (looksLikeMic(st) && st.std < bestStd) { bestStd = st.std; best = p; bestRight = side; }
    }
  }
  if (best < 0) {
    Serial.println("No microphone pattern found in any order.");
    Serial.println("Check: mic VDD -> 3V3 (really connected?), mic GND, L/R -> GND, and the 3 wires on pins 39/40/41.");
    Serial.println("All-zero numbers mean the mic is not sending anything (power or wiring). Random numbers mean a floating pin.");
    return;
  }
  int bclk = CAND[perm[best][0]], ws = CAND[perm[best][1]], din = CAND[perm[best][2]];
  Serial.printf(">>> MIC FOUND: SCK=%d WS=%d SD=%d, data on the %s slot\n", bclk, ws, din, bestRight ? "RIGHT (L/R is probably on 3V3)" : "LEFT");
  Serial.println("Stay quiet 3 s, then clap or talk (30 s). A real mic idles around -75..-60 dBFS and never shows exact zero.");
  if (!startI2S(bclk, ws, din)) { Serial.println("I2S restart failed"); return; }
  readBlock();
  unsigned long end = millis() + 30000;
  while (millis() < end) {
    double sum = 0, sq = 0, peak = 0; uint32_t n = 0;
    for (int b = 0; b < 3; b++) {
      if (!readBlock()) continue;
      for (size_t i = 0; i < FRAMES; i++) {
        double v = (double)(buf[2 * i + (bestRight ? 1 : 0)] >> 8);
        sum += v; sq += v * v; n++;
        if (fabs(v) > peak) peak = fabs(v);
      }
    }
    if (n == 0) continue;
    double mean = sum / n;
    double rms = sqrt(max(0.0, sq / n - mean * mean));
    double dbr = 20.0 * log10((rms + 1.0) / 8388608.0);
    double dbp = 20.0 * log10((peak + 1.0) / 8388608.0);
    int bars = (int)((dbr + 90.0) / 70.0 * 50.0);
    if (bars < 0) bars = 0;
    if (bars > 50) bars = 50;
    char line[64]; int k = 0;
    for (int i = 0; i < bars; i++) line[k++] = '#';
    line[k] = 0;
    Serial.printf("rms %6.1f dBFS | peak %6.1f dBFS |%s\n", dbr, dbp, line);
  }
  stopI2S();
  Serial.println("=== done (press RST to run again) ===");
}

void loop() { }
