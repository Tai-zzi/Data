"""ESP32 시리얼 입력 소스. 대시보드에서 MockNight 대신 끼워 쓰는 '실제 장치' 입력.

 바이트 -> PacketParser -> (채널 정리, 단위 변환, 유실 보정) -> 원본 저장(logger) + 처리용 버퍼
 처리용 버퍼는 홉(0.05초) 단위로 잘라 StreamProcessor 에 넘긴다.

 - 패킷이 빠지면 순번(seq) 차이로 알아내 EMG는 직전 값 유지, 오디오는 0으로 채워 시간축을 유지한다.
 - 패킷에 오디오가 없으면(n_aud=0) 오디오를 0으로 채운다 -> '턱 EMG만' 간이 시험 가능.
 - 목록 확인:  python serial_source.py --list"""
import sys

import numpy as np

import config as C
from packet import PacketParser
from stream import AUD_HOP, EMG_HOP

AUD_PER_EMG = C.AUDIO_FS // C.EMG_FS


def uv_per_count(gain):
    """ADS1292R 24비트 코드 1단계가 몇 µV인지: 2*Vref/gain / 2^24"""
    return 2 * C.ADS_VREF / gain / 2 ** 24 * 1e6


class SerialLink:
    finished = False

    def __init__(self, port, baud=C.SERIAL_BAUD):
        try:
            import serial
        except ImportError:
            raise SystemExit("pyserial이 필요합니다:  pip install pyserial")
        try:
            self.ser = serial.Serial(port, baud, timeout=0)
            try:
                self.ser.set_buffer_size(rx_size=1 << 18)       # 윈도우 기본 수신 버퍼(4KB)는 데이터가 초당 약 40KB라 금방 찬다
            except (AttributeError, ValueError, OSError):
                pass
        except serial.SerialException as e:
            raise SystemExit(f"포트를 열 수 없습니다 ({port}): {e}\n"
                             "다른 프로그램(시리얼 모니터 등)이 포트를 쓰고 있지 않은지 확인하세요.")

    def read_bytes(self):
        n = self.ser.in_waiting
        return self.ser.read(n) if n else b""

    def on_stimulus(self, t, level):
        pass        # TODO(할 일 4): 자극 명령을 ESP32로 보내는 곳. 안전 검토 전까지 비워 둠

    def close(self):
        self.ser.close()


def list_ports():
    try:
        from serial.tools import list_ports as lp
    except ImportError:
        raise SystemExit("pyserial이 필요합니다:  pip install pyserial")
    ports = list(lp.comports())
    if not ports:
        print("연결된 시리얼 포트가 없습니다. USB 연결과 드라이버를 확인하세요.")
    for p in ports:
        print(f"{p.device}\t{p.description}")


