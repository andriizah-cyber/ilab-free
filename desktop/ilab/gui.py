"""
Графічний інтерфейс iLab Free (Tkinter + matplotlib).
"""
import csv
import queue
import time
import tkinter as tk
from datetime import datetime
from tkinter import filedialog, messagebox, ttk

import matplotlib
matplotlib.use("TkAgg")
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg  # noqa: E402
from matplotlib.figure import Figure  # noqa: E402
from matplotlib.ticker import AutoMinorLocator, FuncFormatter  # noqa: E402

from . import __version__  # noqa: E402
from .device import DemoDevice, SerialDevice, USBDevice, list_bt_ports  # noqa: E402
from .sensors import (BUILTIN_CHANNELS, PORT_TYPES, SENSOR_IDS, Calibration,  # noqa: E402
                      load_config,
                      port_channel, save_config)

PORTS = [1, 2, 3, 4]
INTERVALS = [("0.2 с (макс., ~5/с)", 0.2), ("0.5 с", 0.5), ("1 с", 1.0), ("2 с", 2.0), ("5 с", 5.0),
             ("10 с", 10.0), ("30 с", 30.0), ("1 хв", 60.0), ("5 хв", 300.0)]
EXPERIMENTAL_PORTS = set()

THEMES = {
    "Світлий": dict(fig="#fafafa", ax="#ffffff", grid="#e3e3e3", minor=None, text="#333333",
                    ref="#9e9e9e"),
    "Міліметрівка": dict(fig="#fbf7ee", ax="#fffdf6", grid="#eab38f", minor="#f6dcc8",
                         text="#5d4037", ref="#8d8d8d"),
    "Темний": dict(fig="#1e1f22", ax="#26282c", grid="#3b3e44", minor=None, text="#e6e6e6",
                   ref="#9aa0a6"),
}
TIME_FORMATS = ["секунди", "хв:с", "годинник"]


def _fmt_mmss(x, _pos=None):
    sign = "-" if x < 0 else ""
    x = abs(x)
    return f"{sign}{int(x // 60)}:{int(x % 60):02d}"


