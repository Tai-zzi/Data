#include <Arduino.h>
#include <SPI.h>
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/stream_buffer.h"
#include "driver/i2s_std.h"
#include "protocol.h"

// CoGoalTis firmware v2 : ADS1292R (1 kSPS, 2 ch) + INMP441 microphone (16 kHz) -> binary packets over USB serial.
// PC side:  python report_test.py COM4     or    python dashboard.py --source serial --port COM4 --baud 921600
//
// TEST_SIGNAL = 1 : ADS makes its own 1 Hz test wave (no electrodes).   TEST_SIGNAL = 0 : real electrodes.
// Microphone pins: set them to what mic_check found.  There is NO stimulation output in this firmware.
#define TEST_SIGNAL 0

// ADS1292R wiring (table wiring that worked)
constexpr int PIN_SCK   = 4;
constexpr int PIN_MOSI  = 5;
constexpr int PIN_MISO  = 6;
constexpr int PIN_CS    = 7;
constexpr int PIN_DRDY  = 15;
constexpr int PIN_START = 16;
constexpr int PIN_PWDN  = 17;

// INMP441 wiring  (CHANGE these to the result of mic_check if they differ)
constexpr int MIC_BCLK = 40;      // mic SCK
constexpr int MIC_WS   = 41;
constexpr int MIC_DIN  = 39;      // mic SD
constexpr bool MIC_LEFT = true;   // true when the mic's L/R pin is on GND
constexpr int AUDIO_SHIFT = 14;   // 24-bit sample >> 14 -> int16 (louder sounds clip, quiet sounds stay visible)

constexpr uint32_t SERIAL_BAUD = 921600;     // must match SERIAL_BAUD in config.py

constexpr uint8_t CMD_RESET = 0x06, CMD_SDATAC = 0x11, CMD_RDATAC = 0x10, CMD_RREG = 0x20, CMD_WREG = 0x40;
constexpr uint8_t REG_ID = 0x00, REG_CONFIG1 = 0x01, REG_CONFIG2 = 0x02, REG_CH1SET = 0x04, REG_CH2SET = 0x05;
constexpr uint8_t CONFIG1_1KSPS = 0x03;
#if TEST_SIGNAL
constexpr uint8_t CONFIG2_VAL = 0xA3;
constexpr uint8_t CHSET_VAL   = 0x05;
#else
constexpr uint8_t CONFIG2_VAL = 0xA0;
constexpr uint8_t CHSET_VAL   = 0x00;
#endif

SPISettings adsSpi(1000000, MSBFIRST, SPI_MODE1);

volatile uint32_t drdyCount = 0;
void IRAM_ATTR onDrdy() { drdyCount++; }

uint32_t handled = 0, lostSamples = 0, seqNo = 0, audioUnderrun = 0, audioDropped = 0;
uint16_t frameCount = 0;
uint64_t packetStartUs = 0;
int32_t frames[PKT_FRAMES * PKT_CH];
int16_t audioPkt[PKT_AUDIO];
int16_t audioTmp[PKT_AUDIO];
uint8_t packetBuf[PKT_MAX_BYTES];

i2s_chan_handle_t micChan = nullptr;
StreamBufferHandle_t audioSb = nullptr;
bool audioOn = false;

// ---------------- ADS1292R ----------------
void adsCommand(uint8_t cmd) {
  SPI.beginTransaction(adsSpi);
  digitalWrite(PIN_CS, LOW);  delayMicroseconds(10);
  SPI.transfer(cmd);          delayMicroseconds(50);
  digitalWrite(PIN_CS, HIGH);
  SPI.endTransaction();
  delayMicroseconds(50);
}

uint8_t adsRead(uint8_t reg) {
  SPI.beginTransaction(adsSpi);
  digitalWrite(PIN_CS, LOW);  delayMicroseconds(10);
  SPI.transfer(CMD_RREG | reg);   delayMicroseconds(50);
  SPI.transfer(0x00);             delayMicroseconds(50);
  uint8_t v = SPI.transfer(0x00); delayMicroseconds(50);
  digitalWrite(PIN_CS, HIGH);
  SPI.endTransaction();
  delayMicroseconds(50);
  return v;
}

