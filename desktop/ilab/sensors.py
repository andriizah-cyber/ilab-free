"""
Канали реєстратора, перерахунок сирих значень у фізичні величини, калібрування.
"""
import json
import os
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from .protocol import Sample

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
CONFIG_PATH = os.path.join(os.path.expanduser("~"), ".ilab_app.json")


def load_table(path: str):
    """Файл виду 'АЦП,значення' (як 64.dat з комплекту Register iLab)."""
    pts = []
    try:
        with open(path, encoding="latin1") as f:
            for line in f:
                p = line.strip().split(",")
                if len(p) == 2:
                    try:
                        pts.append((float(p[0]), float(p[1])))
                    except ValueError:
                        pass
    except OSError:
        pass
    return sorted(pts)


def interp(table, x):
    if not table or x is None:
        return None
    if x <= table[0][0] or x >= table[-1][0]:
        return None
    lo, hi = 0, len(table) - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if table[mid][0] <= x:
            lo = mid
        else:
            hi = mid
    (x1, y1), (x2, y2) = table[lo], table[hi]
    return y1 + (x - x1) * (y2 - y1) / (x2 - x1)


NTC_TABLE = load_table(os.path.join(DATA_DIR, "64.dat"))
TABLES = {k: load_table(os.path.join(DATA_DIR, f"{k}.dat")) for k in (70, 72, 73)}


def _tab(k):
    return lambda a, b: interp(TABLES[k], a) if a else None


# ---------- типи датчиків для зовнішніх портів ----------
@dataclass
class PortSensorType:
    key: str
    name: str
    unit: str
    fmt: str
    convert: Callable[[int, int], Optional[float]]   # (a, b) -> значення


def _ntc(a, b):
    return interp(NTC_TABLE, a) if a else None


def _volt(a, b):
    return a * 3.3 / 4095 if a else None


PORT_TYPES: List[PortSensorType] = [
    PortSensorType("temp", "Температура (стандартний датчик)", "°C", "{:.1f}", _ntc),
    PortSensorType("temp70", "Температура (датчик ID 70)", "°C", "{:.1f}", _tab(70)),
    PortSensorType("temp72", "Висока температура (ID 72)", "°C", "{:.0f}", _tab(72)),
    PortSensorType("temp73", "Температура поверхні (ID 73)", "°C", "{:.1f}", _tab(73)),
    PortSensorType("hum", "Вологість (потрібне калібрування)", "%", "{:.1f}",
                   lambda a, b: (b * 100.0 / 4095) if a else None),
    PortSensorType("volt", "Напруга АЦП, 0–3.3 В", "В", "{:.3f}", _volt),
    PortSensorType("raw", "Сирий АЦП (основний сигнал)", "", "{:.0f}",
                   lambda a, b: float(a) if a else None),
    PortSensorType("raw2", "Сирий АЦП (другий сигнал)", "", "{:.0f}",
                   lambda a, b: float(b) if a else None),
]
PORT_TYPE_BY_KEY = {t.key: t for t in PORT_TYPES}

# ID датчика (пакет B5, як у SensorInfo.ini старої програми) -> (тип, назва українською)
SENSOR_IDS = {
    64: ("temp", "Температура"),
    70: ("temp70", "Температура"),
    72: ("temp72", "Висока температура"),
    73: ("temp73", "Температура поверхні"),
    62: ("hum", "Вологість"),
    112: ("hum", "Вологість"),
    99: ("hum", "Температура і вологість"),
}

# порт -> індекси (a, b) у масиві adc
PORT_ADC = {1: (0, 1), 2: (2, 3), 3: (4, 5), 4: (6, 7)}


# ---------- канали ----------
@dataclass
class Channel:
    key: str
    name: str
    unit: str
    fmt: str
    getter: Callable[[Sample], Optional[float]]
    color: str
    cal_key: str = ""

    @property
    def ckey(self):
        return self.cal_key or self.key


def _bi(idx, scale=1.0, lo=None, hi=None):
    def g(s: Sample):
        if s.builtin is None or s.builtin[idx] is None:
            return None
        v = s.builtin[idx] * scale
        if (lo is not None and v < lo) or (hi is not None and v > hi):
            return None                      # неможливе значення — відкидаємо
        return v
    return g


BUILTIN_CHANNELS = [
    Channel("light", "Освітленість", "лк", "{:.0f}", _bi(0), "#d9a400"),  # ID 122, Lux
    Channel("uv", "Ультрафіолет", "у.о.", "{:.0f}", _bi(3), "#7b3fbf"),
    Channel("press", "Тиск повітря", "кПа", "{:.1f}", _bi(4, 0.1, 30, 120), "#2a7ab0"),
    Channel("t_in", "Температура (вбудований)", "°C", "{:.1f}", _bi(1, 0.1, -40, 85), "#d0402b"),
    Channel("rh_in", "Вологість (вбудований)", "%", "{:.1f}", _bi(2, 0.1, 0, 100), "#1f9e89"),
    Channel("hr", "Пульс (роз'єм 3.5 мм)", "уд/хв", "{:.0f}", _bi(5), "#c2185b"),
]


def port_channel(port: int, type_key: str) -> Channel:
    t = PORT_TYPE_BY_KEY.get(type_key, PORT_TYPES[0])
    ia, ib = PORT_ADC[port]

    def g(s: Sample):
        return t.convert(s.adc[ia], s.adc[ib])
    colors = {1: "#555555", 2: "#8d6e63", 3: "#e65100", 4: "#2e7d32"}
    return Channel(f"port{port}", f"Порт {port}: {t.name}", t.unit, t.fmt, g, colors[port],
                   cal_key=f"port{port}:{t.key}")


# ---------- калібрування (лінійне, за 2 точками) ----------
class Calibration:
    def __init__(self):
        self.k: Dict[str, float] = {}
        self.b: Dict[str, float] = {}

    def apply(self, key, v):
        if v is None:
            return None
        return v * self.k.get(key, 1.0) + self.b.get(key, 0.0)

    def set_two_point(self, key, m1, r1, m2, r2):
        """m — виміряне (до калібрування), r — еталонне значення."""
        if m2 == m1:
            raise ValueError("Виміряні значення в двох точках однакові")
        k = (r2 - r1) / (m2 - m1)
        self.k[key], self.b[key] = k, r1 - k * m1

    def set_offset(self, key, measured, reference):
        self.k[key] = 1.0
        self.b[key] = reference - measured

    def reset(self, key):
        self.k.pop(key, None)
        self.b.pop(key, None)

    def describe(self, key):
        if key not in self.k:
            return "немає"
        return f"y = {self.k[key]:.4g}·x {'+' if self.b[key] >= 0 else '−'} {abs(self.b[key]):.4g}"


def load_config():
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_config(cfg):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except OSError:
        pass
