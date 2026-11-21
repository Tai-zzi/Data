#pragma once
// Packet format shared with the PC side (packet.py). Pure C++ (no Arduino calls) so it can be tested on a PC.
//
// [0xA5 0x5A][ver u8][n_ch u8][seq u32][t_us u64][n_emg u16][n_aud u16][EMG int32 x (n_emg*n_ch)][audio int16 x n_aud][CRC16 u16]
// little-endian; CRC16 = CRC-CCITT (poly 0x1021, init 0xFFFF) over ver .. end of payload (same as binascii.crc_hqx)
#include <stddef.h>
#include <stdint.h>

static const uint8_t PKT_VERSION = 1;
static const uint8_t PKT_CH = 2;           // ADS1292R: CH1 (-> column 0), CH2 (-> column 1)
static const uint16_t PKT_FRAMES = 50;     // 50 frames = 50 ms at 1 kSPS
static const uint16_t PKT_AUDIO = 800;     // 800 audio samples = 50 ms at 16 kHz
static const size_t PKT_MAX_BYTES = 2 + 18 + PKT_FRAMES * PKT_CH * 4 + PKT_AUDIO * 2 + 2;

inline uint16_t crc16_ccitt(const uint8_t* d, size_t n, uint16_t crc = 0xFFFF) {
  for (size_t i = 0; i < n; i++) {
    crc ^= (uint16_t)d[i] << 8;
    for (int b = 0; b < 8; b++) crc = (crc & 0x8000) ? (uint16_t)((crc << 1) ^ 0x1021) : (uint16_t)(crc << 1);
  }
  return crc;
}

inline size_t putU16(uint8_t* o, uint16_t v) { o[0] = v & 0xFF; o[1] = v >> 8; return 2; }
inline size_t putU32(uint8_t* o, uint32_t v) { for (int i = 0; i < 4; i++) o[i] = (v >> (8 * i)) & 0xFF; return 4; }
inline size_t putU64(uint8_t* o, uint64_t v) { for (int i = 0; i < 8; i++) o[i] = (v >> (8 * i)) & 0xFF; return 8; }

// frames: n_frames * 2 interleaved int32 (CH1, CH2). audio: n_aud int16 (may be n_aud = 0). Returns bytes written.
inline size_t buildPacket(uint8_t* out, uint32_t seq, uint64_t t_us, const int32_t* frames, uint16_t n_frames,
                          const int16_t* audio, uint16_t n_aud) {
  size_t p = 0;
  out[p++] = 0xA5;
  out[p++] = 0x5A;
  const size_t body = p;
  out[p++] = PKT_VERSION;
  out[p++] = PKT_CH;
  p += putU32(out + p, seq);
  p += putU64(out + p, t_us);
  p += putU16(out + p, n_frames);
  p += putU16(out + p, n_aud);
  for (size_t i = 0; i < (size_t)n_frames * PKT_CH; i++) p += putU32(out + p, (uint32_t)frames[i]);
  for (size_t i = 0; i < n_aud; i++) p += putU16(out + p, (uint16_t)audio[i]);
  p += putU16(out + p, crc16_ccitt(out + body, p - body));
  return p;
}

// ADS1292R RDATAC frame = 9 bytes: 3 status + 3 CH1 + 3 CH2 (24-bit two's complement, MSB first)
inline int32_t signExtend24(uint32_t v) { return (int32_t)((v & 0x800000u) ? (v | 0xFF000000u) : v); }
inline void parseAdsFrame(const uint8_t* rx, int32_t* ch1, int32_t* ch2) {
  *ch1 = signExtend24(((uint32_t)rx[3] << 16) | ((uint32_t)rx[4] << 8) | rx[5]);
  *ch2 = signExtend24(((uint32_t)rx[6] << 16) | ((uint32_t)rx[7] << 8) | rx[8]);
}
