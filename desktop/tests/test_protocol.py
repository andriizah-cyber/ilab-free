"""Тести на реальних пакетах, записаних з плати (17.11.2026)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ilab import protocol as P  # noqa: E402
from ilab.sensors import BUILTIN_CHANNELS, Calibration, port_channel  # noqa: E402


def h(s):
    b = bytes.fromhex(s.replace(" ", ""))
    return b + bytes(64 - len(b))


SHORT = h("B2 1B 01 00 00 00 00 21 09 FF 0F 00 00 00 00 00 00 00 00 00 00 00 00 01 00 00 00 07 FF FF")
EXT = h("B2 3F 01 00 00 00 00 00 00 00 00 52 09 FF 0F 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 "
        "00 00 00 00 00 19 00 00 00 F1 00 00 00 44 01 00 00 7D 00 00 00 B2 03 00 00 45 00 00 00 05 00 00 00 26")
B3 = h("B3 3A 00 00 00 00 00 00 00 00 47 09 FF 0F 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 "
       "00 00 1B 00 00 00 EC 00 00 00 4C 01 00 00 7D 00 00 00 B2 03 00 00 3C 00 00 00 0D")
HR = h("B5 3F 0E 4E 00 00 00 00 00 00 00 00 00 80 00 00 80 00 00 80 C8 00 80 D0 C4 C2 CA FF FF FF FF FF FF FF FF FF FF FF FF FF FF FF FF FF FF FF FF FF FF FF FF FF FF 42 50 4D FF FF FF FF FF FF FF F6")


def test_commands():
    assert P.build(P.CMD_CONNECT) == bytes.fromhex("A002A2")
    assert P.build(P.CMD_SENSOR_INFO) == bytes.fromhex("A502A7")
    assert P.build(P.CMD_START) == bytes.fromhex("A202A4")
    assert P.build(P.CMD_STOP) == bytes.fromhex("A402A6")
    assert P.build(P.CMD_SET_FREQ, b"\x03") == bytes.fromhex("A80303AE")


def test_short():
    s = P.parse_sample(P.extract(SHORT))
    assert s.adc[4:6] == [2337, 4095] and s.adc[:2] == [0, 0]   # порт 3
    assert s.builtin is None and s.counter == 1


def test_extended():
    s = P.parse_sample(P.extract(EXT))
    assert s.adc[4:6] == [2386, 4095]                            # порт 3
    assert s.builtin == [25, 241, 324, 125, 946, 69] and s.counter == 5


def test_b3():
    s = P.parse_sample(P.extract(B3))
    assert s.adc[4] == 2375 and s.builtin[4] == 946


PORT1_EXT = h("B2 3F 01 03 09 FF 0F 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 "
              "00 00 00 00 00 00 0D 00 00 00 0B 01 00 00 62 01 00 00 7D 00 00 00 A9 03 00 00 00 00 00 00 "
              "0B 00 00 00 BC")
PORT2_EXT = h("B2 3F 01 00 00 00 00 0A 09 FF 0F 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 "
              "00 00 00 00 00 00 0E 00 00 00 09 01 00 00 66 01 00 00 7D 00 00 00 A9 03 00 00 54 00 00 00 "
              "0B 00 00 00 1A")
PORT1_B3 = h("B3 3A FE 08 FF 0F 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 "
             "00 00 00 00 0B 00 00 00 09 01 00 00 EB 01 00 00 7C 00 00 00 A9 03 00 00 00 00 00 00 2A")
PORT2_SHORT = h("B2 1B 01 F0 08 FF 0F 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 01 00 00 00 D5")


def test_ports_1_2():
    s = P.parse_sample(P.extract(PORT1_EXT))
    assert s.adc == [0x0903, 4095, 0, 0, 0, 0, 0, 0]
    s = P.parse_sample(P.extract(PORT2_EXT))
    assert s.adc == [0, 0, 0x090A, 4095, 0, 0, 0, 0]
    s = P.parse_sample(P.extract(PORT1_B3))
    assert s.adc[:2] == [0x08FE, 4095] and s.builtin[4] == 0x3A9
    s = P.parse_sample(P.extract(PORT2_SHORT))
    assert s.adc[2:4] == [0x08F0, 4095]
    assert 28 < port_channel(1, "temp").getter(P.parse_sample(P.extract(PORT1_EXT))) < 34


BT_SHORT = bytes.fromhex("B2 23 01 19 09 C0 05 00 00 00 00 00 00 00 00 00 00 00 00 17 00 00 00 66 01 "
                         "00 00 A8 03 00 00 01 00 00 00 E7".replace(" ", ""))
BT_PORT1 = bytes.fromhex("B53F014001010001000300140080280080F40100DC0580CEC2B6C8FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFA1E343FFFFFFFFFFFFFF81")


def test_bluetooth():
    s = P.parse_sample(P.extract(BT_SHORT))
    assert s.adc[:2] == [0x0919, 0x05C0] and s.counter == 1
    assert s.builtin == [0x17, None, 0x166, None, 0x3A8, None]
    i = P.parse_port_info(P.extract(BT_PORT1))
    assert (i.port, i.sensor_id, i.name, i.unit) == (1, 64, "温度", "°C")
    ch = {c.key: c for c in BUILTIN_CHANNELS}
    assert ch["t_in"].getter(s) is None and abs(ch["press"].getter(s) - 93.6) < 1e-9
    # потік: шум + два пакети + обрізаний хвіст
    stream = b"\x00\x55" + bytes.fromhex("B002B2") + BT_SHORT + BT_SHORT[:10]
    pk, used = P.scan_stream(stream)
    assert pk == [bytes.fromhex("B002B2"), BT_SHORT] and stream[used:] == BT_SHORT[:10]


def test_bad_checksum():
    bad = bytearray(SHORT)
    bad[7] ^= 1
    assert P.extract(bytes(bad)) is None


def test_portinfo():
    i = P.parse_port_info(P.extract(HR))
    assert i.sensor_id == 78 and i.unit == "BPM" and i.name == "心率"


def test_conversions():
    s = P.parse_sample(P.extract(EXT))
    t = port_channel(3, "temp").getter(s)
    assert 31 < t < 33                       # датчик у руці
    ch = {c.key: c for c in BUILTIN_CHANNELS}
    assert abs(ch["t_in"].getter(s) - 24.1) < 0.01
    assert abs(ch["rh_in"].getter(s) - 32.4) < 0.01
    assert abs(ch["press"].getter(s) - 94.6) < 1e-9


def test_calibration():
    c = Calibration()
    c.set_two_point("x", 1.0, 0.0, 99.0, 100.0)
    assert abs(c.apply("x", 50.0) - 50.0 / 98 * 100 + 100 / 98) < 1e-9
    c.set_offset("y", 24.0, 25.0)
    assert c.apply("y", 30.0) == 31.0


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("OK ", name)
