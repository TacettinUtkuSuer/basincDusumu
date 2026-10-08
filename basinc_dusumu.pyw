# -*- coding: utf-8 -*-
"""
Basınç Düşümü / Kayıp Katsayısı Hesap Programı

Sekmeler
  1) Akışkanlar      : Sıcaklığa bağlı ρ, cp, μ, k tabloları; grafik, tablo, sıcaklık sorgusu
  2) Tek Nokta       : Akışkan-1 için Re ve Lc hesabı, aynı nokta için Akışkan-2 debisi
  3) CSV → Re-Lc     : Ölçüm CSV'sini Re x Lc tablosuna çevirme, grafik, dışa aktarma
  4) dP - Debi       : Re-Lc eğrisinden farklı sıcaklıklar için dP vs debi eğrileri

Tüm veriler programın yanındaki 'basinc_dusumu_data.json' dosyasına otomatik kaydedilir
ve program açıldığında otomatik yüklenir.

Gereksinimler: Python 3.8+, numpy, matplotlib   (pip install numpy matplotlib)
"""

import csv
import json
import math
import os
import re
import sys
import traceback

import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog

try:
    import numpy as np
    import matplotlib
    matplotlib.use("TkAgg")
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
    from matplotlib.ticker import NullFormatter, LogLocator, FuncFormatter
except ImportError as _e:  # pyw'de konsol olmadığı için hatayı pencerede göster
    _r = tk.Tk()
    _r.withdraw()
    messagebox.showerror(
        "Eksik paket",
        "Gerekli Python paketleri bulunamadı:\n\n%s\n\n"
        "Komut satırında şunu çalıştırın:\n    pip install numpy matplotlib" % _e)
    sys.exit(1)


APP_TITLE = "Basınç Düşümü - Kayıp Katsayısı Hesaplayıcı"
DATA_FILENAME = "basinc_dusumu_data.json"
DATA_VERSION = 1

# ---------------------------------------------------------------------------
# Birimler
# ---------------------------------------------------------------------------
# (tip, çarpan) -> kütlesel: kg/s'ye çarpan, hacimsel: m³/s'ye çarpan
FLOW_UNITS = {
    "kg/s": ("m", 1.0),
    "kg/h": ("m", 1.0 / 3600.0),
    "g/s": ("m", 1e-3),
    "LPM": ("v", 1e-3 / 60.0),
    "m³/h": ("v", 1.0 / 3600.0),
    "m³/s": ("v", 1.0),
}
FLOW_UNIT_NAMES = list(FLOW_UNITS.keys())

DP_UNITS = {"Pa": 1.0, "kPa": 1e3, "mbar": 1e2, "bar": 1e5, "psi": 6894.757293168}
DP_UNIT_NAMES = list(DP_UNITS.keys())

TEMP_UNITS = ["°C", "K", "°F"]

PROPS = [
    ("rho", "ρ [kg/m³]"),
    ("cp", "cp [J/kg·K]"),
    ("mu", "μ [Pa·s]"),
    ("k", "k [W/m·K]"),
]
TABLE_COLS = ["T [°C]"] + [p[1] for p in PROPS]

EXPORT_FORMATS = {
    "comma": ("Ayraç ','  -  ondalık '.'", ",", "."),
    "semicolon": ("Ayraç ';'  -  ondalık ',' (Excel TR)", ";", ","),
}

DEFAULT_FLUIDS = {
    # Su, 1 atm (yaklaşık literatür değerleri)
    "Su": [
        [0.0, 999.8, 4217.0, 1.792e-3, 0.561],
        [10.0, 999.7, 4192.0, 1.307e-3, 0.580],
        [20.0, 998.2, 4182.0, 1.002e-3, 0.598],
        [30.0, 995.7, 4178.0, 0.798e-3, 0.615],
        [40.0, 992.2, 4179.0, 0.653e-3, 0.631],
        [50.0, 988.0, 4181.0, 0.547e-3, 0.644],
        [60.0, 983.2, 4185.0, 0.467e-3, 0.654],
        [70.0, 977.8, 4190.0, 0.404e-3, 0.663],
        [80.0, 971.8, 4197.0, 0.355e-3, 0.670],
        [90.0, 965.3, 4205.0, 0.315e-3, 0.675],
        [100.0, 958.4, 4216.0, 0.282e-3, 0.679],
    ],
}


def to_mdot(value, unit, rho):
    kind, f = FLOW_UNITS[unit]
    return value * f if kind == "m" else value * f * rho


def from_mdot(mdot, unit, rho):
    kind, f = FLOW_UNITS[unit]
    return mdot / f if kind == "m" else mdot / rho / f


def temp_to_c(value, unit):
    if unit == "K":
        return value - 273.15
    if unit == "°F":
        return (value - 32.0) * 5.0 / 9.0
    return value


def parse_float(s):
    """Kullanıcı girişini sayıya çevirir ('1,5' ve '1.5' kabul). Boş/geçersiz -> None."""
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return float(s)
    s = str(s).strip().replace(" ", "")
    if not s:
        return None
    if "," in s and "." not in s:
        s = s.replace(",", ".")
    try:
        v = float(s)
    except ValueError:
        return None
    return v if math.isfinite(v) else None


def to_float(s, dec="."):
    """CSV hücresini verilen ondalık ayıraca göre sayıya çevirir."""
    if s is None:
        return None
    s = str(s).strip().replace(" ", "").replace("\u00a0", "")
    if not s:
        return None
    if dec == ",":
        s = s.replace(",", ".")
    try:
        v = float(s)
    except ValueError:
        return None
    return v if math.isfinite(v) else None


def fmt(x, digits=6):
    if x is None:
        return ""
    try:
        if not math.isfinite(x):
            return str(x)
    except TypeError:
        return str(x)
    return "{:.{d}g}".format(x, d=digits)


# ---------------------------------------------------------------------------
# Akışkan özellikleri
# ---------------------------------------------------------------------------
def fluid_props(rows, T):
    """rows: [[T, rho, cp, mu, k], ...]. Doğrusal interpolasyon; aralık dışında uç değer
    sabit tutulur. Dönüş: (dict, aralık_dışı_mı)"""
    if not rows:
        raise ValueError("Akışkan tablosu boş.")
    arr = np.array(sorted(rows, key=lambda r: r[0]), dtype=float)
    Ts = arr[:, 0]
    out = {}
    for i, (key, _) in enumerate(PROPS):
        out[key] = float(np.interp(T, Ts, arr[:, i + 1]))
    oor = bool(T < Ts[0] - 1e-9 or T > Ts[-1] + 1e-9)
    return out, oor


def fluid_props_checked(rows, T, name):
    p, oor = fluid_props(rows, T)
    if p["rho"] <= 0 or p["mu"] <= 0:
        raise ValueError("'%s' için T=%s °C'de ρ veya μ sıfır/negatif." % (name, fmt(T)))
    return p, oor