void adsWrite(uint8_t reg, uint8_t val) {
  SPI.beginTransaction(adsSpi);
  digitalWrite(PIN_CS, LOW);  delayMicroseconds(10);
  SPI.transfer(CMD_WREG | reg);   delayMicroseconds(50);
  SPI.transfer(0x00);             delayMicroseconds(50);
  SPI.transfer(val);              delayMicroseconds(50);
  digitalWrite(PIN_CS, HIGH);
  SPI.endTransaction();
  delayMicroseconds(100);
}

bool writeVerify(uint8_t reg, uint8_t val, const char* name) {
  adsWrite(reg, val);
  uint8_t back = adsRead(reg);
  Serial.printf("  %s: wrote 0x%02X read 0x%02X %s\n", name, val, back, back == val ? "OK" : "MISMATCH");
  return back == val;
}

bool initAds() {
  digitalWrite(PIN_START, LOW);
  digitalWrite(PIN_PWDN, HIGH);
  delay(500);
  digitalWrite(PIN_PWDN, LOW);  delay(10);
  digitalWrite(PIN_PWDN, HIGH); delay(500);
  adsCommand(CMD_RESET);  delay(100);
  adsCommand(CMD_SDATAC); delay(10);
  uint8_t id = adsRead(REG_ID);
  Serial.printf("ADS ID = 0x%02X %s\n", id, (id == 0x73 || id == 0x53) ? "(OK)" : "(NOT FOUND)");
  if (id != 0x73 && id != 0x53) return false;
  bool ok = true;
  ok &= writeVerify(REG_CONFIG1, CONFIG1_1KSPS, "CONFIG1");
  ok &= writeVerify(REG_CONFIG2, CONFIG2_VAL, "CONFIG2");
  delay(300);
  ok &= writeVerify(REG_CH1SET, CHSET_VAL, "CH1SET");
  ok &= writeVerify(REG_CH2SET, CHSET_VAL, "CH2SET");
  return ok;
}

// ---------------- INMP441 (I2S) ----------------
bool startMic() {
  i2s_chan_config_t chan_cfg = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_AUTO, I2S_ROLE_MASTER);
  if (i2s_new_channel(&chan_cfg, NULL, &micChan) != ESP_OK) return false;
  i2s_std_config_t std_cfg = {
    .clk_cfg = I2S_STD_CLK_DEFAULT_CONFIG(16000),
    .slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_32BIT, I2S_SLOT_MODE_STEREO),
    .gpio_cfg = {
      .mclk = I2S_GPIO_UNUSED,
      .bclk = (gpio_num_t)MIC_BCLK,
      .ws = (gpio_num_t)MIC_WS,
      .dout = I2S_GPIO_UNUSED,
      .din = (gpio_num_t)MIC_DIN,
      .invert_flags = {
        .mclk_inv = false,
        .bclk_inv = false,
        .ws_inv = false,
      },
    },
  };
  if (i2s_channel_init_std_mode(micChan, &std_cfg) != ESP_OK || i2s_channel_enable(micChan) != ESP_OK) {
    i2s_del_channel(micChan);
    micChan = nullptr;
    return false;
  }
  return true;
}

// reads the microphone on core 0 and puts int16 samples in a thread-safe stream buffer
void micTask(void* arg) {
  static int32_t blk[256 * 2];
  static int16_t pcm[256];
  for (;;) {
    size_t bytes = 0;
    esp_err_t e = i2s_channel_read(micChan, blk, sizeof(blk), &bytes, 1000);
    if (e != ESP_OK || bytes == 0) { vTaskDelay(1); continue; }
    size_t n = bytes / 8;
    for (size_t i = 0; i < n; i++) {
      int32_t v = blk[2 * i + (MIC_LEFT ? 0 : 1)] >> AUDIO_SHIFT;
      if (v > 32767) v = 32767;
      if (v < -32768) v = -32768;
      pcm[i] = (int16_t)v;
    }
    xStreamBufferSend(audioSb, pcm, n * 2, 0);        // drops data if the PC side stalls
  }
}

