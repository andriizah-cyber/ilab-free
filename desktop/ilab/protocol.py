"""
Протокол реєстратора Register iLab (STM32L152 + CH372B, USB bulk 64 байти).

Пакет (в обидва боки):  [тип][N][дані ...][контрольна сума]
  N — індекс байта контрольної суми (довжина пакета = N + 1),
  контрольна сума = сума байтів 0..N-1 & 0xFF.
Плата не очищає свій 64-байтний буфер, тож усе після байта N — сміття.

Визначено реверс-інжинірингом USBSensor_DLL.dll і тестами на реальній платі.
"""
from dataclasses import dataclass, field
from typing import List, Optional

# ПК -> плата
CMD_CONNECT = 0xA0
CMD_START = 0xA2
CMD_LAST_DATA = 0xA3
CMD_STOP = 0xA4
CMD_SENSOR_INFO = 0xA5
CMD_SET_FREQ = 0xA8

# плата -> ПК
PKT_ACK = 0xA0          # A0 07 <cmd> ...  підтвердження команди
PKT_CONNECTED = 0xB0
PKT_DATA = 0xB2
PKT_LAST_DATA = 0xB3
PKT_SENSOR_INFO = 0xB5

N_ADC = 8               # 4 порти x 2 значення: [п1a, п1b, п2a, п2b, п3a, п3b, п4a, п4b]
N_BUILTIN = 6           # вбудовані датчики


def build(cmd: int, payload: bytes = b"") -> bytes:
    body = bytes([cmd, 2 + len(payload)]) + bytes(payload)
    return body + bytes([sum(body) & 0xFF])


def extract(buf: bytes) -> Optional[bytes]:
    """Повертає валідний пакет з початку 64-байтного блоку або None."""
    if len(buf) < 3:
        return None
    t, n = buf[0], buf[1]
    if t == 0 or n < 2 or n >= len(buf):
        return None
    p = bytes(buf[:n + 1])
    if (sum(p[:n]) & 0xFF) != p[n]:
        return None
    return p


# відомі типи пакетів і їхні N — захист від хибних збігів у потоці Bluetooth
VALID = {0xB0: (2,), 0xB2: (0x1B, 0x23, 0x3F), 0xB3: (0x3A,), 0xB5: (0x3F,), 0xA0: (7,),
         0xFD: (3,)}


def scan_stream(buf: bytes):
    """Потік байтів (Bluetooth) -> (список пакетів, скільки байтів використано)."""
    out, i = [], 0
    while i + 2 < len(buf):
        t, n = buf[i], buf[i + 1]
        if n in VALID.get(t, ()):
            if i + n >= len(buf):
                break                      # пакет ще не прийшов повністю
            p = bytes(buf[i:i + n + 1])
            if (sum(p[:n]) & 0xFF) == p[n]:
                out.append(p)
                i += n + 1
                continue
        i += 1
    return out, i


def _u16(b, i):
    return b[i] | (b[i + 1] << 8)


def _u32(b, i):
    return int.from_bytes(b[i:i + 4], "little")


@dataclass
class Sample:
    adc: List[int]                     # ch0..ch7 (сирі 12-біт)
    builtin: Optional[List[Optional[int]]] = None  # x0..x5 (окремі можуть бути None)
    counter: Optional[int] = None
    kind: int = PKT_DATA


@dataclass
class PortInfo:
    port: int
    sensor_id: int
    name: str = ""
    unit: str = ""
    raw: bytes = field(default=b"", repr=False)


def parse_sample(p: bytes) -> Optional[Sample]:
    """
    Розширений B2 (N=0x3F): порти 1..4 по 2×u16 з байта 3, далі 16 байт резерву,
      вбудовані 6×u32 з байта 35, лічильник u32 @59.
    B3 (N=0x3A): те саме, але зсунуте на 1 байт (порти з байта 2, вбудовані з 34).
    Короткий B2 (N=0x1B, USB): лише порти 2..4 з байта 3 (порту 1 немає), лічильник @23.
    Короткий B2 (N=0x23, Bluetooth): порти 1..4 з байта 3, світло @19, RH @23, тиск @27,
      лічильник @31 (температури всередині та УФ немає).
    """
    t, n = p[0], p[1]
    if t == PKT_DATA and n == 0x23:
        return Sample([_u16(p, 3 + 2 * k) for k in range(N_ADC)],
                      [_u32(p, 19), None, _u32(p, 23), None, _u32(p, 27), None],
                      _u32(p, 31), t)
    if t == PKT_DATA and n == 0x3F:
        return Sample([_u16(p, 3 + 2 * k) for k in range(N_ADC)],
                      [_u32(p, 35 + 4 * k) for k in range(N_BUILTIN)], _u32(p, 59), t)
    if t == PKT_LAST_DATA and n == 0x3A:
        return Sample([_u16(p, 2 + 2 * k) for k in range(N_ADC)],
                      [_u32(p, 34 + 4 * k) for k in range(N_BUILTIN)], None, t)
    if t == PKT_DATA and n == 0x1B:
        return Sample([0, 0] + [_u16(p, 3 + 2 * k) for k in range(N_ADC - 2)], None,
                      _u32(p, 23), t)
    return None


def _text(raw: bytes) -> str:
    raw = raw.split(b"\x00")[0].replace(b"\xff", b"")
    for enc in ("gbk", "cp1251", "latin1"):
        try:
            return raw.decode(enc).strip()
        except UnicodeDecodeError:
            pass
    return ""


def parse_port_info(p: bytes) -> Optional[PortInfo]:
    if p[0] != PKT_SENSOR_INFO or len(p) < 4:
        return None
    name = unit = ""
    if p[1] == 0x3F and len(p) >= 0x3E:
        name, unit = _text(p[0x17:0x33]), _text(p[0x33:0x3D])
    return PortInfo(port=p[2], sensor_id=p[3], name=name, unit=unit, raw=p)