# ---------------------------------------------------------------------------
# CSV yardımcıları
# ---------------------------------------------------------------------------
def read_text_file(path):
    with open(path, "rb") as f:
        data = f.read()
    for enc in ("utf-8-sig", "cp1254", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def detect_delimiter(text, skip=0):
    lines = [l for l in text.splitlines()[skip:] if l.strip()][:30]
    if not lines:
        return ","
    for d in ("\t", ";", ","):
        counts = [l.count(d) for l in lines]
        if min(counts) > 0 and len(set(counts)) == 1:
            return d
    for d in ("\t", ";", ","):
        counts = [l.count(d) for l in lines]
        if min(counts) > 0:
            return d
    return " "


def split_rows(text, delim, skip=0):
    lines = [l for l in text.splitlines()[skip:] if l.strip()]
    if delim == " ":
        rows = [l.split() for l in lines]
    else:
        rows = list(csv.reader(lines, delimiter=delim))
    return [[c.strip() for c in r] for r in rows]


_RE_COMMA_NUM = re.compile(r"^[-+]?\d+,\d+([eE][-+]?\d+)?$")
_RE_DOT_NUM = re.compile(r"^[-+]?\d*\.\d+([eE][-+]?\d+)?$")


def detect_decimal(rows, delim):
    if delim == ",":
        return "."
    comma = dot = 0
    for r in rows[:200]:
        for c in r:
            if _RE_COMMA_NUM.match(c):
                comma += 1
            elif _RE_DOT_NUM.match(c):
                dot += 1
    return "," if comma > dot else "."


def row_is_numeric(row, dec):
    vals = [c for c in row if c != ""]
    return bool(vals) and all(to_float(c, dec) is not None for c in vals)


def parse_numeric_table(text, ncols):
    """Panodan/CSV'den gelen metinden en az ncols sayısal sütunu olan satırları alır."""
    delim = detect_delimiter(text)
    rows = split_rows(text, delim)
    dec = detect_decimal(rows, delim)
    out = []
    for r in rows:
        vals = [to_float(c, dec) for c in r if c != ""]
        if len(vals) >= ncols and all(v is not None for v in vals[:ncols]):
            out.append(vals[:ncols])
    return out


# ---------------------------------------------------------------------------
# Re - Lc modeli
# ---------------------------------------------------------------------------
MODEL_KINDS = {
    "interp": "log-log interpolasyon",
    "power": "Güç yasası fit: Lc = a·Re^b",
    "poly2": "log-log 2. derece polinom fit",
}
EXTRAP_KINDS = {
    "clamp": "Uç değeri sabit tut",
    "extrap": "log-log ekstrapolasyon",
}


def build_model(re_vals, k_vals, kind="interp", extrap="clamp"):
    re_a = np.asarray(re_vals, dtype=float)
    k_a = np.asarray(k_vals, dtype=float)
    m = np.isfinite(re_a) & np.isfinite(k_a) & (re_a > 0) & (k_a > 0)
    if m.sum() < 2:
        raise ValueError("Eğride Re>0 ve Lc>0 olan en az 2 nokta olmalı.")
    x = np.log(re_a[m])
    y = np.log(k_a[m])
    xu, inv = np.unique(x, return_inverse=True)
    yu = np.array([y[inv == i].mean() for i in range(len(xu))])
    re_min, re_max = float(np.exp(xu[0])), float(np.exp(xu[-1]))

    if kind == "power":
        c = np.polyfit(x, y, 1)
        a, b = math.exp(c[1]), c[0]
        yhat = np.polyval(c, x)
        desc = "Lc = %s · Re^(%s)   R²(log) = %s" % (fmt(a, 5), fmt(b, 5), fmt(_r2(y, yhat), 4))

        def f(Re):
            return np.exp(np.polyval(c, np.log(np.asarray(Re, dtype=float))))
    elif kind == "poly2":
        if len(xu) < 3:
            raise ValueError("2. derece polinom için en az 3 farklı Re gerekli.")
        c = np.polyfit(x, y, 2)
        yhat = np.polyval(c, x)
        desc = "ln(Lc) = %s·ln(Re)² + %s·ln(Re) + %s   R²(log) = %s" % (
            fmt(c[0], 5), fmt(c[1], 5), fmt(c[2], 5), fmt(_r2(y, yhat), 4))

        def f(Re):
            return np.exp(np.polyval(c, np.log(np.asarray(Re, dtype=float))))
    else:
        if len(xu) < 2:
            raise ValueError("İnterpolasyon için en az 2 farklı Re gerekli.")
        desc = "log-log interpolasyon, %d nokta (%s)" % (len(xu), EXTRAP_KINDS.get(extrap, ""))

        def f(Re):
            lx = np.log(np.asarray(Re, dtype=float))
            ly = np.interp(lx, xu, yu)
            if extrap == "extrap":
                s0 = (yu[1] - yu[0]) / (xu[1] - xu[0])
                s1 = (yu[-1] - yu[-2]) / (xu[-1] - xu[-2])
                ly = np.where(lx < xu[0], yu[0] + s0 * (lx - xu[0]), ly)
                ly = np.where(lx > xu[-1], yu[-1] + s1 * (lx - xu[-1]), ly)
            return np.exp(ly)

    return f, re_min, re_max, desc


def _r2(y, yhat):
    ss_res = float(np.sum((y - yhat) ** 2))
    ss_tot = float(np.sum((y - np.mean(y)) ** 2))
    return 1.0 - ss_res / ss_tot if ss_tot > 0 else 1.0


# ---------------------------------------------------------------------------
# Ortak GUI yardımcıları
# ---------------------------------------------------------------------------
def make_tree(parent, columns, height=10, widths=None):
    frame = ttk.Frame(parent)
    tree = ttk.Treeview(frame, columns=columns, show="headings", height=height)
    vsb = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
    hsb = ttk.Scrollbar(frame, orient="horizontal", command=tree.xview)
    tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
    tree.grid(row=0, column=0, sticky="nsew")
    vsb.grid(row=0, column=1, sticky="ns")
    hsb.grid(row=1, column=0, sticky="ew")
    frame.rowconfigure(0, weight=1)
    frame.columnconfigure(0, weight=1)
    set_tree_columns(tree, columns, widths)
    return frame, tree


def set_tree_columns(tree, columns, widths=None):
    tree.delete(*tree.get_children())
    tree["columns"] = columns
    for i, c in enumerate(columns):
        w = widths[i] if widths and i < len(widths) else 100
        tree.heading(c, text=c)
        tree.column(c, width=w, minwidth=50, anchor="e", stretch=True)


def make_plot(parent, figsize=(7, 5), constrained=False):
    frame = ttk.Frame(parent)
    if constrained:
        try:
            fig = Figure(figsize=figsize, dpi=100, layout="constrained")
        except TypeError:
            fig = Figure(figsize=figsize, dpi=100, constrained_layout=True)
    else:
        fig = Figure(figsize=figsize, dpi=100)
    canvas = FigureCanvasTkAgg(fig, master=frame)
    tb = NavigationToolbar2Tk(canvas, frame, pack_toolbar=False)
    tb.update()
    tb.pack(side="bottom", fill="x")
    canvas.get_tk_widget().pack(side="top", fill="both", expand=True)
    return frame, fig, canvas


def set_initial_sash(pw, frac):
    """PanedWindow ilk gösterildiğinde ayırıcıyı genişliğin 'frac' oranına koyar."""
    def on_map(_e=None):
        pw.unbind("<Map>")
        pw.after(50, lambda: pw.sashpos(0, int(pw.winfo_width() * frac)))
    pw.bind("<Map>", on_map)


def no_minor_labels(ax):
    """Log eksenlerde okunaklı etiketler: 1, 1.5, 2, 3, 5, 7 x 10^n."""
    for axis, scale, subs in ((ax.xaxis, ax.get_xscale(), (1.0, 2.0, 5.0)),
                              (ax.yaxis, ax.get_yscale(), (1.0, 1.5, 2.0, 3.0, 5.0, 7.0))):
        if scale != "log":
            continue
        axis.set_major_locator(LogLocator(base=10, subs=subs))
        axis.set_major_formatter(FuncFormatter(lambda v, _p: "%g" % v))
        axis.set_minor_formatter(NullFormatter())


def labeled(parent, text, widget, row, col=0, **grid):
    ttk.Label(parent, text=text).grid(row=row, column=col, sticky="w", padx=(4, 2), pady=2)
    widget.grid(row=row, column=col + 1, sticky="ew", padx=(2, 6), pady=2, **grid)
    return widget


class BaseTab(ttk.Frame):
    def __init__(self, master, app):
        super().__init__(master)
        self.app = app
        self.vars = {}
        self.fluid_vars = []
        self.fluid_combos = []

    def var(self, key, default="", kind=tk.StringVar):
        v = kind(value=default)
        v.trace_add("write", lambda *a: self.app.schedule_save())
        self.vars[key] = v
        return v

    def fluid_combo(self, parent, key):
        v = self.var(key, "")
        cb = ttk.Combobox(parent, textvariable=v, state="readonly", width=22)
        self.fluid_vars.append(v)
        self.fluid_combos.append(cb)
        return cb

    def refresh_fluids(self):
        names = self.app.fluid_names()
        for v, cb in zip(self.fluid_vars, self.fluid_combos):
            cb["values"] = names
            if v.get() not in names:
                v.set(names[0] if names else "")

    def rename_fluid(self, old, new):
        for v in self.fluid_vars:
            if v.get() == old:
                v.set(new)

    def get_state(self):
        return {k: v.get() for k, v in self.vars.items()}

    def set_state(self, st):
        for k, v in self.vars.items():
            if k in st:
                try:
                    v.set(st[k])
                except (tk.TclError, TypeError, ValueError):
                    pass

    def after_load(self):
        pass

    def fluid_rows(self, name):
        if not name:
            raise ValueError("Akışkan seçilmedi.")
        rows = self.app.data["fluids"].get(name)
        if not rows:
            raise ValueError("'%s' akışkanının özellik tablosu boş." % name)
        return rows

    @staticmethod
    def need_float(var, label, positive=False):
        v = parse_float(var.get())
        if v is None:
            raise ValueError("'%s' için geçerli bir sayı girin." % label)
        if positive and v <= 0:
            raise ValueError("'%s' sıfırdan büyük olmalı." % label)
        return v


# ---------------------------------------------------------------------------
# Sekme 1 - Akışkanlar
# ---------------------------------------------------------------------------
class FluidsTab(BaseTab):
    def __init__(self, master, app):
        super().__init__(master, app)
        self.sel = self.var("selected", "")
        self.qT = self.var("query_T", "25")
        self._editor = None

        pw = ttk.PanedWindow(self, orient="horizontal")
        pw.pack(fill="both", expand=True, padx=4, pady=4)

        left = ttk.Frame(pw)
        pw.add(left, weight=1)
        right = ttk.Frame(pw)
        pw.add(right, weight=2)

        # Akışkan listesi
        lf = ttk.LabelFrame(left, text="Akışkanlar")
        lf.pack(fill="x", padx=2, pady=2)
        self.listbox = tk.Listbox(lf, height=6, exportselection=False)
        self.listbox.grid(row=0, column=0, rowspan=5, sticky="nsew", padx=4, pady=4)
        self.listbox.bind("<<ListboxSelect>>", self._on_list_select)
        lf.columnconfigure(0, weight=1)
        for i, (t, cmd) in enumerate([("Yeni", self.new_fluid), ("Kopyala", self.copy_fluid),
                                      ("Yeniden adlandır", self.rename_selected),
                                      ("Sil", self.delete_fluid)]):
            ttk.Button(lf, text=t, command=cmd).grid(row=i, column=1, sticky="ew", padx=4, pady=1)

        # Özellik tablosu
        tf = ttk.LabelFrame(left, text="Sıcaklığa bağlı özellikler (hücreye çift tıklayarak düzenleyin)")
        tf.pack(fill="both", expand=True, padx=2, pady=2)
        tframe, self.tree = make_tree(tf, TABLE_COLS, height=12, widths=[70, 90, 90, 100, 90])
        tframe.pack(fill="both", expand=True, padx=4, pady=4)
        self.tree.bind("<Double-1>", self._on_double_click)
        self.tree.bind("<Delete>", lambda e: self.delete_rows())
        bf = ttk.Frame(tf)
        bf.pack(fill="x", padx=4, pady=(0, 4))
        for t, cmd in [("Satır ekle", self.add_row), ("Seçili satırları sil", self.delete_rows),
                       ("Panodan yapıştır", self.paste_rows), ("CSV'den al", self.import_csv),
                       ("CSV'ye ver", self.export_csv)]:
            ttk.Button(bf, text=t, command=cmd).pack(side="left", padx=2)
        ttk.Label(tf, text="Birimler: T [°C], ρ [kg/m³], cp [J/kg·K], μ [Pa·s] (1 cP = 0.001 Pa·s), "
                           "k [W/m·K].  Excel'den 5 sütun (T, ρ, cp, μ, k) kopyalayıp yapıştırabilirsiniz.",
                  wraplength=520, foreground="#555").pack(fill="x", padx=4, pady=(0, 4))

        # Sorgu
        qf = ttk.LabelFrame(left, text="Sıcaklıkta değer sorgula")
        qf.pack(fill="x", padx=2, pady=2)
        ttk.Label(qf, text="T [°C]:").grid(row=0, column=0, padx=4, pady=4)
        e = ttk.Entry(qf, textvariable=self.qT, width=10)
        e.grid(row=0, column=1, padx=4)
        e.bind("<Return>", lambda ev: self.query())
        ttk.Button(qf, text="Göster", command=self.query).grid(row=0, column=2, padx=4)
        self.q_result = ttk.Label(qf, text="", justify="left", font=("Consolas", 10))
        self.q_result.grid(row=1, column=0, columnspan=4, sticky="w", padx=4, pady=4)

        # Grafik
        pframe, self.fig, self.canvas = make_plot(right)
        pframe.pack(fill="both", expand=True)

    # --- yardımcılar
    def current_rows(self):
        return self.app.data["fluids"].get(self.sel.get())

    def refresh_fluids(self):
        names = self.app.fluid_names()
        self.listbox.delete(0, "end")
        for n in names:
            self.listbox.insert("end", n)
        if self.sel.get() not in names:
            self.sel.set(names[0] if names else "")
        if self.sel.get() in names:
            i = names.index(self.sel.get())
            self.listbox.selection_clear(0, "end")
            self.listbox.selection_set(i)
            self.listbox.see(i)
        self.refresh_table()

    def rename_fluid(self, old, new):
        if self.sel.get() == old:
            self.sel.set(new)

    def _on_list_select(self, _ev=None):
        s = self.listbox.curselection()
        if s:
            self.sel.set(self.listbox.get(s[0]))
            self.refresh_table()

    def refresh_table(self):
        self.tree.delete(*self.tree.get_children())
        rows = self.current_rows() or []
        for i, r in enumerate(rows):
            self.tree.insert("", "end", iid=str(i), values=[fmt(v) for v in r])
        self.query(silent=True)

    def data_changed(self):
        rows = self.current_rows()
        if rows is not None:
            rows.sort(key=lambda r: r[0])
        self.refresh_table()
        self.app.schedule_save()

    # --- akışkan yönetimi
    def _ask_name(self, title, initial=""):
        name = simpledialog.askstring(title, "Akışkan adı:", initialvalue=initial, parent=self)
        if name is None:
            return None
        name = name.strip()
        if not name:
            return None
        if name in self.app.data["fluids"]:
            messagebox.showwarning(title, "Bu isimde bir akışkan zaten var.", parent=self)
            return None
        return name

    def new_fluid(self):
        name = self._ask_name("Yeni akışkan")
        if name:
            self.app.data["fluids"][name] = []
            self.sel.set(name)
            self.app.fluids_changed()

    def copy_fluid(self):
        src = self.sel.get()
        if not src:
            return
        name = self._ask_name("Akışkanı kopyala", src + " (kopya)")
        if name:
            self.app.data["fluids"][name] = [list(r) for r in self.app.data["fluids"][src]]
            self.sel.set(name)
            self.app.fluids_changed()

    def rename_selected(self):
        old = self.sel.get()
        if not old:
            return
        new = self._ask_name("Yeniden adlandır", old)
        if new:
            fl = self.app.data["fluids"]
            fl[new] = fl.pop(old)
            self.app.rename_fluid(old, new)
            self.app.fluids_changed()

    def delete_fluid(self):
        name = self.sel.get()
        if name and messagebox.askyesno("Sil", "'%s' akışkanı silinsin mi?" % name, parent=self):
            del self.app.data["fluids"][name]
            self.sel.set("")
            self.app.fluids_changed()

    # --- tablo düzenleme
    def _ensure_fluid(self):
        if self.current_rows() is None:
            messagebox.showinfo("Akışkan", "Önce bir akışkan oluşturun/seçin.", parent=self)
            return False
        return True

    def add_row(self):
        if not self._ensure_fluid():
            return
        rows = self.current_rows()
        if rows:
            last = list(rows[-1])
            last[0] += 10.0
            rows.append(last)
        else:
            rows.append([20.0, 1000.0, 4180.0, 1e-3, 0.6])
        self.data_changed()

    def delete_rows(self):
        rows = self.current_rows()
        sel = self.tree.selection()
        if rows is None or not sel:
            return
        for idx in sorted((int(s) for s in sel), reverse=True):
            if 0 <= idx < len(rows):
                rows.pop(idx)
        self.data_changed()

    def _merge_rows(self, new_rows, source):
        if not new_rows:
            messagebox.showwarning(source, "5 sayısal sütunlu (T, ρ, cp, μ, k) satır bulunamadı.", parent=self)
            return
        rows = self.current_rows()
        if rows:
            ans = messagebox.askyesnocancel(
                source, "%d satır bulundu.\n\nEvet: mevcut tabloyu değiştir\nHayır: mevcut tabloya ekle"
                % len(new_rows), parent=self)
            if ans is None:
                return
            if ans:
                rows.clear()
        rows.extend(new_rows)
        self.data_changed()

    def paste_rows(self):
        if not self._ensure_fluid():
            return
        try:
            text = self.clipboard_get()
        except tk.TclError:
            messagebox.showwarning("Pano", "Panoda metin yok.", parent=self)
            return
        self._merge_rows(parse_numeric_table(text, 5), "Panodan yapıştır")

    def import_csv(self):
        if not self._ensure_fluid():
            return
        path = filedialog.askopenfilename(parent=self, title="Akışkan özellik CSV",
                                          filetypes=[("CSV/Metin", "*.csv *.txt *.dat"), ("Tümü", "*.*")])
        if path:
            self._merge_rows(parse_numeric_table(read_text_file(path), 5), "CSV'den al")

    def export_csv(self):
        rows = self.current_rows()
        if not rows:
            return
        path = filedialog.asksaveasfilename(parent=self, defaultextension=".csv",
                                            initialfile=self.sel.get() + ".csv",
                                            filetypes=[("CSV", "*.csv")])
        if path:
            self.app.write_csv(path, TABLE_COLS, rows)

    def _on_double_click(self, ev):
        if self.tree.identify_region(ev.x, ev.y) != "cell":
            return
        item = self.tree.identify_row(ev.y)
        col = self.tree.identify_column(ev.x)
        if not item or not col:
            return
        ci = int(col[1:]) - 1
        bbox = self.tree.bbox(item, col)
        if not bbox:
            return
        x, y, w, h = bbox
        if self._editor is not None:
            self._editor.destroy()
        e = ttk.Entry(self.tree)
        self._editor = e
        e.place(x=x, y=y, width=w, height=h)
        e.insert(0, self.tree.set(item, self.tree["columns"][ci]))
        e.select_range(0, "end")
        e.focus_set()
        done = {"v": False}

        def finish(commit):
            if done["v"]:
                return
            done["v"] = True
            txt = e.get()
            e.destroy()
            self._editor = None
            if not commit:
                return
            v = parse_float(txt)
            rows = self.current_rows()
            idx = int(item)
            if v is None or rows is None or idx >= len(rows):
                return
            rows[idx][ci] = v
            self.data_changed()

        e.bind("<Return>", lambda _e: finish(True))
        e.bind("<KP_Enter>", lambda _e: finish(True))
        e.bind("<FocusOut>", lambda _e: finish(True))
        e.bind("<Escape>", lambda _e: finish(False))

    # --- sorgu & grafik
    def query(self, silent=False):
        rows = self.current_rows()
        T = parse_float(self.qT.get())
        txt = ""
        p = None
        if rows and T is not None:
            p, oor = fluid_props(rows, T)
            nu = p["mu"] / p["rho"] if p["rho"] else float("nan")
            pr = p["cp"] * p["mu"] / p["k"] if p["k"] else float("nan")
            txt = ("T  = %s °C%s\n"
                   "ρ  = %s kg/m³\n"
                   "cp = %s J/kg·K\n"
                   "μ  = %s Pa·s\n"
                   "k  = %s W/m·K\n"
                   "ν  = %s m²/s\n"
                   "Pr = %s") % (fmt(T), "   (tablo aralığı dışı! uç değer kullanıldı)" if oor else "",
                                 fmt(p["rho"]), fmt(p["cp"]), fmt(p["mu"]), fmt(p["k"]), fmt(nu), fmt(pr))
        elif not silent and T is None:
            txt = "Geçerli bir sıcaklık girin."
        self.q_result.configure(text=txt)
        self.draw(T if p else None, p)

    def draw(self, Tq=None, pq=None):
        self.fig.clear()
        rows = self.current_rows() or []
        arr = np.array(rows, dtype=float) if rows else np.zeros((0, 5))
        for i, (key, label) in enumerate(PROPS):
            ax = self.fig.add_subplot(2, 2, i + 1)
            if len(arr):
                ax.plot(arr[:, 0], arr[:, i + 1], "o-", ms=4, lw=1.5, color="C%d" % i)
            if Tq is not None and pq is not None:
                ax.axvline(Tq, color="r", lw=0.8, ls="--")
                ax.plot([Tq], [pq[key]], "rs", ms=7)
            ax.set_xlabel("T [°C]")
            ax.set_ylabel(label)
            ax.grid(True, alpha=0.3)
        self.fig.suptitle(self.sel.get() or "(akışkan yok)")
        self.fig.tight_layout()
        self.canvas.draw_idle()

    def after_load(self):
        self.refresh_fluids()


# ---------------------------------------------------------------------------
# Sekme 2 - Tek nokta
# ---------------------------------------------------------------------------
class PointTab(BaseTab):
    def __init__(self, master, app):
        super().__init__(master, app)
        top = ttk.Frame(self)
        top.pack(fill="x", padx=6, pady=6)

        b1 = ttk.LabelFrame(top, text="Bölge 1 - Ölçülen akışkan")
        b1.pack(side="left", fill="y", padx=4)
        self.f1 = self.fluid_combo(b1, "fluid1")
        labeled(b1, "Akışkan 1:", self.f1, 0)
        self.flow_unit = self.var("flow_unit", "kg/s")
        labeled(b1, "Debi birimi:", ttk.Combobox(b1, textvariable=self.flow_unit, values=FLOW_UNIT_NAMES,
                                                 state="readonly", width=10), 1)
        self.flow = self.var("flow", "")
        labeled(b1, "Debi (ṁ / V̇):", ttk.Entry(b1, textvariable=self.flow, width=14), 2)
        self.T1 = self.var("T1", "20")
        labeled(b1, "Sıcaklık T1 [°C]:", ttk.Entry(b1, textvariable=self.T1, width=14), 3)
        self.dp = self.var("dp", "")
        labeled(b1, "Basınç düşümü:", ttk.Entry(b1, textvariable=self.dp, width=14), 4)
        self.dp_unit = self.var("dp_unit", "Pa")
        labeled(b1, "dP birimi:", ttk.Combobox(b1, textvariable=self.dp_unit, values=DP_UNIT_NAMES,
                                               state="readonly", width=10), 5)
        self.dh1 = self.var("dh1", "")
        labeled(b1, "Hidrolik çap Dh1 [mm]:", ttk.Entry(b1, textvariable=self.dh1, width=14), 6)

        b2 = ttk.LabelFrame(top, text="Bölge 2 - Hedef akışkan")
        b2.pack(side="left", fill="y", padx=4)
        self.f2 = self.fluid_combo(b2, "fluid2")
        labeled(b2, "Akışkan 2:", self.f2, 0)
        self.T2 = self.var("T2", "")
        labeled(b2, "Sıcaklık T2 [°C]:", ttk.Entry(b2, textvariable=self.T2, width=14), 1)
        ttk.Label(b2, text="(boşsa T1 kullanılır)", foreground="#666").grid(row=2, column=1, sticky="w")
        self.dh2 = self.var("dh2", "")
        labeled(b2, "Hidrolik çap Dh2 [mm]:", ttk.Entry(b2, textvariable=self.dh2, width=14), 3)
        ttk.Label(b2, text="(boşsa Dh1 kullanılır)", foreground="#666").grid(row=4, column=1, sticky="w")
        self.out_unit = self.var("out_unit", "LPM")
        labeled(b2, "Sonuç debi birimi:", ttk.Combobox(b2, textvariable=self.out_unit, values=FLOW_UNIT_NAMES,
                                                       state="readonly", width=10), 5)

        bf = ttk.Frame(top)
        bf.pack(side="left", fill="y", padx=10)
        ttk.Button(bf, text="HESAPLA", command=self.calculate).pack(fill="x", ipady=8, pady=4)
        ttk.Button(bf, text="Sonucu kopyala", command=self.copy_result).pack(fill="x", pady=4)

        rf = ttk.LabelFrame(self, text="Sonuçlar")
        rf.pack(fill="both", expand=True, padx=10, pady=6)
        self.text = tk.Text(rf, font=("Consolas", 10), wrap="none", height=20)
        sb = ttk.Scrollbar(rf, command=self.text.yview)
        self.text.configure(yscrollcommand=sb.set)
        self.text.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.text.configure(state="disabled")

    def set_text(self, s):
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.insert("1.0", s)
        self.text.configure(state="disabled")

    def copy_result(self):
        self.clipboard_clear()
        self.clipboard_append(self.text.get("1.0", "end"))

    def calculate(self, silent=False):
        try:
            self.set_text(self._calculate())
        except Exception as e:  # noqa: BLE001
            if silent:
                return
            self.set_text("HATA: %s" % e)
            messagebox.showerror("Hesap hatası", str(e), parent=self)

    def _calculate(self):
        n1, n2 = self.f1.get(), self.f2.get()
        rows1, rows2 = self.fluid_rows(n1), self.fluid_rows(n2)
        unit = self.flow_unit.get()
        q = self.need_float(self.flow, "Debi", positive=True)
        T1 = self.need_float(self.T1, "Sıcaklık T1")
        dP = self.need_float(self.dp, "Basınç düşümü", positive=True) * DP_UNITS[self.dp_unit.get()]
        Dh1 = self.need_float(self.dh1, "Dh1", positive=True) * 1e-3
        T2 = parse_float(self.T2.get())
        T2_note = ""
        if T2 is None:
            T2, T2_note = T1, "  (T1 alındı)"
        Dh2 = parse_float(self.dh2.get())
        Dh2_note = ""
        if Dh2 is None:
            Dh2, Dh2_note = Dh1, "  (Dh1 alındı)"
        else:
            if Dh2 <= 0:
                raise ValueError("Dh2 sıfırdan büyük olmalı.")
            Dh2 *= 1e-3
        ou = self.out_unit.get()

        p1, oor1 = fluid_props_checked(rows1, T1, n1)
        p2, oor2 = fluid_props_checked(rows2, T2, n2)
        rho1, mu1 = p1["rho"], p1["mu"]
        rho2, mu2 = p2["rho"], p2["mu"]

        A1 = math.pi * Dh1 ** 2 / 4.0
        A2 = math.pi * Dh2 ** 2 / 4.0
        m1 = to_mdot(q, unit, rho1)
        V1 = m1 / rho1
        v1 = V1 / A1
        Re = rho1 * v1 * Dh1 / mu1
        K = dP / (0.5 * rho1 * v1 * v1)

        # Aynı Re (dinamik benzerlik) -> aynı Lc
        v2 = Re * mu2 / (rho2 * Dh2)
        m2 = rho2 * v2 * A2
        V2 = m2 / rho2
        dP2 = K * 0.5 * rho2 * v2 * v2

        # Aynı dP, sabit Lc varsayımı
        v2b = math.sqrt(2.0 * dP / (K * rho2))
        m2b = rho2 * v2b * A2
        V2b = m2b / rho2
        Re2b = rho2 * v2b * Dh2 / mu2

        du = self.dp_unit.get()
        f = lambda x: fmt(x, 6)  # noqa: E731
        L = []
        L.append("=" * 72)
        L.append(" BÖLGE 1 : %s" % n1)
        L.append("=" * 72)
        L.append("  T1            = %s °C%s" % (f(T1), "   [!] tablo aralığı dışı" if oor1 else ""))
        L.append("  ρ1            = %s kg/m³" % f(rho1))
        L.append("  μ1            = %s Pa·s" % f(mu1))
        L.append("  Dh1           = %s mm     A1 = %s m²" % (f(Dh1 * 1e3), f(A1)))
        L.append("  Debi          = %s %s" % (f(q), unit))
        L.append("  ṁ1            = %s kg/s   = %s kg/h" % (f(m1), f(m1 * 3600)))
        L.append("  Vdot1         = %s LPM    = %s m³/h" % (f(V1 * 6e4), f(V1 * 3600)))
        L.append("  v1            = %s m/s" % f(v1))
        L.append("  dP            = %s %s  (= %s Pa)" % (f(dP / DP_UNITS[du]), du, f(dP)))
        L.append("")
        L.append("  Re            = %s" % f(Re))
        L.append("  Lc = dP/(½ρv²)= %s" % f(K))
        L.append("")
        L.append("=" * 72)
        L.append(" BÖLGE 2 : %s" % n2)
        L.append("=" * 72)
        L.append("  T2            = %s °C%s%s" % (f(T2), T2_note, "   [!] tablo aralığı dışı" if oor2 else ""))
        L.append("  ρ2            = %s kg/m³" % f(rho2))
        L.append("  μ2            = %s Pa·s" % f(mu2))
        L.append("  Dh2           = %s mm%s     A2 = %s m²" % (f(Dh2 * 1e3), Dh2_note, f(A2)))
        L.append("")
        L.append(" a) AYNI NOKTA (aynı Re = %s  ->  aynı Lc = %s)   [dinamik benzerlik]" % (f(Re), f(K)))
        L.append("  ṁ2            = %s kg/s   = %s kg/h" % (f(m2), f(m2 * 3600)))
        L.append("  Vdot2         = %s LPM    = %s m³/h" % (f(V2 * 6e4), f(V2 * 3600)))
        L.append("  Debi2         = %s %s" % (f(from_mdot(m2, ou, rho2)), ou))
        L.append("  v2            = %s m/s" % f(v2))
        L.append("  dP2           = %s %s  (= %s Pa)" % (f(dP2 / DP_UNITS[du]), du, f(dP2)))
        L.append("")
        L.append(" b) AYNI dP (= %s %s), sabit Lc = %s varsayımıyla" % (f(dP / DP_UNITS[du]), du, f(K)))
        L.append("  ṁ2            = %s kg/s   = %s kg/h" % (f(m2b), f(m2b * 3600)))
        L.append("  Vdot2         = %s LPM    = %s m³/h" % (f(V2b * 6e4), f(V2b * 3600)))
        L.append("  Debi2         = %s %s" % (f(from_mdot(m2b, ou, rho2)), ou))
        L.append("  v2            = %s m/s" % f(v2b))
        L.append("  Re2           = %s   (Re1'den farklıysa Lc değişebilir; (a) daha doğrudur)" % f(Re2b))
        return "\n".join(L)

    def after_load(self):
        self.calculate(silent=True)


# ---------------------------------------------------------------------------
# Sekme 3 - CSV -> Re-Lc
# ---------------------------------------------------------------------------
DELIMS = {"Otomatik": None, "Virgül ,": ",", "Noktalı virgül ;": ";", "Tab": "\t", "Boşluk": " "}
DECIMALS = {"Otomatik": None, "Nokta .": ".", "Virgül ,": ","}
CONST_T = "(Sabit değer)"
NONE_COL = "(seçiniz)"

RESULT_COLS = ["Re", "Lc", "T [°C]", "Debi (girdi)", "ṁ [kg/s]", "V̇ [LPM]", "dP [Pa]",
               "ρ [kg/m³]", "μ [Pa·s]", "v [m/s]", "Uyarı"]


class CsvTab(BaseTab):
    def __init__(self, master, app):
        super().__init__(master, app)
        self.raw_text = ""
        self.headers = []
        self.rows = []
        self.result = []

        top = ttk.Frame(self)
        top.pack(fill="x", padx=4, pady=4)

        g = ttk.LabelFrame(top, text="Genel")
        g.pack(side="left", fill="y", padx=3)
        self.fluid = self.fluid_combo(g, "fluid")
        labeled(g, "Akışkan:", self.fluid, 0)
        self.dh = self.var("dh", "")
        labeled(g, "Dh [mm] (zorunlu):", ttk.Entry(g, textvariable=self.dh, width=12), 1)
        ttk.Button(g, text="CSV aç...", command=self.open_csv).grid(row=2, column=0, columnspan=2,
                                                                     sticky="ew", padx=4, pady=3)
        self.path = self.var("path", "")
        self.path_lbl = ttk.Label(g, text="", foreground="#555", wraplength=230)
        self.path_lbl.grid(row=3, column=0, columnspan=2, sticky="w", padx=4)

        o = ttk.LabelFrame(top, text="Okuma ayarları")
        o.pack(side="left", fill="y", padx=3)
        self.delim = self.var("delim", "Otomatik")
        labeled(o, "Ayraç:", ttk.Combobox(o, textvariable=self.delim, values=list(DELIMS), state="readonly",
                                          width=16), 0)
        self.decimal = self.var("decimal", "Otomatik")
        labeled(o, "Ondalık:", ttk.Combobox(o, textvariable=self.decimal, values=list(DECIMALS),
                                            state="readonly", width=16), 1)
        self.skip = self.var("skip", "0")
        labeled(o, "Atlanacak satır:", ttk.Spinbox(o, from_=0, to=1000, textvariable=self.skip, width=6), 2)
        self.header = self.var("header", True, tk.BooleanVar)
        ttk.Checkbutton(o, text="İlk satır başlık", variable=self.header).grid(row=3, column=0, columnspan=2,
                                                                                 sticky="w", padx=4)
        ttk.Button(o, text="Yeniden oku", command=self.reparse).grid(row=4, column=0, columnspan=2,
                                                                       sticky="ew", padx=4, pady=3)

        m = ttk.LabelFrame(top, text="Sütun eşleştirme")
        m.pack(side="left", fill="y", padx=3)
        ttk.Label(m, text="Büyüklük").grid(row=0, column=0, padx=4)
        ttk.Label(m, text="Sütun").grid(row=0, column=1, padx=4)
        ttk.Label(m, text="Birim").grid(row=0, column=2, padx=4)
        self.col_T = self.var("col_T", NONE_COL)
        self.col_dp = self.var("col_dp", NONE_COL)
        self.col_q = self.var("col_q", NONE_COL)
        self.unit_T = self.var("unit_T", "°C")
        self.unit_dp = self.var("unit_dp", "Pa")
        self.unit_q = self.var("unit_q", "kg/s")
        self.const_T = self.var("const_T", "20")
        self.cb_T = ttk.Combobox(m, textvariable=self.col_T, state="readonly", width=20)
        self.cb_dp = ttk.Combobox(m, textvariable=self.col_dp, state="readonly", width=20)
        self.cb_q = ttk.Combobox(m, textvariable=self.col_q, state="readonly", width=20)
        for r, (lbl, cb, uv, units) in enumerate([
                ("Sıcaklık", self.cb_T, self.unit_T, TEMP_UNITS),
                ("Basınç düşümü", self.cb_dp, self.unit_dp, DP_UNIT_NAMES),
                ("Debi (ṁ / V̇)", self.cb_q, self.unit_q, FLOW_UNIT_NAMES)], start=1):
            ttk.Label(m, text=lbl + ":").grid(row=r, column=0, sticky="w", padx=4, pady=2)
            cb.grid(row=r, column=1, padx=4, pady=2)
            ttk.Combobox(m, textvariable=uv, values=units, state="readonly", width=8).grid(row=r, column=2,
                                                                                           padx=4, pady=2)
        ttk.Label(m, text="Sabit T [°C]:").grid(row=4, column=0, sticky="w", padx=4)
        ttk.Entry(m, textvariable=self.const_T, width=10).grid(row=4, column=1, sticky="w", padx=4)

        a = ttk.Frame(top)
        a.pack(side="left", fill="y", padx=6)
        ttk.Button(a, text="DÖNÜŞTÜR (Re - Lc)", command=self.convert).pack(fill="x", ipady=6, pady=2)
        ttk.Button(a, text="Sonucu CSV'ye kaydet", command=self.export).pack(fill="x", pady=2)
        ttk.Button(a, text="Eğri olarak kaydet (Sekme 4)", command=self.save_curve).pack(fill="x", pady=2)
        self.logx = self.var("logx", True, tk.BooleanVar)
        self.logy = self.var("logy", True, tk.BooleanVar)
        ttk.Checkbutton(a, text="Re ekseni log", variable=self.logx, command=self.draw).pack(anchor="w")
        ttk.Checkbutton(a, text="Lc ekseni log", variable=self.logy, command=self.draw).pack(anchor="w")
        self.status = ttk.Label(a, text="", foreground="#a33", wraplength=200)
        self.status.pack(fill="x", pady=4)

        pw = ttk.PanedWindow(self, orient="horizontal")
        pw.pack(fill="both", expand=True, padx=4, pady=4)
        tabs = ttk.Frame(pw)
        pw.add(tabs, weight=1)
        nb = ttk.Notebook(tabs)
        nb.pack(fill="both", expand=True)
        f1, self.raw_tree = make_tree(nb, ["-"], height=15)
        f2, self.res_tree = make_tree(nb, RESULT_COLS, height=15, widths=[85] * len(RESULT_COLS))
        nb.add(f1, text="CSV verisi")
        nb.add(f2, text="Re - Lc tablosu")
        self.inner_nb = nb
        pframe, self.fig, self.canvas = make_plot(pw, figsize=(7, 5), constrained=True)
        pw.add(pframe, weight=2)
        set_initial_sash(pw, 0.42)

    # --- CSV okuma
    def open_csv(self):
        path = filedialog.askopenfilename(parent=self, title="Ölçüm CSV dosyası",
                                          filetypes=[("CSV/Metin", "*.csv *.txt *.dat"), ("Tümü", "*.*")])
        if not path:
            return
        try:
            self.raw_text = read_text_file(path)
        except OSError as e:
            messagebox.showerror("CSV", str(e), parent=self)
            return
        self.path.set(path)
        # başlık otomatik algıla
        delim = DELIMS.get(self.delim.get()) or detect_delimiter(self.raw_text, self._skip())
        rows = split_rows(self.raw_text, delim, self._skip())
        dec = DECIMALS.get(self.decimal.get()) or detect_decimal(rows, delim)
        if rows:
            self.header.set(not row_is_numeric(rows[0], dec))
        self.reparse(guess=True)

    def _skip(self):
        try:
            return max(0, int(float(self.skip.get())))
        except ValueError:
            return 0

    def reparse(self, guess=False):
        self.path_lbl.configure(text=os.path.basename(self.path.get()) if self.path.get() else "")
        text = self.raw_text
        if not text:
            set_tree_columns(self.raw_tree, ["-"])
            return
        skip = self._skip()
        delim = DELIMS.get(self.delim.get()) or detect_delimiter(text, skip)
        rows = split_rows(text, delim, skip)
        self._dec = DECIMALS.get(self.decimal.get()) or detect_decimal(rows, delim)
        ncol = max((len(r) for r in rows), default=0)
        if self.header.get() and rows:
            hdr = rows[0] + [""] * (ncol - len(rows[0]))
            rows = rows[1:]
        else:
            hdr = [""] * ncol
        headers = []
        for i, h in enumerate(hdr):
            name = "%d: %s" % (i + 1, h) if h else "Sütun %d" % (i + 1)
            headers.append(name)
        self.headers = headers
        self.rows = [r + [""] * (ncol - len(r)) for r in rows]

        cols = headers if headers else ["-"]
        set_tree_columns(self.raw_tree, cols, [110] * len(cols))
        for r in self.rows[:5000]:
            self.raw_tree.insert("", "end", values=r)

        self.cb_T["values"] = [CONST_T] + headers
        self.cb_dp["values"] = headers
        self.cb_q["values"] = headers
        if guess:
            self._guess_mapping(hdr)
        for v, allowed in ((self.col_T, [CONST_T] + headers), (self.col_dp, headers), (self.col_q, headers)):
            if v.get() not in allowed:
                v.set(NONE_COL)
        self.inner_nb.select(0)
        self.app.schedule_save()

    def _guess_mapping(self, hdr):
        low = [h.lower() for h in hdr]

        def find(keys, exclude=()):
            for i, h in enumerate(low):
                if i in exclude:
                    continue
                if any(k in h for k in keys):
                    return i
            return None

        iT = find(["sıcak", "sicak", "temp", "t [", "t(", "t_", "°c", "degc"])
        if iT is None:
            iT = next((i for i, h in enumerate(low) if h.strip() in ("t", "temp")), None)
        idp = find(["dp", "basınç", "basinc", "pressure", "delta", "Δp", "fark"], exclude={iT})
        iq = find(["mdot", "vdot", "debi", "flow", "lpm", "l/min", "kg/s", "kg/h", "m3/h", "m³/h", "ṁ", "v̇"],
                  exclude={iT, idp})
        if iT is not None:
            self.col_T.set(self.headers[iT])
            h = low[iT]
            self.unit_T.set("K" if "[k]" in h or "(k)" in h or "kelvin" in h else
                            "°F" if "°f" in h or "degf" in h else "°C")
        else:
            self.col_T.set(CONST_T if len(self.headers) == 2 else NONE_COL)
        if idp is not None:
            self.col_dp.set(self.headers[idp])
            h = low[idp]
            for u, keys in (("mbar", ["mbar"]), ("kPa", ["kpa"]), ("bar", ["bar"]), ("psi", ["psi"]),
                            ("Pa", ["pa"])):
                if any(k in h for k in keys):
                    self.unit_dp.set(u)
                    break
        if iq is not None:
            self.col_q.set(self.headers[iq])
            h = low[iq]
            for u, keys in (("LPM", ["lpm", "l/min", "l/dk"]), ("kg/h", ["kg/h"]), ("kg/s", ["kg/s"]),
                            ("g/s", ["g/s"]), ("m³/h", ["m3/h", "m³/h"]), ("m³/s", ["m3/s", "m³/s"])):
                if any(k in h for k in keys):
                    self.unit_q.set(u)
                    break
        # isimden bulunamadıysa sayısal sütunları sırayla ata
        if len(self.headers) >= 2:
            free = [i for i in range(len(self.headers)) if i not in (iT, idp, iq)]
            if self.col_dp.get() == NONE_COL and free:
                self.col_dp.set(self.headers[free.pop(0)])
            if self.col_q.get() == NONE_COL and free:
                self.col_q.set(self.headers[free.pop(0)])

    # --- dönüşüm
    def convert(self, silent=False):
        try:
            self._convert()
            self.status.configure(text=self._status_msg, foreground="#264")
            self.inner_nb.select(1)
        except Exception as e:  # noqa: BLE001
            self.result = []
            self.fill_result()
            self.draw()
            self.status.configure(text="HATA: %s" % e, foreground="#a33")
            if not silent:
                messagebox.showerror("Dönüştürme hatası", str(e), parent=self)

    def _col_index(self, var, label):
        name = var.get()
        if name not in self.headers:
            raise ValueError("'%s' için sütun seçin." % label)
        return self.headers.index(name)

    def _convert(self):
        name = self.fluid.get()
        frows = self.fluid_rows(name)
        Dh = self.need_float(self.dh, "Dh", positive=True) * 1e-3
        if not self.rows:
            raise ValueError("Önce bir CSV dosyası açın.")
        const_T = None
        if self.col_T.get() == CONST_T:
            const_T = self.need_float(self.const_T, "Sabit T")
            iT = None
        else:
            iT = self._col_index(self.col_T, "Sıcaklık")
        idp = self._col_index(self.col_dp, "Basınç düşümü")
        iq = self._col_index(self.col_q, "Debi")
        dec = self._dec
        fdp = DP_UNITS[self.unit_dp.get()]
        uq = self.unit_q.get()
        uT = self.unit_T.get()
        A = math.pi * Dh * Dh / 4.0
        res = []
        skipped = 0
        n_oor = 0
        for r in self.rows:
            T = const_T if iT is None else to_float(r[iT], dec)
            dpv = to_float(r[idp], dec)
            q = to_float(r[iq], dec)
            if T is None or dpv is None or q is None or q <= 0:
                skipped += 1
                continue
            if iT is not None:
                T = temp_to_c(T, uT)
            p, oor = fluid_props_checked(frows, T, name)
            rho, mu = p["rho"], p["mu"]
            dP = dpv * fdp
            m = to_mdot(q, uq, rho)
            V = m / rho
            v = V / A
            Re = rho * v * Dh / mu
            K = dP / (0.5 * rho * v * v)
            n_oor += oor
            res.append([Re, K, T, q, m, V * 6e4, dP, rho, mu, v, "T aralık dışı" if oor else ""])
        if not res:
            raise ValueError("Geçerli satır bulunamadı (sütun/birim/ondalık ayarlarını kontrol edin).")
        self.result = res
        msg = "%d satır dönüştürüldü." % len(res)
        if skipped:
            msg += " %d satır atlandı (boş/geçersiz/debi≤0)." % skipped
        if n_oor:
            msg += " %d satırda T, akışkan tablosu dışında!" % n_oor
        self._status_msg = msg
        self.fill_result()
        self.draw()

    def fill_result(self):
        self.res_tree.delete(*self.res_tree.get_children())
        for r in self.result:
            self.res_tree.insert("", "end", values=[fmt(x) if not isinstance(x, str) else x for x in r])

    def draw(self):
        self.fig.clear()
        if not self.result:
            self.canvas.draw_idle()
            return
        a = np.array([r[:10] for r in self.result], dtype=float)
        Re, K, T, q, dP = a[:, 0], a[:, 1], a[:, 2], a[:, 3], a[:, 6]
        uq = self.unit_q.get()
        udp = self.unit_dp.get()
        varT = np.ptp(T) > 1e-9
        kw = dict(c=T, cmap="viridis", s=18) if varT else dict(color="C0", s=18)
        ax1 = self.fig.add_subplot(2, 2, 1)
        sc = ax1.scatter(Re, K, **kw)
        ax1.set_xlabel("Re")
        ax1.set_ylabel("Lc = dP/(½ρv²)")
        ax1.set_title("Lc - Re")
        if self.logx.get() and np.all(Re > 0):
            ax1.set_xscale("log")
        if self.logy.get() and np.all(K > 0):
            ax1.set_yscale("log")
        no_minor_labels(ax1)
        ax2 = self.fig.add_subplot(2, 2, 2)
        ax2.scatter(q, dP / DP_UNITS[udp], **kw)
        ax2.set_xlabel("Debi [%s]" % uq)
        ax2.set_ylabel("dP [%s]" % udp)
        ax2.set_title("dP - Debi")
        ax3 = self.fig.add_subplot(2, 2, 3)
        ax3.scatter(q, Re, **kw)
        ax3.set_xlabel("Debi [%s]" % uq)
        ax3.set_ylabel("Re")
        ax3.set_title("Re - Debi")
        ax4 = self.fig.add_subplot(2, 2, 4)
        ax4.scatter(q, K, **kw)
        ax4.set_xlabel("Debi [%s]" % uq)
        ax4.set_ylabel("Lc")
        ax4.set_title("Lc - Debi")
        for ax in (ax1, ax2, ax3, ax4):
            ax.grid(True, which="both", alpha=0.3)
        if varT:
            self.fig.colorbar(sc, ax=[ax1, ax2, ax3, ax4], label="T [°C]", shrink=0.8)
        self.canvas.draw_idle()

    def export(self):
        if not self.result:
            messagebox.showinfo("Kaydet", "Önce dönüştürme yapın.", parent=self)
            return
        base = os.path.splitext(os.path.basename(self.path.get() or "sonuc"))[0]
        path = filedialog.asksaveasfilename(parent=self, defaultextension=".csv",
                                            initialfile=base + "_Re_Lc.csv", filetypes=[("CSV", "*.csv")])
        if path:
            cols = list(RESULT_COLS)
            cols[3] = "Debi [%s]" % self.unit_q.get()
            self.app.write_csv(path, cols, self.result)

    def save_curve(self):
        if not self.result:
            messagebox.showinfo("Eğri", "Önce dönüştürme yapın.", parent=self)
            return
        base = os.path.splitext(os.path.basename(self.path.get() or "egri"))[0]
        name = simpledialog.askstring("Eğri olarak kaydet", "Eğri adı:", initialvalue=base, parent=self)
        if not name:
            return
        name = name.strip()
        if name in self.app.data["curves"] and not messagebox.askyesno(
                "Eğri", "'%s' zaten var. Üzerine yazılsın mı?" % name, parent=self):
            return
        pts = sorted((r[0], r[1]) for r in self.result)
        self.app.data["curves"][name] = {
            "re": [p[0] for p in pts], "k": [p[1] for p in pts],
            "info": "Kaynak: %s | akışkan: %s | Dh: %s mm" % (os.path.basename(self.path.get()),
                                                             self.fluid.get(), self.dh.get()),
        }
        self.app.curves_changed(select=name)
        messagebox.showinfo("Eğri", "'%s' eğrisi kaydedildi. Sekme 4'te kullanabilirsiniz." % name, parent=self)

    # --- durum
    def get_state(self):
        st = super().get_state()
        st["raw_text"] = self.raw_text if len(self.raw_text) < 5_000_000 else ""
        return st

    def set_state(self, st):
        super().set_state(st)
        self.raw_text = st.get("raw_text", "") or ""
        if not self.raw_text and self.path.get() and os.path.isfile(self.path.get()):
            try:
                self.raw_text = read_text_file(self.path.get())
            except OSError:
                pass

    def after_load(self):
        if self.raw_text:
            # eşleştirme bilgilerini kaybetmemek için önce kaydedilenleri koru
            saved = (self.col_T.get(), self.col_dp.get(), self.col_q.get())
            self.reparse()
            for v, s, allowed in zip((self.col_T, self.col_dp, self.col_q), saved,
                                     ([CONST_T] + self.headers, self.headers, self.headers)):
                if s in allowed:
                    v.set(s)
            self.convert(silent=True)


# ---------------------------------------------------------------------------
# Sekme 4 - dP vs debi
# ---------------------------------------------------------------------------
class CurveTab(BaseTab):
    def __init__(self, master, app):
        super().__init__(master, app)
        self.out = None

        top = ttk.Frame(self)
        top.pack(fill="x", padx=4, pady=4)

        c = ttk.LabelFrame(top, text="Re - Lc eğrisi")
        c.pack(side="left", fill="y", padx=3)
        self.curve = self.var("curve", "")
        self.cb_curve = ttk.Combobox(c, textvariable=self.curve, state="readonly", width=24)
        labeled(c, "Eğri:", self.cb_curve, 0)
        self.cb_curve.bind("<<ComboboxSelected>>", lambda e: self.show_curve_info())
        bf = ttk.Frame(c)
        bf.grid(row=1, column=0, columnspan=2, sticky="ew")
        ttk.Button(bf, text="CSV'den yükle", command=self.load_curve_csv).pack(side="left", padx=2, pady=2)
        ttk.Button(bf, text="Sil", command=self.delete_curve).pack(side="left", padx=2, pady=2)
        self.model = self.var("model", MODEL_KINDS["interp"])
        labeled(c, "Model:", ttk.Combobox(c, textvariable=self.model, values=list(MODEL_KINDS.values()),
                                          state="readonly", width=24), 2)
        self.extrap = self.var("extrap", EXTRAP_KINDS["clamp"])
        labeled(c, "Aralık dışı:", ttk.Combobox(c, textvariable=self.extrap, values=list(EXTRAP_KINDS.values()),
                                                state="readonly", width=24), 3)
        self.info = ttk.Label(c, text="", foreground="#555", wraplength=300)
        self.info.grid(row=4, column=0, columnspan=2, sticky="w", padx=4)

        g = ttk.LabelFrame(top, text="Akışkan & geometri")
        g.pack(side="left", fill="y", padx=3)
        self.fluid = self.fluid_combo(g, "fluid")
        labeled(g, "Akışkan:", self.fluid, 0)
        self.dh = self.var("dh", "")
        labeled(g, "Dh [mm]:", ttk.Entry(g, textvariable=self.dh, width=12), 1)
        self.tmin = self.var("tmin", "20")
        self.tmax = self.var("tmax", "80")
        self.tstep = self.var("tstep", "20")
        labeled(g, "T min [°C]:", ttk.Entry(g, textvariable=self.tmin, width=12), 2)
        labeled(g, "T max [°C]:", ttk.Entry(g, textvariable=self.tmax, width=12), 3)
        labeled(g, "T adım [°C]:", ttk.Entry(g, textvariable=self.tstep, width=12), 4)

        q = ttk.LabelFrame(top, text="Debi aralığı")
        q.pack(side="left", fill="y", padx=3)
        self.qunit = self.var("qunit", "LPM")
        labeled(q, "Debi birimi:", ttk.Combobox(q, textvariable=self.qunit, values=FLOW_UNIT_NAMES,
                                                state="readonly", width=10), 0)
        self.qmin = self.var("qmin", "1")
        self.qmax = self.var("qmax", "20")
        self.qn = self.var("qn", "40")
        labeled(q, "Debi min:", ttk.Entry(q, textvariable=self.qmin, width=12), 1)
        labeled(q, "Debi max:", ttk.Entry(q, textvariable=self.qmax, width=12), 2)
        labeled(q, "Nokta sayısı:", ttk.Entry(q, textvariable=self.qn, width=12), 3)
        self.dpunit = self.var("dpunit", "kPa")
        labeled(q, "dP birimi:", ttk.Combobox(q, textvariable=self.dpunit, values=DP_UNIT_NAMES,
                                              state="readonly", width=10), 4)

        a = ttk.Frame(top)
        a.pack(side="left", fill="y", padx=6)
        ttk.Button(a, text="HESAPLA", command=self.calculate).pack(fill="x", ipady=6, pady=2)
        ttk.Button(a, text="Tabloyu CSV'ye kaydet", command=self.export).pack(fill="x", pady=2)
        self.status = ttk.Label(a, text="", foreground="#a33", wraplength=220)
        self.status.pack(fill="x", pady=4)

        pw = ttk.PanedWindow(self, orient="horizontal")
        pw.pack(fill="both", expand=True, padx=4, pady=4)
        tf, self.tree = make_tree(pw, ["-"], height=15)
        pw.add(tf, weight=1)
        pframe, self.fig, self.canvas = make_plot(pw, figsize=(8, 5))
        pw.add(pframe, weight=2)
        set_initial_sash(pw, 0.33)

    # --- eğri yönetimi
    def refresh_curves(self, select=None):
        names = sorted(self.app.data["curves"].keys(), key=str.lower)
        self.cb_curve["values"] = names
        if select:
            self.curve.set(select)
        if self.curve.get() not in names:
            self.curve.set(names[0] if names else "")
        self.show_curve_info()

    def show_curve_info(self):
        c = self.app.data["curves"].get(self.curve.get())
        if c:
            self.info.configure(text="%d nokta, Re: %s ... %s\n%s" % (
                len(c["re"]), fmt(min(c["re"]), 4), fmt(max(c["re"]), 4), c.get("info", "")))
        else:
            self.info.configure(text="Eğri yok. Sekme 3'ten kaydedin veya CSV'den yükleyin.")

    def load_curve_csv(self):
        path = filedialog.askopenfilename(parent=self, title="Re - Lc CSV",
                                          filetypes=[("CSV/Metin", "*.csv *.txt *.dat"), ("Tümü", "*.*")])
        if not path:
            return
        text = read_text_file(path)
        delim = detect_delimiter(text)
        rows = split_rows(text, delim)
        dec = detect_decimal(rows, delim)
        iRe, iK = 0, 1
        if rows and not row_is_numeric(rows[0], dec):
            low = [h.strip().lower() for h in rows[0]]
            for i, h in enumerate(low):
                if h.startswith("re"):
                    iRe = i
                    break
            for i, h in enumerate(low):
                if i != iRe and (h.startswith("lc") or h in ("k", "zeta", "ζ", "kayıp", "loss") or "loss" in h
                                 or "kayıp" in h):
                    iK = i
                    break
            else:
                iK = 1 if iRe != 1 else 0
            rows = rows[1:]
        pts = []
        for r in rows:
            if len(r) > max(iRe, iK):
                a, b = to_float(r[iRe], dec), to_float(r[iK], dec)
                if a is not None and b is not None:
                    pts.append((a, b))
        if len(pts) < 2:
            messagebox.showerror("Eğri", "Dosyada en az 2 sayısal (Re, Lc) satırı bulunamadı.", parent=self)
            return
        base = os.path.splitext(os.path.basename(path))[0]
        name = simpledialog.askstring("Eğri adı", "Eğri adı:", initialvalue=base, parent=self)
        if not name:
            return
        pts.sort()
        self.app.data["curves"][name.strip()] = {"re": [p[0] for p in pts], "k": [p[1] for p in pts],
                                                 "info": "Kaynak: %s" % os.path.basename(path)}
        self.app.curves_changed(select=name.strip())

    def delete_curve(self):
        n = self.curve.get()
        if n and messagebox.askyesno("Sil", "'%s' eğrisi silinsin mi?" % n, parent=self):
            self.app.data["curves"].pop(n, None)
            self.curve.set("")
            self.app.curves_changed()

    # --- hesap
    def calculate(self, silent=False):
        try:
            self._calculate()
            self.status.configure(text=self._status_msg, foreground="#264")
        except Exception as e:  # noqa: BLE001
            self.out = None
            set_tree_columns(self.tree, ["-"])
            self.fig.clear()
            self.canvas.draw_idle()
            self.status.configure(text="HATA: %s" % e, foreground="#a33")
            if not silent:
                messagebox.showerror("Hesap hatası", str(e), parent=self)

    def _calculate(self):
        c = self.app.data["curves"].get(self.curve.get())
        if not c:
            raise ValueError("Re - Lc eğrisi seçin.")
        kind = {v: k for k, v in MODEL_KINDS.items()}.get(self.model.get(), "interp")
        extrap = {v: k for k, v in EXTRAP_KINDS.items()}.get(self.extrap.get(), "clamp")
        model, re_min, re_max, desc = build_model(c["re"], c["k"], kind, extrap)
        name = self.fluid.get()
        frows = self.fluid_rows(name)
        Dh = self.need_float(self.dh, "Dh", positive=True) * 1e-3
        tmin = self.need_float(self.tmin, "T min")
        tmax = self.need_float(self.tmax, "T max")
        tstep = parse_float(self.tstep.get())
        if tmax < tmin:
            tmin, tmax = tmax, tmin
        if tmax == tmin:
            Ts = [tmin]
        else:
            if tstep is None or tstep <= 0:
                raise ValueError("T adım sıfırdan büyük olmalı.")
            n = int(math.floor((tmax - tmin) / tstep + 1e-9)) + 1
            if n > 100:
                raise ValueError("Çok fazla sıcaklık adımı (%d). Adımı büyütün." % n)
            Ts = [tmin + i * tstep for i in range(n)]
            if Ts[-1] < tmax - 1e-9:
                Ts.append(tmax)
        qmin = self.need_float(self.qmin, "Debi min")
        qmax = self.need_float(self.qmax, "Debi max", positive=True)
        try:
            qn = int(float(self.qn.get()))
        except ValueError:
            raise ValueError("Nokta sayısı tam sayı olmalı.")
        qn = max(2, min(qn, 5000))
        if qmin < 0 or qmin >= qmax:
            raise ValueError("Debi aralığı geçersiz (0 ≤ min < max).")
        qs = np.linspace(qmin, qmax, qn)
        uq = self.qunit.get()
        udp = self.dpunit.get()
        A = math.pi * Dh * Dh / 4.0

        series = []
        n_out = 0
        any_oor = False
        for T in Ts:
            p, oor = fluid_props_checked(frows, T, name)
            any_oor |= oor
            rho, mu = p["rho"], p["mu"]
            m = np.array([to_mdot(x, uq, rho) for x in qs])
            v = m / rho / A
            Re = rho * v * Dh / mu
            with np.errstate(divide="ignore", invalid="ignore"):
                K = np.where(Re > 0, model(np.where(Re > 0, Re, 1.0)), 0.0)
            dP = K * 0.5 * rho * v * v
            outside = (Re < re_min * (1 - 1e-9)) | (Re > re_max * (1 + 1e-9))
            n_out += int(outside.sum())
            series.append(dict(T=T, Re=Re, K=K, dP=dP, out=outside, rho=rho, mu=mu))
        self.out = dict(qs=qs, series=series, uq=uq, udp=udp, curve=c, model=model,
                        re_min=re_min, re_max=re_max, desc=desc)
        msg = desc
        if n_out:
            msg += "\n[!] %d nokta eğrinin Re aralığı dışında (kesikli çizgi)." % n_out
        if any_oor:
            msg += "\n[!] Bazı sıcaklıklar akışkan tablosu dışında."
        self._status_msg = msg
        self.fill_table()
        self.draw()

    def _table(self):
        o = self.out
        cols = ["Debi [%s]" % o["uq"]]
        for s in o["series"]:
            cols.append("dP@%s°C [%s]" % (fmt(s["T"], 5), o["udp"]))
        for s in o["series"]:
            cols.append("Re@%s°C" % fmt(s["T"], 5))
        f = DP_UNITS[o["udp"]]
        rows = []
        for i, qv in enumerate(o["qs"]):
            r = [float(qv)]
            r += [float(s["dP"][i] / f) for s in o["series"]]
            r += [float(s["Re"][i]) for s in o["series"]]
            rows.append(r)
        return cols, rows

    def fill_table(self):
        cols, rows = self._table()
        set_tree_columns(self.tree, cols, [95] * len(cols))
        for r in rows:
            self.tree.insert("", "end", values=[fmt(x) for x in r])

    def draw(self):
        self.fig.clear()
        o = self.out
        if not o:
            self.canvas.draw_idle()
            return
        gs = self.fig.add_gridspec(1, 3)
        ax = self.fig.add_subplot(gs[0, :2])
        f = DP_UNITS[o["udp"]]
        n = len(o["series"])
        cmap = matplotlib.colormaps["coolwarm"] if hasattr(matplotlib, "colormaps") \
            else matplotlib.cm.get_cmap("coolwarm")
        for j, s in enumerate(o["series"]):
            col = cmap(j / max(1, n - 1)) if n > 1 else "C0"
            y = s["dP"] / f
            inside = np.where(s["out"], np.nan, y)
            ax.plot(o["qs"], y, "--", color=col, lw=1, alpha=0.6)
            ax.plot(o["qs"], inside, "-", color=col, lw=2, label="%s °C" % fmt(s["T"], 5))
        ax.set_xlabel("Debi [%s]" % o["uq"])
        ax.set_ylabel("dP [%s]" % o["udp"])
        ax.set_title("dP - Debi  (%s, Dh = %s mm)" % (self.fluid.get(), self.dh.get()))
        ax.grid(True, alpha=0.3)
        ax.legend(fontsize=8, ncol=2 if n > 8 else 1)

        ax2 = self.fig.add_subplot(gs[0, 2])
        c = o["curve"]
        re_a = np.array(c["re"], dtype=float)
        k_a = np.array(c["k"], dtype=float)
        ax2.plot(re_a, k_a, "o", ms=3, color="0.4", label="veri")
        allre = np.concatenate([s["Re"] for s in o["series"]] + [re_a])
        allre = allre[allre > 0]
        lo, hi = allre.min(), allre.max()
        rr = np.logspace(math.log10(lo), math.log10(hi), 200)
        ax2.plot(rr, o["model"](rr), "-", color="C3", lw=1.5, label="model")
        ax2.axvspan(o["re_min"], o["re_max"], color="C2", alpha=0.08, label="veri aralığı")
        ax2.set_xscale("log")
        if np.all(k_a > 0):
            ax2.set_yscale("log")
        no_minor_labels(ax2)
        ax2.set_xlabel("Re")
        ax2.set_ylabel("Lc")
        ax2.set_title("Re - Lc eğrisi")
        ax2.grid(True, which="both", alpha=0.3)
        ax2.legend(fontsize=8)
        self.fig.tight_layout()
        self.canvas.draw_idle()

    def export(self):
        if not self.out:
            messagebox.showinfo("Kaydet", "Önce hesaplama yapın.", parent=self)
            return
        path = filedialog.asksaveasfilename(parent=self, defaultextension=".csv",
                                            initialfile="dP_debi_%s.csv" % (self.fluid.get() or "akiskan"),
                                            filetypes=[("CSV", "*.csv")])
        if path:
            cols, rows = self._table()
            self.app.write_csv(path, cols, rows)

    def after_load(self):
        self.refresh_curves()
        self.calculate(silent=True)


# ---------------------------------------------------------------------------
# Uygulama
# ---------------------------------------------------------------------------
def default_data_path():
    here = os.path.dirname(os.path.abspath(sys.argv[0] if sys.argv and sys.argv[0] else __file__))
    if os.access(here, os.W_OK):
        return os.path.join(here, DATA_FILENAME)
    return os.path.join(os.path.expanduser("~"), DATA_FILENAME)


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1400x880")
        self.minsize(1000, 650)
        self.report_callback_exception = self._report_exc
        self.data_path = default_data_path()
        self._save_job = None
        self.loading = True
        self.data = {"version": DATA_VERSION, "fluids": {}, "curves": {}, "settings": {}, "tabs": {}}

        style = ttk.Style(self)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        elif "clam" in style.theme_names():
            style.theme_use("clam")

        self.export_fmt = tk.StringVar(value="comma")
        self.export_fmt.trace_add("write", lambda *a: self.schedule_save())
        self._build_menu()

        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True)
        self.tabs = {
            "fluids": FluidsTab(self.nb, self),
            "point": PointTab(self.nb, self),
            "csv": CsvTab(self.nb, self),
            "curve": CurveTab(self.nb, self),
        }
        for key, title in (("fluids", " 1 - Akışkanlar "), ("point", " 2 - Tek Nokta Hesabı "),
                           ("csv", " 3 - CSV → Re-Lc "), ("curve", " 4 - dP vs Debi ")):
            self.nb.add(self.tabs[key], text=title)
        self.nb.bind("<<NotebookTabChanged>>", lambda e: self.schedule_save())

        self.statusbar = ttk.Label(self, text="", anchor="w", relief="sunken")
        self.statusbar.pack(fill="x", side="bottom")

        self.load()
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    # --- menü
    def _build_menu(self):
        mb = tk.Menu(self)
        fm = tk.Menu(mb, tearoff=0)
        fm.add_command(label="Veri dosyasının konumunu göster", command=self.show_data_path)
        fm.add_command(label="Yedek al (JSON dışa aktar)...", command=self.export_json)
        fm.add_command(label="JSON'dan yükle...", command=self.import_json)
        fm.add_separator()
        fm.add_command(label="Çıkış", command=self.on_close)
        mb.add_cascade(label="Dosya", menu=fm)
        sm = tk.Menu(mb, tearoff=0)
        for key, (label, _d, _dec) in EXPORT_FORMATS.items():
            sm.add_radiobutton(label="CSV dışa aktarım: " + label, variable=self.export_fmt, value=key)
        mb.add_cascade(label="Ayarlar", menu=sm)
        hm = tk.Menu(mb, tearoff=0)
        hm.add_command(label="Hakkında", command=self.about)
        mb.add_cascade(label="Yardım", menu=hm)
        self.config(menu=mb)

    def about(self):
        messagebox.showinfo("Hakkında", APP_TITLE + "\n\n"
                            "Re = ρ·v·Dh/μ,   v = ṁ/(ρ·A),   A = π·Dh²/4\n"
                            "Lc = dP / (½·ρ·v²)\n\n"
                            "Akışkan-2 eşdeğer noktası: aynı Re (dinamik benzerlik) → aynı Lc.\n\n"
                            "Veri dosyası:\n" + self.data_path, parent=self)

    def show_data_path(self):
        messagebox.showinfo("Veri dosyası", self.data_path, parent=self)

    # --- ortak
    def fluid_names(self):
        return sorted(self.data["fluids"].keys(), key=str.lower)

    def fluids_changed(self):
        for t in self.tabs.values():
            t.refresh_fluids()
        self.schedule_save()

    def rename_fluid(self, old, new):
        for t in self.tabs.values():
            t.rename_fluid(old, new)

    def curves_changed(self, select=None):
        self.tabs["curve"].refresh_curves(select)
        self.schedule_save()

    def write_csv(self, path, headers, rows):
        _label, delim, dec = EXPORT_FORMATS.get(self.export_fmt.get(), EXPORT_FORMATS["comma"])

        def cell(x):
            if isinstance(x, str):
                return x
            s = repr(float(x)) if isinstance(x, (float, np.floating)) else str(x)
            if s.endswith(".0"):
                s = s[:-2]
            return s.replace(".", dec) if dec != "." else s

        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                w = csv.writer(f, delimiter=delim)
                w.writerow(headers)
                for r in rows:
                    w.writerow([cell(x) for x in r])
        except OSError as e:
            messagebox.showerror("Kaydetme hatası", str(e), parent=self)
            return
        self.set_status("Kaydedildi: %s" % path)

    def set_status(self, s):
        self.statusbar.configure(text=s)

    def _report_exc(self, exc, val, tb):
        msg = "".join(traceback.format_exception(exc, val, tb))
        messagebox.showerror("Beklenmeyen hata", msg[-3000:], parent=self)

    # --- kayıt / yükleme
    def schedule_save(self):
        if self.loading:
            return
        if self._save_job is not None:
            self.after_cancel(self._save_job)
        self._save_job = self.after(600, self.save)

    def collect(self):
        d = self.data
        d["version"] = DATA_VERSION
        d["settings"] = {"export_fmt": self.export_fmt.get(), "geometry": self.geometry(),
                         "active_tab": self.nb.index("current") if self.nb.tabs() else 0}
        d["tabs"] = {k: t.get_state() for k, t in self.tabs.items()}
        return d

    def save(self):
        self._save_job = None
        try:
            d = self.collect()
            tmp = self.data_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(d, f, ensure_ascii=False, indent=1)
            os.replace(tmp, self.data_path)
            self.set_status("Otomatik kaydedildi  -  %s" % self.data_path)
        except Exception as e:  # noqa: BLE001
            self.set_status("KAYIT HATASI: %s" % e)

    def apply_data(self, d):
        self.loading = True
        try:
            fluids = {}
            for name, rows in (d.get("fluids") or {}).items():
                clean = []
                for r in rows or []:
                    try:
                        vals = [float(x) for x in r[:5]]
                    except (TypeError, ValueError):
                        continue
                    if len(vals) == 5:
                        clean.append(vals)
                fluids[str(name)] = sorted(clean, key=lambda r: r[0])
            curves = {}
            for name, c in (d.get("curves") or {}).items():
                try:
                    curves[str(name)] = {"re": [float(x) for x in c["re"]], "k": [float(x) for x in c["k"]],
                                         "info": str(c.get("info", ""))}
                except (KeyError, TypeError, ValueError):
                    continue
            self.data["fluids"] = fluids
            self.data["curves"] = curves
            st = d.get("settings") or {}
            if st.get("export_fmt") in EXPORT_FORMATS:
                self.export_fmt.set(st["export_fmt"])
            if st.get("geometry"):
                try:
                    self.geometry(st["geometry"])
                except tk.TclError:
                    pass
            tabs = d.get("tabs") or {}
            for k, t in self.tabs.items():
                t.set_state(tabs.get(k) or {})
            for t in self.tabs.values():
                t.refresh_fluids()
            self.tabs["curve"].refresh_curves()
            for t in self.tabs.values():
                try:
                    t.after_load()
                except Exception:  # noqa: BLE001
                    pass
            try:
                self.nb.select(int(st.get("active_tab", 0)))
            except (tk.TclError, ValueError, TypeError):
                pass
        finally:
            self.loading = False

    def load(self):
        d = None
        if os.path.isfile(self.data_path):
            try:
                with open(self.data_path, "r", encoding="utf-8") as f:
                    d = json.load(f)
            except Exception as e:  # noqa: BLE001
                bad = self.data_path + ".bozuk"
                try:
                    os.replace(self.data_path, bad)
                except OSError:
                    pass
                messagebox.showwarning("Veri dosyası", "Veri dosyası okunamadı (%s).\n"
                                       "Dosya '%s' olarak yedeklendi, varsayılanlarla başlanıyor." % (e, bad))
        if d is None:
            d = {"fluids": DEFAULT_FLUIDS}
        self.apply_data(d)
        self.set_status("Veri dosyası: %s" % self.data_path)
        self.save()

    def export_json(self):
        path = filedialog.asksaveasfilename(parent=self, defaultextension=".json", initialfile="yedek.json",
                                            filetypes=[("JSON", "*.json")])
        if path:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self.collect(), f, ensure_ascii=False, indent=1)
            self.set_status("Yedek alındı: %s" % path)

    def import_json(self):
        path = filedialog.askopenfilename(parent=self, filetypes=[("JSON", "*.json"), ("Tümü", "*.*")])
        if not path:
            return
        if not messagebox.askyesno("JSON'dan yükle", "Mevcut tüm veriler bu dosyadakilerle değiştirilecek. "
                                   "Devam edilsin mi?", parent=self):
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                d = json.load(f)
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("JSON", str(e), parent=self)
            return
        self.apply_data(d)
        self.save()

    def on_close(self):
        if self._save_job is not None:
            self.after_cancel(self._save_job)
        self.save()
        self.destroy()


def main():
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:  # noqa: BLE001
            pass
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