class SerialSource:
    live = True

    def __init__(self, link, logger=None):
        self.link, self.logger = link, logger
        self.parser = PacketParser()
        self._emg = np.zeros((0, 2), np.float32)    # 처리 대기: 열0=EEG, 열1=턱EMG (µV)
        self._aud = np.zeros(0, np.float32)         # 처리 대기: -1~1
        self._scale = np.array([uv_per_count(C.ADS_GAIN_EEG), uv_per_count(C.ADS_GAIN_EMG)])
        self._last_seq, self._last_row = None, np.zeros(2, np.float32)
        self.packets = self.lost = self.restarts = 0
        self._frames = self._last_n = 0
        self._t_first = self._t_last = None
        self._warned = set()

    def _warn(self, key, msg):
        if key not in self._warned:
            self._warned.add(key)
            print("[경고]", msg)
            if self.logger:
                self.logger.event("경고: " + msg)

    def _ingest(self, pkt):
        n = len(pkt.emg)
        if pkt.emg.shape[1] <= max(C.CH_EEG, C.CH_EMG):
            self._warn("ch", f"패킷 채널 수({pkt.emg.shape[1]})가 CH_EEG/CH_EMG 설정과 안 맞아 무시합니다.")
            return
        emg = (np.column_stack((pkt.emg[:, C.CH_EEG], pkt.emg[:, C.CH_EMG])) * self._scale).astype(np.float32)
        aud = pkt.audio
        if len(aud) == 0:
            aud = np.zeros(n * AUD_PER_EMG, np.int16)
            self._warn("noaudio", "패킷에 오디오가 없어 0으로 채웁니다 (코골이 감지 불가, 턱 EMG만 동작).")
        elif len(aud) != n * AUD_PER_EMG:
            self._warn("ratio", f"EMG {n}개당 오디오 {len(aud)}개 - EMG_FS/AUDIO_FS 설정과 펌웨어를 확인하세요.")

        missed = 0
        if self._last_seq is not None:
            missed = (pkt.seq - self._last_seq - 1) & 0xFFFFFFFF
            if 0 < missed <= 2000:                       # 유실 보정: 시간축 유지
                fe = np.repeat(self._last_row[None, :], missed * n, axis=0)
                fa = np.zeros(missed * len(aud), np.int16)
                self._push(fe, fa)
                self.lost += missed
                if self.logger:
                    self.logger.event(f"패킷 {missed}개 유실 (seq {self._last_seq + 1}~{pkt.seq - 1}) -> 보정")
            elif missed > 2000:
                self.restarts += 1
                missed = 0
                if self.logger:
                    self.logger.event(f"순번 불연속(seq {self._last_seq}->{pkt.seq}), 장치 재시작으로 간주")
        self._last_seq = pkt.seq
        self._push(emg, aud)
        self._last_row = emg[-1].copy()

        self.packets += 1
        self._frames += n * (1 + missed if 0 < missed <= 2000 else 1)
        self._last_n = n
        if self._t_first is None:
            self._t_first = pkt.t_us
        self._t_last = pkt.t_us

    def _push(self, emg, aud_i16):
        if self.logger:
            self.logger.write(emg, aud_i16)
        self._emg = np.concatenate((self._emg, emg))
        self._aud = np.concatenate((self._aud, aud_i16.astype(np.float32) / 32768.0))

    def read_available(self, max_hops=400):
        """도착한 데이터를 처리하고, 홉 단위로 잘라 (emg2, audio) 반환. 없으면 None."""
        data = self.link.read_bytes()
        if data:
            for pkt in self.parser.feed(data):
                self._ingest(pkt)
        if abs(len(self._emg) / C.EMG_FS - len(self._aud) / C.AUDIO_FS) > 5:
            self._warn("sync", "EMG와 오디오 길이가 5초 이상 어긋납니다. EMG_FS/AUDIO_FS가 펌웨어와 같은지 확인하세요.")
        K = min(len(self._emg) // EMG_HOP, len(self._aud) // AUD_HOP, max_hops)
        if K <= 0:
            return None
        e, self._emg = self._emg[:K * EMG_HOP], self._emg[K * EMG_HOP:]
        a, self._aud = self._aud[:K * AUD_HOP], self._aud[K * AUD_HOP:]
        return e, a

    @property
    def finished(self):
        return self.link.finished and (len(self._emg) < EMG_HOP or len(self._aud) < AUD_HOP)

    def apply_stimulus(self, t, level):
        getattr(self.link, "on_stimulus", lambda *a: None)(t, level)

    def stats(self):
        s = dict(packets=self.packets, lost_packets=self.lost, bad_crc=self.parser.bad_crc,
                 device_restarts=self.restarts)
        if self._t_first is not None and self._t_last > self._t_first:
            s["measured_emg_fs"] = round((self._frames - self._last_n) / ((self._t_last - self._t_first) / 1e6), 2)
        return s

    def status(self):
        s = self.stats()
        txt = f"수신 {s['packets']}패킷 | 유실 {s['lost_packets']} | CRC오류 {s['bad_crc']}"
        if self.logger:
            txt += f" | 저장: {self.logger.dir}"
        return txt

    def close(self):
        self.link.close() if hasattr(self.link, "close") else None
        s = self.stats()
        print("[수신 통계]", s)
        if "measured_emg_fs" in s and abs(s["measured_emg_fs"] - C.EMG_FS) > 0.02 * C.EMG_FS:
            print(f"[경고] 실제 EMG 샘플링 {s['measured_emg_fs']} Hz가 설정 EMG_FS={C.EMG_FS}와 다릅니다. "
                  "config.py 또는 ADS1292R 설정을 맞추세요.")
        if self.logger:
            d = self.logger.close(extra=s)
            print("[저장 완료]", d)


if __name__ == "__main__":
    if "--list" in sys.argv:
        list_ports()
    else:
        print("사용법: python serial_source.py --list   (연결된 시리얼 포트 목록)")
