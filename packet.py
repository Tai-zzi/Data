"""ESP32 -> PC 바이너리 패킷 형식 (펌웨어와 PC 코드가 공유하는 약속).

[0xA5 0x5A] [ver u8][n_ch u8][seq u32][t_us u64][n_emg u16][n_aud u16]
[EMG int32 x (n_emg*n_ch), 채널 인터리브][오디오 int16 x n_aud][CRC16 u16]
- 모두 little-endian (ESP32 기본). CRC16은 ver ~ payload 끝까지 (CRC-CCITT, init 0xFFFF)
- t_us: 패킷 첫 샘플 시각 (esp_timer_get_time) -> EMG/오디오 동기화의 기준
- ADS1292R은 24비트 -> 부호 확장해서 int32로 보냄
"""
import binascii
import struct
from dataclasses import dataclass

import numpy as np

HEADER = b"\xA5\x5A"
VERSION = 1
_FIXED = struct.Struct("<BBIQHH")
_MAX_EMG_VALUES = 4096
_MAX_AUDIO = 8192


@dataclass
class Packet:
    seq: int
    t_us: int
    emg: np.ndarray    # (n_emg, n_ch) int32
    audio: np.ndarray  # (n_aud,) int16


def pack(seq, t_us, emg, audio):
    emg = np.asarray(emg, dtype="<i4")
    if emg.ndim == 1:
        emg = emg[:, None]
    audio = np.asarray(audio, dtype="<i2")
    n_emg, n_ch = emg.shape
    body = _FIXED.pack(VERSION, n_ch, seq & 0xFFFFFFFF, t_us, n_emg, len(audio))
    body += emg.tobytes() + audio.tobytes()
    crc = binascii.crc_hqx(body, 0xFFFF)
    return HEADER + body + struct.pack("<H", crc)


class PacketParser:
    """시리얼에서 들어오는 임의 크기의 바이트 조각을 넣으면 완성된 패킷을 꺼내준다.
    헤더를 놓치거나 깨진 패킷이 있어도 다음 헤더에서 자동 복구한다."""

    def __init__(self):
        self.buf = bytearray()
        self.bad_crc = 0
        self.lost = 0          # seq 건너뜀으로 추정한 유실 패킷 수
        self._last_seq = None

    def feed(self, data):
        self.buf += data
        out = []
        head = 2 + _FIXED.size
        while True:
            i = self.buf.find(HEADER)
            if i < 0:
                del self.buf[:-1]          # 마지막 1바이트는 헤더 앞부분일 수 있어 보존
                break
            if i > 0:
                del self.buf[:i]
            if len(self.buf) < head:
                break
            ver, n_ch, seq, t_us, n_emg, n_aud = _FIXED.unpack_from(self.buf, 2)
            if (ver != VERSION or n_ch == 0 or n_emg * n_ch > _MAX_EMG_VALUES
                    or n_aud > _MAX_AUDIO):
                del self.buf[:1]
                continue
            total = head + n_emg * n_ch * 4 + n_aud * 2 + 2
            if len(self.buf) < total:
                break
            body = bytes(self.buf[2:total - 2])
            (crc,) = struct.unpack_from("<H", self.buf, total - 2)
            if binascii.crc_hqx(body, 0xFFFF) != crc:
                self.bad_crc += 1
                del self.buf[:1]
                continue
            p = _FIXED.size
            emg = np.frombuffer(body, "<i4", n_emg * n_ch, p).reshape(n_emg, n_ch)
            audio = np.frombuffer(body, "<i2", n_aud, p + n_emg * n_ch * 4)
            if self._last_seq is not None and seq != self._last_seq + 1:
                self.lost += (seq - self._last_seq - 1) & 0xFFFFFFFF
            self._last_seq = seq
            out.append(Packet(seq, t_us, emg.copy(), audio.copy()))
            del self.buf[:total]
        return out


if __name__ == "__main__":
    # 자가 테스트: 쓰레기 바이트 삽입 + 패킷 1개 손상 + 1개 누락 + 랜덤 조각 입력
    rng = np.random.default_rng(0)
    stream, sent = bytearray(), []
    for seq in range(200):
        emg = rng.integers(-2**23, 2**23, (20, 2))
        aud = rng.integers(-2**15, 2**15, 320)
        pkt = pack(seq, seq * 20000, emg, aud)
        if seq == 50:
            continue                      # 유실 시뮬레이션
        if seq == 100:
            pkt = pkt[:30] + b"\x00" + pkt[31:]   # 손상 시뮬레이션
        else:
            sent.append((seq, emg, aud))
        if seq % 37 == 0:
            stream += bytes(rng.integers(0, 256, 13, dtype=np.uint8))  # 쓰레기
        stream += pkt
    parser, got, pos = PacketParser(), [], 0
    while pos < len(stream):
        n = int(rng.integers(1, 200))
        got += parser.feed(bytes(stream[pos:pos + n]))
        pos += n
    ok = len(got) == len(sent) and all(
        g.seq == s and np.array_equal(g.emg, e) and np.array_equal(g.audio, a)
        for g, (s, e, a) in zip(got, sent))
    print(f"수신 {len(got)}/{len(sent)}  CRC오류 {parser.bad_crc}  유실 {parser.lost}  "
          f"-> {'PASS' if ok else 'FAIL'}")
