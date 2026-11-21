#include <Arduino.h>
#include "driver/i2s_std.h"

// mic_scan : finds which of the 3 wires on ESP32 pins 39/40/41 is the INMP441's SCK, WS and SD,
// then shows a live loudness bar so you can clap and see it move.
//
// Wiring assumed:  INMP441 VDD -> 3V3,  GND -> GND,  L/R -> GND,  and its SCK/WS/SD wires on ESP32 pins 39, 40, 41 (any order).
// It tries all 6 orders. In a wrong order the ESP32 may briefly drive the microphone's SD output pin
// (3.3 V logic on both sides, about 1 s) - low risk but not zero. Do this on the bench, electrodes OFF the body.
//
// Open Serial Monitor at 115200.  Heuristic reading of the numbers:
//   std < 20          : no signal
//   20 .. 1,500,000   : looks like a microphone (quiet room usually a few hundred to a few tens of thousands)
//   > 1,500,000       : random garbage (floating pin), not a microphone

constexpr int CAND[3] = {39, 40, 41};
constexpr uint32_t FS = 16000;
constexpr size_t FRAMES = 512;                 // frames per read (stereo: 2 x 32 bit each)

i2s_chan_handle_t rx = nullptr;
int32_t buf[FRAMES * 2];

// NOTE: this type must be declared BEFORE the first function, because the Arduino IDE
// inserts function prototypes above the first function (they need to know this type).
struct Stat { double mean, std; uint32_t changes; };

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


// values are the top 24 bits of each 32-bit word (the INMP441 sends 24-bit data, left-justified)
void measure(Stat& L, Stat& R, int blocks) {
  double sL = 0, qL = 0, sR = 0, qR = 0;
  uint32_t cL = 0, cR = 0, n = 0;
  int32_t pl = 0, pr = 0;
  for (int b = 0; b < blocks; b++) {
    if (!readBlock()) continue;
    for (size_t i = 0; i < FRAMES; i++) {
      int32_t l = buf[2 * i] >> 8, r = buf[2 * i + 1] >> 8;
      sL += l; qL += (double)l * l; sR += r; qR += (double)r * r;
      if (l != pl) cL++;
      if (r != pr) cR++;
      pl = l; pr = r; n++;
    }
  }
  if (n == 0) n = 1;
  L.mean = sL / n; L.std = sqrt(max(0.0, qL / n - L.mean * L.mean)); L.changes = cL;
  R.mean = sR / n; R.std = sqrt(max(0.0, qR / n - R.mean * R.mean)); R.changes = cR;
}

const char* judge(double s) {
  if (s < 20) return "no signal";
  if (s < 1500000) return "looks like a mic";
  return "random garbage";
}

void setup() {
  Serial.begin(115200);
  unsigned long t0 = millis();
  while (!Serial && millis() - t0 < 3000) { delay(10); }
  delay(500);
  Serial.println("\n=== mic_scan start (takes about 15 s) ===");

  const int perm[6][3] = {{0,1,2},{0,2,1},{1,0,2},{1,2,0},{2,0,1},{2,1,0}};
  int best = -1; double bestStd = 0; bool bestRight = false;
  for (int p = 0; p < 6; p++) {
    int bclk = CAND[perm[p][0]], ws = CAND[perm[p][1]], din = CAND[perm[p][2]];
    if (!startI2S(bclk, ws, din)) { Serial.printf("SCK=%d WS=%d SD=%d : I2S start failed\n", bclk, ws, din); continue; }
    readBlock(); readBlock();                       // settle
    Stat L, R;
    measure(L, R, 24);                              // about 0.8 s
    stopI2S();
    pinMode(bclk, INPUT); pinMode(ws, INPUT); pinMode(din, INPUT);
    Serial.printf("SCK=%d WS=%d SD=%d : left std=%.0f (%s) | right std=%.0f (%s)\n",
                  bclk, ws, din, L.std, judge(L.std), R.std, judge(R.std));
    for (int side = 0; side < 2; side++) {
      double s = side ? R.std : L.std;
      if (s >= 20 && s < 1500000 && s > bestStd) { bestStd = s; best = p; bestRight = side; }
    }
  }
  if (best < 0) {
    Serial.println("No microphone found. Check: VDD -> 3V3, GND, L/R -> GND, and that the 3 wires really are on pins 39/40/41.");
    return;
  }
  int bclk = CAND[perm[best][0]], ws = CAND[perm[best][1]], din = CAND[perm[best][2]];
  Serial.printf(">>> FOUND: SCK=%d WS=%d SD=%d, data on the %s slot\n", bclk, ws, din, bestRight ? "RIGHT (L/R pin is NOT on GND)" : "left");
  Serial.println("Now clap or talk: the bar should move. (30 s)");
  if (!startI2S(bclk, ws, din)) { Serial.println("I2S restart failed"); return; }
  readBlock();
  unsigned long end = millis() + 30000;
  while (millis() < end) {
    Stat L, R;
    measure(L, R, 3);
    double rms = bestRight ? R.std : L.std;
    double db = 20.0 * log10((rms + 1.0) / 8388608.0);          // dB relative to full scale
    int bars = (int)((db + 90.0) / 70.0 * 50.0);                  // -90 dBFS .. -20 dBFS
    if (bars < 0) bars = 0; if (bars > 50) bars = 50;
    char line[96]; int k = 0;
    for (int i = 0; i < bars; i++) line[k++] = '#';
    line[k] = 0;
    Serial.printf("%6.1f dBFS |%s\n", db, line);
  }
  stopI2S();
  Serial.println("=== done (press RST to run again) ===");
}

void loop() { }
