"""RS-232 scale engine: frame parser, stability lock, background reader, simulator."""
import re, threading, time, collections

try:
    import serial
    from serial.tools import list_ports
except ImportError:  # app still runs in simulator mode
    serial = None
    list_ports = None

_GENERIC = (r"(?:(?P<st>ST|US),?\s*)?(?:(?:GS|NT),?\s*)?"
            r"(?P<w>[+-]?\s*\d+(?:\.\d+)?)\s*(?P<u>kg|g|lb)?")

# Presets. Always verify against the real indicator in the Technician screen.
PROFILES = {
    "Generic (ST,GS,+001.250kg)": {"regex": _GENERIC, "reverse": False},
    "Plain number (1.250 kg)":    {"regex": r"(?P<w>\d+(?:\.\d+)?)\s*(?P<u>kg|g)?", "reverse": False},
    "Yaohua reversed '=' frame":  {"regex": r"=(?P<w>\d+(?:\.\d+)?)", "reverse": True},
    "Custom (regex below)":       {"regex": None, "reverse": False},
}


def parse_frame(text, regex, reverse=False):
    """Return (weight_kg, stable) or None. stable is None when the frame has no flag."""
    try:
        m = re.search(regex, text.strip(), re.I)
    except re.error:
        return None
    if not m or not m.groupdict().get("w"):
        return None
    w = m.group("w").replace(" ", "")
    if reverse:
        w = w[::-1]
    try:
        val = float(w)
    except ValueError:
        return None
    unit = (m.groupdict().get("u") or "kg").lower()
    if unit == "g":
        val /= 1000.0
    elif unit == "lb":
        val *= 0.45359237
    st = m.groupdict().get("st")
    return round(val, 3), (None if not st else st.upper() == "ST")


def list_serial_ports():
    if not list_ports:
        return []
    return [(p.device, p.description) for p in list_ports.comports()]


def autodetect_port():
    for dev, desc in list_serial_ports():
        if any(k in desc.upper() for k in ("USB", "CH340", "FTDI", "SERIAL", "PROLIFIC")):
            return dev
    ports = list_serial_ports()
    return ports[0][0] if ports else None


class ScaleReader:
    def __init__(self):
        self.lock = threading.Lock()
        self.weight, self.stable = 0.0, False
        self.captured = None
        self.connected, self.error = False, ""
        self.raw = collections.deque(maxlen=200)
        self.hist = collections.deque(maxlen=6)
        self.last_frame = 0.0
        self.regex, self.reverse, self.mode = PROFILES["Generic (ST,GS,+001.250kg)"]["regex"], False, "continuous"
        self.sim_weight = 0.0
        self._stop = threading.Event()
        self._thread = None

    def configure(self, profile, custom_regex="", reverse=False, mode="continuous"):
        p = PROFILES.get(profile, PROFILES["Generic (ST,GS,+001.250kg)"])
        self.regex = p["regex"] or custom_regex or _GENERIC
        self.reverse = reverse if p["regex"] is None else p["reverse"]
        self.mode = mode

    def feed(self, line):
        self.raw.append(line)
        self.last_frame = time.time()
        r = parse_frame(line, self.regex, self.reverse)
        if not r:
            return
        w, st = r
        with self.lock:
            self.hist.append(w)
            if st is None:  # no flag from scale: rolling-variance stability
                st = len(self.hist) == self.hist.maxlen and max(self.hist) - min(self.hist) <= 0.002
            self.weight, self.stable = w, st
            if self.mode == "button":
                self.captured, self.stable = w, True

    def status(self):
        if not self.connected:
            return "disconnected"
        if self.mode == "continuous" and time.time() - self.last_frame > 3:
            return "no_data"
        return "stable" if self.stable else "unstable"

    def take(self):
        """Weight to bill (kg) or None if not ready."""
        with self.lock:
            if self.mode == "button":
                w, self.captured = self.captured, None
                return w if w and w > 0 else None
            return self.weight if self.stable and self.weight > 0 else None

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1.5)
        self._thread, self.connected = None, False

    def start(self, port, baud=9600):
        self.stop(); self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, args=(port, int(baud)), daemon=True)
        self._thread.start()

    def start_sim(self):
        self.stop(); self._stop = threading.Event()
        self._thread = threading.Thread(target=self._sim, daemon=True)
        self._thread.start()

    def _sim(self):
        import random
        self.connected = True
        while not self._stop.is_set():
            w = self.sim_weight
            if w and time.time() < getattr(self, "sim_settle", 0):  # item just placed: wobble, then settle
                self.feed(f"US,GS,{w + random.uniform(-0.06, 0.06):+08.3f}kg")
            else:
                self.feed(f"ST,GS,{w:+08.3f}kg")
            time.sleep(0.15)

    def _run(self, port, baud):
        if not serial:
            self.error = "pyserial not installed"; return
        buf = b""
        while not self._stop.is_set():
            try:
                with serial.Serial(port, baud, bytesize=8, parity="N", stopbits=1, timeout=0.2) as ser:
                    self.connected, self.error = True, ""
                    while not self._stop.is_set():
                        buf += ser.read(64)
                        parts = re.split(rb"[\r\n]+", buf)
                        buf = parts.pop()
                        if len(buf) > 256:
                            buf = b""
                        for p in parts:
                            if p.strip():
                                self.feed(p.decode("ascii", "ignore"))
            except Exception as e:  # cable unplugged -> retry
                self.connected, self.error = False, str(e)
                self._stop.wait(2)
        self.connected = False