// ---------------- setup / loop ----------------
void setup() {
  Serial.setTxBufferSize(8192);                   // lets Serial.write return at once (no sample loss)
  Serial.begin(SERIAL_BAUD);
  unsigned long t0 = millis();
  while (!Serial && millis() - t0 < 3000) { delay(10); }
  delay(500);
  Serial.println("\nCOGOALTIS-FW v2 boot");
  Serial.println(TEST_SIGNAL ? "mode: TEST SIGNAL (no electrodes needed)" : "mode: ELECTRODES");

  digitalWrite(PIN_CS, HIGH);    pinMode(PIN_CS, OUTPUT);
  digitalWrite(PIN_START, LOW);  pinMode(PIN_START, OUTPUT);
  digitalWrite(PIN_PWDN, HIGH);  pinMode(PIN_PWDN, OUTPUT);
  pinMode(PIN_DRDY, INPUT_PULLUP);
  SPI.begin(PIN_SCK, PIN_MISO, PIN_MOSI, -1);     // CS is controlled by hand

  while (!initAds()) {
    Serial.println("ADS init failed, retrying in 2 s (check wiring / solder)");
    delay(2000);
  }

  audioSb = xStreamBufferCreate(16384, 1);
  if (audioSb && startMic()) {
    audioOn = true;
    xTaskCreatePinnedToCore(micTask, "mic", 4096, NULL, 2, NULL, 0);
    Serial.printf("MIC OK: BCLK=%d WS=%d DIN=%d slot=%s\n", MIC_BCLK, MIC_WS, MIC_DIN, MIC_LEFT ? "LEFT" : "RIGHT");
  } else {
    Serial.println("MIC NOT AVAILABLE: continuing with EMG only (audio will be empty)");
  }

  attachInterrupt(digitalPinToInterrupt(PIN_DRDY), onDrdy, FALLING);
  adsCommand(CMD_RDATAC);
  delay(10);
  digitalWrite(PIN_START, HIGH);
  Serial.println("STREAMING binary packets from now on (do not use Serial Monitor)");
  Serial.flush();
  handled = drdyCount;
}

void loop() {
  uint32_t seen = drdyCount;
  if (seen == handled) return;
  if (seen - handled > 1) lostSamples += seen - handled - 1;
  handled = seen;

  uint8_t tx[9] = {0}, rx[9];
  SPI.beginTransaction(adsSpi);
  digitalWrite(PIN_CS, LOW);
  SPI.transferBytes(tx, rx, 9);
  digitalWrite(PIN_CS, HIGH);
  SPI.endTransaction();

  if (frameCount == 0) packetStartUs = (uint64_t)esp_timer_get_time();
  parseAdsFrame(rx, &frames[frameCount * PKT_CH], &frames[frameCount * PKT_CH + 1]);
  frameCount++;

  if (frameCount == PKT_FRAMES) {
    uint16_t nAud = 0;
    if (audioOn) {
      const size_t need = PKT_AUDIO * 2;
      while (xStreamBufferBytesAvailable(audioSb) > 3 * need) {      // audio is ahead of the ADS clock: drop the oldest
        xStreamBufferReceive(audioSb, audioTmp, need, 0);
        audioDropped++;
      }
      size_t got = xStreamBufferReceive(audioSb, audioPkt, need, 0);
      if (got < need) {                                              // audio is behind: pad with zeros
        memset(((uint8_t*)audioPkt) + got, 0, need - got);
        audioUnderrun++;
      }
      nAud = PKT_AUDIO;
    }
    size_t n = buildPacket(packetBuf, seqNo++, packetStartUs, frames, frameCount, audioPkt, nAud);
    Serial.write(packetBuf, n);
    frameCount = 0;
  }
}
