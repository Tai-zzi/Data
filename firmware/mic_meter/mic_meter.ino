#include <Arduino.h>
#include "driver/i2s_std.h"

// mic_meter : fixed-pin microphone check with a live loudness bar (clap or talk and watch it move).
//
// Pins (from the mic_scan result): ESP32 pin 40 = BCLK (mic SCK), pin 41 = WS, pin 39 = DIN (mic SD).
// If the bar does NOT react to your voice, edit the three numbers below (try the other orders) and upload again.
// Data is read from the LEFT slot (INMP441 with L/R wired to GND).  Set USE_LEFT = false if L/R is wired to 3V3.
// Serial Monitor: 115200.
constexpr int PIN_BCLK = 40;
constexpr int PIN_WS   = 41;
constexpr int PIN_DIN  = 39;
constexpr bool USE_LEFT = true;

constexpr uint32_t FS = 16000;
constexpr size_t FRAMES = 512;                 // frames per read (stereo: 2 x 32 bit each)

i2s_chan_handle_t rx = nullptr;
int32_t buf[FRAMES * 2];

bool startI2S() {
  i2s_chan_config_t chan_cfg = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_AUTO, I2S_ROLE_MASTER);
  if (i2s_new_channel(&chan_cfg, NULL, &rx) != ESP_OK) return false;
  i2s_std_config_t std_cfg = {
    .clk_cfg = I2S_STD_CLK_DEFAULT_CONFIG(FS),
    .slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_32BIT, I2S_SLOT_MODE_STEREO),
    .gpio_cfg = {
      .mclk = I2S_GPIO_UNUSED,
      .bclk = (gpio_num_t)PIN_BCLK,
      .ws = (gpio_num_t)PIN_WS,
      .dout = I2S_GPIO_UNUSED,
      .din = (gpio_num_t)PIN_DIN,
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

bool readBlock() {
  size_t bytes = 0;
  esp_err_t e = i2s_channel_read(rx, buf, sizeof(buf), &bytes, 1000);
  return e == ESP_OK && bytes == sizeof(buf);
}

void setup() {
  Serial.begin(115200);
  unsigned long t0 = millis();
  while (!Serial && millis() - t0 < 3000) { delay(10); }
  delay(500);
  Serial.println("\n=== mic_meter start ===");
  Serial.printf("BCLK=%d WS=%d DIN=%d slot=%s\n", PIN_BCLK, PIN_WS, PIN_DIN, USE_LEFT ? "LEFT" : "RIGHT");
  if (!startI2S()) { Serial.println("I2S start failed"); return; }
  readBlock(); readBlock();
  Serial.println("Stay quiet for 3 seconds, then clap or talk. A real microphone idles around -75 to -65 dBFS.");
}

void loop() {
  if (!rx) return;
  double sum = 0, sq = 0, peak = 0;
  uint32_t n = 0;
  for (int b = 0; b < 3; b++) {                      // about 100 ms per line
    if (!readBlock()) continue;
    for (size_t i = 0; i < FRAMES; i++) {
      double v = (double)(buf[2 * i + (USE_LEFT ? 0 : 1)] >> 8);   // top 24 bits
      sum += v; sq += v * v; n++;
      if (fabs(v) > peak) peak = fabs(v);
    }
  }
  if (n == 0) return;
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