class App(tk.Tk):
    def __init__(self, demo=False):
        super().__init__()
        self.title(f"iLab Free {__version__} — цифрова лабораторія")
        self.geometry("1240x740")
        self.minsize(900, 560)
        try:
            import os
            self.iconbitmap(os.path.join(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__))), "ilab.ico"))
        except tk.TclError:
            pass
        self.events = queue.Queue()
        self.device = None
        self.samples = []          # [(t_sec, Sample)]
        self.t0 = None
        self.dirty = False
        self.cal = Calibration()
        self.reference = None      # {"label": str, "t": [...], "series": {назва: [...]}}
        self.cfg = load_config()
        self._load_cfg()
        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.after(100, self._poll_events)
        self.after(1000, self._redraw_tick)
        if demo:
            self.after(300, lambda: self.connect(demo=True))

    # ---------- конфігурація ----------
    def _load_cfg(self):
        c = self.cfg
        self.port_type = {p: c.get("port_type", {}).get(str(p), "temp")
                          for p in PORTS}
        self.enabled = set(c.get("enabled", ["t_in", "rh_in", "press", "light", "port1", "port2",
                                              "port3", "port4"]))
        for k, v in c.get("cal_k", {}).items():
            self.cal.k[k] = v
        for k, v in c.get("cal_b", {}).items():
            self.cal.b[k] = v
        self.interval = c.get("interval", 1.0)
        self.theme = c.get("theme", "Світлий")
        self.time_fmt = c.get("time_fmt", "секунди")
        self.x_all = c.get("x_all", True)

    def _save_cfg(self):
        self.cfg.update({
            "port_type": {str(p): t for p, t in self.port_type.items()},
            "enabled": sorted(self.enabled),
            "cal_k": self.cal.k, "cal_b": self.cal.b,
            "interval": self._interval(), "conn": self.conn_var.get(),
            "theme": self.theme_var.get(), "time_fmt": self.tfmt_var.get(),
            "x_all": bool(self.xall_var.get()),
        })
        save_config(self.cfg)

    # ---------- канали ----------
    def channels(self):
        chs = []
        for p in PORTS:
            chs.append(port_channel(p, self.port_type[p]))
        chs += BUILTIN_CHANNELS
        return chs

    def value(self, ch, sample):
        return self.cal.apply(ch.ckey, ch.getter(sample))

    # ---------- UI ----------
    def _build_ui(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("Big.TLabel", font=("Segoe UI", 11, "bold"))
        style.configure("Accent.TButton", font=("Segoe UI", 10, "bold"))

        bar = ttk.Frame(self, padding=(8, 6))
        bar.pack(side=tk.TOP, fill=tk.X)
        self.conn_var = tk.StringVar(value=self.cfg.get("conn", "USB"))
        self.conn_box = ttk.Combobox(bar, state="readonly", width=17, textvariable=self.conn_var,
                                     postcommand=self._refresh_conn)
        self.conn_box.pack(side=tk.LEFT, padx=(0, 3))
        self._refresh_conn()
        self.btn_conn = ttk.Button(bar, text="Підключити", command=self.connect)
        self.btn_demo = ttk.Button(bar, text="Демо", command=lambda: self.connect(demo=True))
        self.btn_start = ttk.Button(bar, text="▶ Старт", style="Accent.TButton",
                                    command=self.start, state=tk.DISABLED)
        self.btn_stop = ttk.Button(bar, text="■ Стоп", command=self.stop, state=tk.DISABLED)
        for w in (self.btn_conn, self.btn_demo, self.btn_start, self.btn_stop):
            w.pack(side=tk.LEFT, padx=3)
        ttk.Separator(bar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=8)
        ttk.Button(bar, text="Очистити", command=self.clear).pack(side=tk.LEFT, padx=3)
        ttk.Button(bar, text="Зберегти CSV", command=self.export_csv).pack(side=tk.LEFT, padx=3)
        ttk.Button(bar, text="Зберегти Excel", command=self.export_xlsx).pack(side=tk.LEFT, padx=3)
        ttk.Button(bar, text="Калібрування", command=self.open_calibration).pack(side=tk.LEFT, padx=3)

        view = ttk.Frame(self, padding=(8, 0, 8, 6))
        view.pack(side=tk.TOP, fill=tk.X)
        ttk.Label(view, text="Еталон:").pack(side=tk.LEFT)
        ttk.Button(view, text="Поточний → еталон",
                   command=self.ref_from_current).pack(side=tk.LEFT, padx=3)
        ttk.Button(view, text="З файлу…", command=self.ref_from_file).pack(side=tk.LEFT, padx=3)
        self.ref_show = tk.BooleanVar(value=True)
        ttk.Checkbutton(view, text="показати", variable=self.ref_show,
                        command=lambda: self._redraw(force=True)).pack(side=tk.LEFT, padx=3)
        ttk.Button(view, text="Прибрати", command=self.ref_clear).pack(side=tk.LEFT, padx=3)
        self.ref_lbl = ttk.Label(view, text="немає", foreground="#777")
        self.ref_lbl.pack(side=tk.LEFT, padx=6)
        ttk.Label(view, text="Інтервал:").pack(side=tk.LEFT, padx=(20, 2))
        self.int_box = ttk.Combobox(view, state="readonly", width=18,
                                    values=[t for t, _ in INTERVALS])
        vals = [v for _, v in INTERVALS]
        self.int_box.current(vals.index(self.interval) if self.interval in vals else 2)
        self.int_box.pack(side=tk.LEFT)

        body = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        body.pack(fill=tk.BOTH, expand=True)

        # --- ліва панель каналів
        left = ttk.Frame(body, padding=8)
        body.add(left, weight=0)
        ttk.Label(left, text="Канали", style="Big.TLabel").pack(anchor=tk.W, pady=(0, 6))
        self.chk_vars, self.val_labels, self.type_boxes, self.chk_btns = {}, {}, {}, {}

        def row(parent, key, title, with_type_for=None):
            fr = ttk.Frame(parent)
            fr.pack(fill=tk.X, pady=2)
            v = tk.BooleanVar(value=key in self.enabled)
            self.chk_vars[key] = v
            cbtn = ttk.Checkbutton(fr, text=title, variable=v,
                                   command=lambda k=key: self._toggle(k))
            cbtn.pack(side=tk.LEFT)
            self.chk_btns[key] = cbtn
            lab = ttk.Label(fr, text="—", width=12, anchor=tk.E, font=("Consolas", 11))
            lab.pack(side=tk.RIGHT)
            self.val_labels[key] = lab
            if with_type_for is not None:
                p = with_type_for
                cb = ttk.Combobox(parent, state="readonly", width=34,
                                  values=[t.name for t in PORT_TYPES])
                cb.current([t.key for t in PORT_TYPES].index(self.port_type[p]))
                cb.pack(fill=tk.X, padx=(22, 0), pady=(0, 4))
                cb.bind("<<ComboboxSelected>>", lambda e, p=p, cb=cb: self._set_type(p, cb))
                self.type_boxes[p] = cb

        grp = ttk.LabelFrame(left, text="Зовнішні порти", padding=6)
        grp.pack(fill=tk.X)
        for p in PORTS:
            title = f"Порт {p}" + (" (експерим.)" if p in EXPERIMENTAL_PORTS else "")
            row(grp, f"port{p}", title, with_type_for=p)
        grp2 = ttk.LabelFrame(left, text="Вбудовані датчики", padding=6)
        grp2.pack(fill=tk.X, pady=(8, 0))
        for ch in BUILTIN_CHANNELS:
            row(grp2, ch.key, f"{ch.name}, {ch.unit}")
        def opt_changed(*_):
            self._save_cfg()
            self._redraw(force=True)
        grp3 = ttk.LabelFrame(left, text="Вигляд графіків", padding=6)
        grp3.pack(fill=tk.X, pady=(8, 0))
        grp3.columnconfigure(1, weight=1)
        ttk.Label(grp3, text="Фон:").grid(row=0, column=0, sticky=tk.W, pady=2)
        self.theme_var = tk.StringVar(value=self.theme if self.theme in THEMES else "Світлий")
        th = ttk.Combobox(grp3, state="readonly", values=list(THEMES), textvariable=self.theme_var)
        th.grid(row=0, column=1, sticky=tk.EW, padx=(6, 0))
        th.bind("<<ComboboxSelected>>", opt_changed)
        ttk.Label(grp3, text="Вісь часу:").grid(row=1, column=0, sticky=tk.W, pady=2)
        self.tfmt_var = tk.StringVar(value=self.time_fmt)
        tb = ttk.Combobox(grp3, state="readonly", values=TIME_FORMATS, textvariable=self.tfmt_var)
        tb.grid(row=1, column=1, sticky=tk.EW, padx=(6, 0))
        tb.bind("<<ComboboxSelected>>", opt_changed)
        self.xall_var = tk.BooleanVar(value=self.x_all)
        ttk.Checkbutton(grp3, text="Час під кожним графіком", variable=self.xall_var,
                        command=opt_changed).grid(row=2, column=0, columnspan=2, sticky=tk.W,
                                                  pady=(4, 0))
        self.info_lbl = ttk.Label(left, text="", foreground="#555", wraplength=330, justify=tk.LEFT)
        self.info_lbl.pack(anchor=tk.W, pady=(10, 0))

        # --- графіки
        right = ttk.Frame(body)
        body.add(right, weight=1)
        self.fig = Figure(figsize=(7, 5), dpi=96)
        self.fig.set_facecolor("#fafafa")
        self.canvas = FigureCanvasTkAgg(self.fig, master=right)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

        self.status = tk.StringVar(value="Не підключено. Натисніть «Підключити» або «Демо».")
        sb = ttk.Label(self, textvariable=self.status, anchor=tk.W, padding=(8, 3),
                       relief=tk.SUNKEN)
        sb.pack(side=tk.BOTTOM, fill=tk.X)
        self._redraw(force=True)

    def _toggle(self, key):
        if self.chk_vars[key].get():
            self.enabled.add(key)
        else:
            self.enabled.discard(key)
        self._save_cfg()
        self._redraw(force=True)

    def _set_type(self, port, cb):
        self.port_type[port] = PORT_TYPES[cb.current()].key
        self._save_cfg()
        self._update_values()
        self._redraw(force=True)

    def _interval(self):
        try:
            return INTERVALS[self.int_box.current()][1]
        except (AttributeError, IndexError, tk.TclError):
            return 1.0

    # ---------- керування пристроєм ----------
    def _refresh_conn(self):
        vals = ["USB"] + [f"Bluetooth {d}" for d, _ in list_bt_ports()]
        self.conn_box.config(values=vals)
        if self.conn_var.get() not in vals:
            self.conn_var.set(vals[1] if self.conn_var.get().startswith("Bluetooth")
                              and len(vals) > 1 else "USB")

    def _on_portinfo(self, info):
        """Плата повідомила, що стоїть у порту: підписуємо порт і вибираємо тип."""
        if not 1 <= info.port <= 4:
            return
        p, sid = info.port, info.sensor_id
        if getattr(self, "port_ids", {}).get(p) == sid:
            return
        self.port_ids[p] = sid
        btn = self.chk_btns.get(f"port{p}")
        if not sid:
            if btn:
                btn.config(text=f"Порт {p} — порожній")
            return
        typ, name = SENSOR_IDS.get(sid, (None, f"датчик ID {sid}"))
        if btn:
            btn.config(text=f"Порт {p} — {name}")
        if typ and self.port_type.get(p) != typ:
            self.port_type[p] = typ
            self.type_boxes[p].current([t.key for t in PORT_TYPES].index(typ))
            self._save_cfg()
            self._update_values()
            self._redraw(force=True)
        if f"port{p}" not in self.enabled:
            self.enabled.add(f"port{p}")
            self.chk_vars[f"port{p}"].set(True)
            self._redraw(force=True)

    def connect(self, demo=False):
        if self.device:
            self.device.close()
            self.device = None
            self.btn_conn.config(text="Підключити")
            self._set_running(False, connected=False)
            self.status.set("Відключено")
            return
        if demo:
            self.device = DemoDevice(self.events)
        else:
            conn = self.conn_var.get()
            self.cfg["conn"] = conn
            if conn.startswith("Bluetooth"):
                port = conn.split()[-1]
                self.device = SerialDevice(self.events, port)
            else:
                self.device = USBDevice(self.events)
        self.port_ids = {}
        self.device.open()
        self.btn_conn.config(text="Відключити")
        self.status.set("Підключення…")

    def start(self):
        if not self.device:
            return
        if self.samples and not messagebox.askyesno(
                "Новий дослід", "Почати новий запис? Поточні дані буде очищено "
                "(збережіть їх перед цим, якщо потрібно)."):
            return
        self.clear()
        self.device.start(self._interval())
        self._save_cfg()
        self._set_running(True)

    def stop(self):
        if self.device:
            self.device.stop()
        self._set_running(False)

    def _set_running(self, running, connected=True):
        self.btn_start.config(state=tk.DISABLED if running or not connected else tk.NORMAL)
        self.btn_stop.config(state=tk.NORMAL if running else tk.DISABLED)

    def clear(self):
        self.samples.clear()
        self.t0 = None
        self._redraw(force=True)

    # ---------- події від пристрою ----------
    def _poll_events(self):
        try:
            while True:
                kind, data = self.events.get_nowait()
                if kind == "sample":
                    now = time.time()
                    if self.t0 is None:
                        self.t0 = now
                    self.samples.append((now - self.t0, data, datetime.now()))
                    self.dirty = True
                    self._update_values()
                elif kind == "status":
                    self.status.set(data)
                elif kind == "error":
                    self.status.set(data)
                    messagebox.showerror("Помилка", data)
                    if self.device:
                        self.device = None
                        self.btn_conn.config(text="Підключити")
                    self._set_running(False, connected=False)
                elif kind == "connected":
                    if data:
                        self._set_running(False, connected=True)
                    else:
                        self._set_running(False, connected=False)
                elif kind == "portinfo":
                    self._on_portinfo(data)
        except queue.Empty:
            pass
        self.after(100, self._poll_events)

    def _update_values(self):
        if not self.samples:
            for lab in self.val_labels.values():
                lab.config(text="—")
            return
        s = self.samples[-1][1]
        for ch in self.channels():
            v = self.value(ch, s)
            txt = "—" if v is None else (ch.fmt.format(v) + (f" {ch.unit}" if ch.unit else ""))
            self.val_labels[ch.key].config(text=txt)

    # ---------- графіки ----------
    def _redraw_tick(self):
        if self.dirty:
            self._redraw()
        self.after(1000, self._redraw_tick)

    @staticmethod
    def _series_key(ch):
        return f"{ch.name}{', ' + ch.unit if ch.unit else ''}"

    def _time_formatter(self):
        fmt = self.tfmt_var.get()
        if fmt == "хв:с":
            return FuncFormatter(_fmt_mmss), "Час, хв:с"
        if fmt == "годинник" and self.samples:
            t0 = self.samples[0][2]
            from datetime import timedelta
            return (FuncFormatter(lambda x, _p: (t0 + timedelta(seconds=x)).strftime("%H:%M:%S")),
                    "Час (годинник)")
        return None, "Час, с"

    def _redraw(self, force=False):
        self.dirty = False
        th = THEMES.get(self.theme_var.get(), THEMES["Світлий"])
        self.fig.clear()
        self.fig.set_facecolor(th["fig"])
        chs = [c for c in self.channels() if c.key in self.enabled]
        if not chs:
            self.fig.text(0.5, 0.5, "Оберіть канали ліворуч", ha="center", va="center",
                          color="#888", fontsize=13)
            self.canvas.draw_idle()
            return
        ref = self.reference if (self.reference and self.ref_show.get()) else None
        ts = [t for t, _, _ in self.samples]
        axes = self.fig.subplots(len(chs), 1, sharex=True, squeeze=False)[:, 0]
        tformatter, xlabel = self._time_formatter()
        x_all = self.xall_var.get()
        for ax, ch in zip(axes, chs):
            ax.set_facecolor(th["ax"])
            has_ref = False
            if ref:
                rys = ref["series"].get(self._series_key(ch))
                if rys:
                    pts = [(t, y) for t, y in zip(ref["t"], rys) if y is not None]
                    if pts:
                        ax.plot([p[0] for p in pts], [p[1] for p in pts], color=th["ref"],
                                linewidth=1.6, linestyle="--", label=f"Еталон: {ref['label']}")
                        has_ref = True
            ys = [self.value(ch, s) for _, s, _ in self.samples]
            xs = [t for t, y in zip(ts, ys) if y is not None]
            ys = [y for y in ys if y is not None]
            ax.plot(xs, ys, color=ch.color, linewidth=1.9, label="Дослід")
            if has_ref:
                leg = ax.legend(fontsize=7, loc="upper left", frameon=True, framealpha=0.8)
                leg.get_frame().set_facecolor(th["ax"])
                for txt in leg.get_texts():
                    txt.set_color(th["text"])
            ax.set_ylabel(ch.unit or "АЦП", fontsize=9, color=th["text"])
            ax.set_title(ch.name, fontsize=9, loc="left", color=th["text"])
            ax.grid(True, which="major", color=th["grid"], linewidth=0.8)
            if th["minor"]:
                ax.xaxis.set_minor_locator(AutoMinorLocator(5))
                ax.yaxis.set_minor_locator(AutoMinorLocator(5))
                ax.grid(True, which="minor", color=th["minor"], linewidth=0.5)
            ax.tick_params(labelsize=8, colors=th["text"])
            for side in ("top", "right"):
                ax.spines[side].set_visible(False)
            for side in ("left", "bottom"):
                ax.spines[side].set_color(th["grid"] if th is not THEMES["Світлий"] else "#999")
            if tformatter:
                ax.xaxis.set_major_formatter(tformatter)
            if x_all:
                ax.tick_params(labelbottom=True)
            if ys:
                ax.annotate(ch.fmt.format(ys[-1]), xy=(1.0, 1.0), xycoords="axes fraction",
                            ha="right", va="bottom", fontsize=10, color=ch.color,
                            fontweight="bold")
        axes[-1].set_xlabel(xlabel, fontsize=9, color=th["text"])
        self.fig.tight_layout(h_pad=0.8 if x_all else 0.6)
        self.canvas.draw_idle()

    # ---------- еталон (порівняння) ----------
    def ref_from_current(self):
        if not self.samples:
            messagebox.showinfo("Еталон", "Спочатку виконайте вимірювання.")
            return
        series = {self._series_key(c): [self.value(c, s) for _, s, _ in self.samples]
                  for c in self.channels()}
        t0 = self.samples[0][2]
        self._set_reference(f"дослід {t0:%H:%M}", [t for t, _, _ in self.samples], series)

    def ref_from_file(self):
        path = filedialog.askopenfilename(
            filetypes=[("Дані iLab", "*.csv *.xlsx"), ("CSV", "*.csv"), ("Excel", "*.xlsx")])
        if not path:
            return
        try:
            head, rows = self._read_table(path)
            it = head.index("t, с")
        except Exception as e:  # noqa
            messagebox.showerror("Еталон", f"Не вдалося прочитати файл:\n{e}")
            return

        def num(v):
            if v is None or v == "":
                return None
            if isinstance(v, (int, float)):
                return float(v)
            try:
                return float(str(v).replace(",", ".").strip())
            except ValueError:
                return None
        t = [num(r[it]) for r in rows]
        series = {h: [num(r[i]) if i < len(r) else None for r in rows]
                  for i, h in enumerate(head) if i > it}
        import os
        self._set_reference(os.path.splitext(os.path.basename(path))[0],
                            [x if x is not None else 0 for x in t], series)

    @staticmethod
    def _read_table(path):
        if path.lower().endswith(".xlsx"):
            from openpyxl import load_workbook
            ws = load_workbook(path, read_only=True, data_only=True).active
            rows = [list(r) for r in ws.iter_rows(values_only=True)]
        else:
            with open(path, encoding="utf-8-sig", newline="") as f:
                rows = list(csv.reader(f, delimiter=";"))
        head = [str(h) if h is not None else "" for h in rows[0]]
        return head, [r for r in rows[1:] if any(v not in (None, "") for v in r)]

    def _set_reference(self, label, t, series):
        self.reference = {"label": label, "t": t, "series": series}
        self.ref_show.set(True)
        short = label if len(label) <= 16 else label[:15] + "…"
        self.ref_lbl.config(text=f"{short} ({len(t)} вим.)", foreground="#2e7d32")
        self._redraw(force=True)

    def ref_clear(self):
        self.reference = None
        self.ref_lbl.config(text="немає", foreground="#777")
        self._redraw(force=True)

    # ---------- експорт ----------
    def _table(self):
        chs = [c for c in self.channels() if c.key in self.enabled] or self.channels()
        head = ["Час", "t, с"] + [f"{c.name}{', ' + c.unit if c.unit else ''}" for c in chs]
        rows = []
        for t, s, dt in self.samples:
            rows.append([dt.strftime("%H:%M:%S"), round(t, 2)] + [self.value(c, s) for c in chs])
        return head, rows

    def export_csv(self):
        if not self.samples:
            messagebox.showinfo("Немає даних", "Спочатку виконайте вимірювання.")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".csv", filetypes=[("CSV", "*.csv")],
            initialfile=f"дослід_{datetime.now():%Y%m%d_%H%M}.csv")
        if not path:
            return
        head, rows = self._table()
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f, delimiter=";")
            w.writerow(head)
            for r in rows:   # десяткова кома — для Excel з українськими налаштуваннями
                w.writerow([("" if v is None else str(round(v, 4)).replace(".", ","))
                            if isinstance(v, (float, int)) or v is None else v for v in r])
        self.status.set(f"Збережено: {path}")

    def export_xlsx(self):
        if not self.samples:
            messagebox.showinfo("Немає даних", "Спочатку виконайте вимірювання.")
            return
        try:
            from openpyxl import Workbook
            from openpyxl.chart import LineChart, Reference
        except ImportError:
            messagebox.showerror("Excel", "Потрібен пакет openpyxl:\npy -m pip install openpyxl")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".xlsx", filetypes=[("Excel", "*.xlsx")],
            initialfile=f"дослід_{datetime.now():%Y%m%d_%H%M}.xlsx")
        if not path:
            return
        head, rows = self._table()
        wb = Workbook()
        ws = wb.active
        ws.title = "Дані"
        ws.append(head)
        for r in rows:
            ws.append([round(v, 4) if isinstance(v, float) else v for v in r])
        for i, _ in enumerate(head, 1):
            ws.column_dimensions[ws.cell(1, i).column_letter].width = 16
        n = len(rows) + 1
        for ci in range(3, len(head) + 1):
            ch = LineChart()
            ch.title = head[ci - 1]
            ch.x_axis.title = "t, с"
            ch.height, ch.width = 7, 16
            ch.add_data(Reference(ws, min_col=ci, min_row=1, max_row=n), titles_from_data=True)
            ch.set_categories(Reference(ws, min_col=2, min_row=2, max_row=n))
            ch.legend = None
            ws.add_chart(ch, f"{ws.cell(1, len(head) + 2).column_letter}{2 + (ci - 3) * 15}")
        wb.save(path)
        self.status.set(f"Збережено: {path}")

    # ---------- калібрування ----------
    def open_calibration(self):
        CalibrationDialog(self)

    def on_close(self):
        self._save_cfg()
        if self.device:
            self.device.close()
        self.destroy()


class CalibrationDialog(tk.Toplevel):
    """Калібрування за двома точками (або зсув за однією) для будь-якого каналу."""

    def __init__(self, app: App):
        super().__init__(app)
        self.app = app
        self.title("Калібрування")
        self.resizable(False, False)
        self.transient(app)
        chs = app.channels()
        self.chs = chs
        frm = ttk.Frame(self, padding=12)
        frm.pack(fill=tk.BOTH)
        ttk.Label(frm, text="Канал:").grid(row=0, column=0, sticky=tk.W)
        self.cb = ttk.Combobox(frm, state="readonly", width=44, values=[c.name for c in chs])
        self.cb.grid(row=0, column=1, columnspan=3, sticky=tk.W, pady=4)
        self.cb.current(0)
        self.cb.bind("<<ComboboxSelected>>", lambda e: self._refresh())

        ttk.Label(frm, text="Поточне (без калібрування):").grid(row=1, column=0, sticky=tk.W)
        self.cur = ttk.Label(frm, text="—", font=("Consolas", 11, "bold"))
        self.cur.grid(row=1, column=1, sticky=tk.W)
        ttk.Label(frm, text="Зараз:").grid(row=2, column=0, sticky=tk.W)
        self.desc = ttk.Label(frm, text="")
        self.desc.grid(row=2, column=1, columnspan=3, sticky=tk.W)

        ttk.Label(frm, text="Виміряне", foreground="#555").grid(row=3, column=1, pady=(10, 0))
        ttk.Label(frm, text="Еталон", foreground="#555").grid(row=3, column=3, pady=(10, 0))
        self.m1, self.r1, self.m2, self.r2 = (tk.StringVar() for _ in range(4))
        for i, (m, r) in enumerate(((self.m1, self.r1), (self.m2, self.r2))):
            ttk.Label(frm, text=f"Точка {i + 1}:").grid(row=4 + i, column=0, sticky=tk.W)
            ttk.Entry(frm, textvariable=m, width=12).grid(row=4 + i, column=1, pady=2)
            ttk.Button(frm, text="← взяти поточне",
                       command=lambda m=m: m.set(self._cur_text())).grid(row=4 + i, column=2, padx=4)
            ttk.Entry(frm, textvariable=r, width=12).grid(row=4 + i, column=3)

        ttk.Label(frm, foreground="#666", wraplength=470, justify=tk.LEFT, text=(
            "Помістіть датчик у середовище з відомим значенням (напр. тануча крига 0 °C, "
            "кипіння 100 °C; для вологості — насичений розчин солі NaCl ≈ 75 %), дочекайтеся "
            "стабілізації, натисніть «взяти поточне» і введіть еталон. Для простого зсуву "
            "достатньо однієї точки.")).grid(row=6, column=0, columnspan=4, sticky=tk.W, pady=8)
        b = ttk.Frame(frm)
        b.grid(row=7, column=0, columnspan=4, sticky=tk.E)
        ttk.Button(b, text="Скинути", command=self._reset).pack(side=tk.LEFT, padx=3)
        ttk.Button(b, text="Зсув (1 точка)", command=self._offset).pack(side=tk.LEFT, padx=3)
        ttk.Button(b, text="Застосувати (2 точки)", command=self._two).pack(side=tk.LEFT, padx=3)
        ttk.Button(b, text="Закрити", command=self.destroy).pack(side=tk.LEFT, padx=3)
        self._refresh()
        self._tick()

    def _ch(self):
        return self.chs[self.cb.current()]

    def _raw(self):
        if not self.app.samples:
            return None
        return self._ch().getter(self.app.samples[-1][1])

    def _cur_text(self):
        v = self._raw()
        return "" if v is None else f"{v:.4g}"

    def _refresh(self):
        self.desc.config(text=self.app.cal.describe(self._ch().ckey))

    def _tick(self):
        if not self.winfo_exists():
            return
        self.cur.config(text=self._cur_text() or "—")
        self.after(500, self._tick)

    @staticmethod
    def _f(var):
        return float(var.get().replace(",", ".").strip())

    def _apply_done(self):
        self.app._save_cfg()
        self.app._update_values()
        self.app._redraw(force=True)
        self._refresh()

    def _two(self):
        try:
            self.app.cal.set_two_point(self._ch().ckey, self._f(self.m1), self._f(self.r1),
                                       self._f(self.m2), self._f(self.r2))
        except ValueError as e:
            messagebox.showerror("Калібрування", f"Перевірте числа: {e}", parent=self)
            return
        self._apply_done()

    def _offset(self):
        try:
            self.app.cal.set_offset(self._ch().ckey, self._f(self.m1), self._f(self.r1))
        except ValueError:
            messagebox.showerror("Калібрування", "Заповніть точку 1.", parent=self)
            return
        self._apply_done()

    def _reset(self):
        self.app.cal.reset(self._ch().ckey)
        self._apply_done()
