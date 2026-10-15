#!/usr/bin/python3
"""bthidd — BT HID Relay daemon (root, systemd).

PC keyboard/mouse → phone over Bluetooth Classic HID. The PC acts as an HID
device; the phone is the HID host. A tray app (bthid_tray.py) controls this
daemon through a Unix socket (newline-delimited JSON, one request per connection).

Threads: GLib main loop (all D-Bus + control socket), input loop (evdev),
L2CAP accept loop, phone reconnect loop.
"""
import json, os, selectors, shutil, socket, struct, subprocess, sys, threading, time

APP_DIR      = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH  = os.environ.get("BTHID_CONFIG", "/etc/bthid/config.json")
SOCK_PATH    = os.environ.get("BTHID_SOCK", "/run/bthid/ctl.sock")
PAIR_DROPIN  = "/run/systemd/system/bluetooth.service.d/zz-bthid-pair.conf"
BLUETOOTHD_CANDIDATES = ["/usr/libexec/bluetooth/bluetoothd", "/usr/lib/bluetooth/bluetoothd",
                         "/usr/sbin/bluetoothd"]   # Ubuntu/Debian, Arch/Fedora, others
RFKILL_DIR   = "/sys/class/rfkill"
HID_UUID     = "00001124-0000-1000-8000-00805f9b34fb"
PSM_CTRL, PSM_INTR = 0x11, 0x13
PAIR_WINDOW  = 120          # seconds the PC stays discoverable
PAIR_SETTLE  = 8            # seconds after bonding before restoring normal bluetoothd
SEND_TIMEOUT = 1.0          # intr send timeout; on expiry the relay turns OFF (input never stays grabbed)

SOL_BLUETOOTH, BT_SECURITY = 274, 4

def find_bluetoothd():
    return next((p for p in BLUETOOTHD_CANDIDATES if os.access(p, os.X_OK)), BLUETOOTHD_CANDIDATES[0])

def rfkill_unblock_bluetooth(base=None):
    """Soft-unblock Bluetooth via sysfs (no rfkill binary needed). Returns how many were unblocked."""
    import glob
    n = 0
    for d in glob.glob(os.path.join(base or RFKILL_DIR, "rfkill*")):
        try:
            if open(os.path.join(d, "type")).read().strip() == "bluetooth" and \
               open(os.path.join(d, "soft")).read().strip() == "1":
                with open(os.path.join(d, "soft"), "w") as f: f.write("0")
                n += 1
        except OSError:
            pass
    return n

def log(*a):
    print("[bthidd]", *a, flush=True)

# ─── Config ───────────────────────────────────────────────────────────────────
DEFAULT_CONFIG = {"user": None, "target": None, "devices": [], "hotkey": [97, 100]}

def load_config(path=None):
    path = path or CONFIG_PATH
    cfg = dict(DEFAULT_CONFIG, devices=[])
    try:
        with open(path) as f:
            cfg.update(json.load(f))
    except (OSError, ValueError):
        pass
    return cfg

def save_config(cfg, path=None):
    path = path or CONFIG_PATH
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)

MODIFIER_CODES = {29, 42, 54, 56, 97, 100, 125, 126}   # L/R Ctrl, Shift, Alt, Meta

def validate_hotkey(codes):
    """Hotkey keys are never forwarded to the phone, so require 2–4 keys incl. a modifier."""
    if not isinstance(codes, list) or not 2 <= len(codes) <= 4:
        return "단축키는 2~4개 키 조합이어야 해요"
    if not all(isinstance(c, int) and 0 < c < 768 for c in codes) or len(set(codes)) != len(codes):
        return "잘못된 키 코드예요"
    if not MODIFIER_CODES & set(codes):
        return "Ctrl/Alt/Shift/Win 중 하나는 포함해야 해요 (일반 키가 폰으로 안 가게 되는 걸 막기 위해)"
    return None

def should_authorize(mac, uuid, target):
    """BlueZ AuthorizeService policy: the target phone gets HID only (no call/audio).
    Every other device is accepted so headsets etc. keep working."""
    if target and mac.upper() == target.upper():
        return uuid.lower() == HID_UUID
    return True

# ─── HID report building ──────────────────────────────────────────────────────
MODIFIER = {29:0x01, 42:0x02, 56:0x04, 125:0x08, 97:0x10, 54:0x20, 100:0x40, 126:0x80}
KEYMAP = {
    30:0x04,48:0x05,46:0x06,32:0x07,18:0x08,33:0x09,34:0x0A,35:0x0B,
    23:0x0C,36:0x0D,37:0x0E,38:0x0F,50:0x10,49:0x11,24:0x12,25:0x13,
    16:0x14,19:0x15,31:0x16,20:0x17,22:0x18,47:0x19,17:0x1A,45:0x1B,
    21:0x1C,44:0x1D,
    2:0x1E,3:0x1F,4:0x20,5:0x21,6:0x22,7:0x23,8:0x24,9:0x25,10:0x26,11:0x27,
    28:0x28,1:0x29,14:0x2A,15:0x2B,57:0x2C,
    12:0x2D,13:0x2E,26:0x2F,27:0x30,43:0x31,39:0x33,40:0x34,41:0x35,
    51:0x36,52:0x37,53:0x38,58:0x39,
    59:0x3A,60:0x3B,61:0x3C,62:0x3D,63:0x3E,64:0x3F,65:0x40,66:0x41,
    67:0x42,68:0x43,87:0x44,88:0x45,
    110:0x49,102:0x4A,104:0x4B,111:0x4C,107:0x4D,109:0x4E,
    106:0x4F,105:0x50,108:0x51,103:0x52,
}
MOUSE_BTN   = {272:0, 273:1, 274:2}
HANGUL_KEYS = {100, 122}    # 한/영: RIGHT_ALT on kr104 layout, KEY_HANGEUL elsewhere
LANG_SWITCH = (0x02, 0x2C)  # Shift+Space = Samsung Keyboard 한/영 전환

class Relay:
    """Turns evdev events into HID reports. ON = PC input grabbed and sent to the phone.

    Safety rules (PC must never stay locked):
      * ON only while a phone link exists; any send error/timeout → OFF + ungrab.
      * ON takes effect after the hotkey keys are released, so X never sees them stuck.
      * force_off() ungrabs unconditionally (used by watchdog / control socket).
    """
    def __init__(self, hotkey=(97, 100)):
        self.lock = threading.RLock()
        self.intr = None; self.ctrl = None
        self.modifiers = 0; self.keys = []; self.buttons = 0
        self.dx = self.dy = self.wheel = 0
        self.active = False; self.toggle = set(hotkey); self.held = set()
        self.armed = True; self.pending_on = False
        self.devices = []; self.hangul_tap = False
        self.capturing = False      # tray is recording a new hotkey → don't toggle

    def set_hotkey(self, codes):
        with self.lock:
            self.toggle = set(codes); self.held.clear(); self.armed = True; self.pending_on = False

    def reset_held(self):
        with self.lock:
            self.held.clear(); self.armed = True; self.pending_on = False

    def attach(self, intr, ctrl=None):
        intr.settimeout(SEND_TIMEOUT)
        with self.lock:
            self.intr, self.ctrl = intr, ctrl

    def _grab_all(self, on):
        for d in self.devices:
            try: d.grab() if on else d.ungrab()
            except OSError: pass

    def set_active(self, on):
        with self.lock:
            if on and not self.intr:
                log("relay ON 무시 — 폰 미연결"); return
            if on == self.active:
                return
            self.active = on
            self._grab_all(on)
            log("relay", "ON (PC 입력 차단)" if on else "OFF (PC 입력 복귀)")
            self.modifiers = 0; self.keys = []; self.buttons = 0
            self.dx = self.dy = self.wheel = 0
            self._send_kbd()

    def force_off(self):
        with self.lock:
            was = self.active
            self.active = False; self.pending_on = False
            self._grab_all(False)
            if was:                               # best-effort key/button release on the phone
                self.modifiers = 0; self.keys = []; self.buttons = 0
                self._send_kbd(); self._send_mouse()

    def disconnected(self, ctrl=None):
        """Link lost. With ctrl given, only acts if that ctrl socket is still the current link."""
        with self.lock:
            if ctrl is not None and ctrl is not self.ctrl:
                return
            close_quietly(self.intr, self.ctrl)
            self.intr = self.ctrl = None
            self.pending_on = False
            if self.active:
                self.active = False
                self._grab_all(False)
                log("relay OFF (연결 끊김 — PC 입력 복귀)")

    def _send(self, data):
        if not self.intr: return
        try:
            self.intr.send(data)
        except OSError:            # includes socket.timeout
            log("intr send failed — disconnected")
            self.disconnected()

    def _send_kbd(self):
        self._send(bytes([0xA1, 0x01, self.modifiers, 0x00] + (self.keys + [0]*6)[:6]))

    def _send_mouse(self, dx=0, dy=0, wheel=0):
        c = lambda v: max(-127, min(127, v)) & 0xFF
        self._send(bytes([0xA1, 0x02, self.buttons, c(dx), c(dy), c(wheel)]))

    def lang_switch(self):
        mods, key = LANG_SWITCH
        saved = self.modifiers, list(self.keys)
        self.modifiers, self.keys = mods, [key]; self._send_kbd()
        self.modifiers, self.keys = mods, [];    self._send_kbd()
        self.modifiers, self.keys = saved;       self._send_kbd()

    def _on_hotkey_key(self, code, value):
        if value == 1: self.held.add(code)
        elif value == 0: self.held.discard(code)
        if value == 1 and self.armed and self.toggle <= self.held:
            self.armed = False; self.hangul_tap = False   # one toggle per press (no autorepeat flapping)
            if self.active:
                self.set_active(False)
            else:
                self.pending_on = True                    # wait for release → X sees key-ups
        elif value == 1 and code in HANGUL_KEYS:          # 한/영 tap alone = language switch
            self.hangul_tap = True
        elif value == 0:
            if code in HANGUL_KEYS and self.hangul_tap and self.active:
                self.hangul_tap = False; self.lang_switch()
            if not self.held & self.toggle:
                self.armed = True
                if self.pending_on:
                    self.pending_on = False; self.set_active(True)

    def on_key(self, code, value, is_mouse=False):
        with self.lock:
            if code in self.toggle and not self.capturing:
                self._on_hotkey_key(code, value); return
            if value == 2: value = 1          # autorepeat of normal keys = still held
            if value: self.hangul_tap = False
            if not self.active: return
            if code in HANGUL_KEYS:
                if value: self.lang_switch()
                return
            if is_mouse and code in MOUSE_BTN:
                bit = 1 << MOUSE_BTN[code]
                self.buttons = (self.buttons | bit) if value else (self.buttons & ~bit)
                self._send_mouse()
            elif code in MODIFIER:
                m = MODIFIER[code]
                self.modifiers = (self.modifiers | m) if value else (self.modifiers & ~m)
                self._send_kbd()
            elif code in KEYMAP:
                h = KEYMAP[code]
                if value and h not in self.keys: self.keys.append(h)
                elif not value and h in self.keys: self.keys.remove(h)
                self._send_kbd()

    def on_rel(self, code, value):
        """Accumulate motion; on_syn() sends one report per evdev frame."""
        with self.lock:
            if not self.active: return
            if   code == 0: self.dx += value
            elif code == 1: self.dy += value
            elif code == 8: self.wheel += value

    def on_syn(self):
        with self.lock:
            if not self.active or not (self.dx or self.dy or self.wheel): return
            while self.dx or self.dy or self.wheel:       # split large moves into ±127 chunks
                cx = max(-127, min(127, self.dx)); cy = max(-127, min(127, self.dy))
                cw = max(-127, min(127, self.wheel))
                self.dx -= cx; self.dy -= cy; self.wheel -= cw
                self._send_mouse(cx, cy, cw)
                if not self.active: break
            self.dx = self.dy = self.wheel = 0

# ─── HIDP control channel ─────────────────────────────────────────────────────
def hidp_reply(data):
    """Response to a HIDP control message from the phone (None = no reply)."""
    mt = (data[0] >> 4) & 0xF
    if mt == 0x7: return bytes([0x00])            # SET_PROTOCOL → SUCCESSFUL
    if mt == 0x6: return bytes([0xA0, 0x01])      # GET_PROTOCOL → DATA(Other), report mode
    if mt in (0x4, 0x8): return bytes([0x03])     # GET/SET_REPORT etc → unsupported
    if mt in (0x5, 0x9): return bytes([0x00])
    return None

def handle_ctrl(sock, relay):
    """Serve the HIDP control channel; when it closes, the link is gone."""
    while True:
        try:
            data = sock.recv(64)
        except OSError:
            break
        if not data:
            break
        if (data[0] >> 4) == 0x1 and (data[0] & 0xF) == 0x5:   # VIRTUAL_CABLE_UNPLUG
            break
        r = hidp_reply(data)
        if r:
            try: sock.send(r)
            except OSError: break
    relay.disconnected(ctrl=sock)       # no-op if a newer link already replaced this one
    try: sock.close()
    except OSError: pass

def close_quietly(*socks):
    """shutdown() first so a thread blocked in recv() on the socket wakes up."""
    for s in socks:
        if s:
            try: s.shutdown(socket.SHUT_RDWR)
            except (OSError, AttributeError): pass
            try: s.close()
            except OSError: pass

# ─── Daemon ───────────────────────────────────────────────────────────────────
class Daemon:
    PREPARE_TIMEOUT = 60      # bluetoothd must come back within this
    CAPTURE_TIMEOUT = 60      # tray hotkey-capture mode auto-expires

    def __init__(self, cfg, run=subprocess.run):
        self.cfg = cfg
        self.run = run                    # injectable for tests
        self.relay = Relay(cfg["hotkey"])
        self.state = "idle"               # idle | preparing | pairing | finishing
        self.pair_deadline = 0
        self.pair_mac = None
        self.bluez = None                 # BluezBridge, set in main()
        self.bluez_ready = False
        self.input_thread = None
        self.capture_until = 0
        self.last_agent_claim = 0

    # ── pairing mode: bluetoothd without audio plugins + WirePlumber paused ──
    def _user_systemctl(self, *args):
        user = self.cfg.get("user")
        if user:
            return self.run(["systemctl", "--user", f"--machine={user}@", *args],
                            capture_output=True, text=True, timeout=15)

    def _user_cmd(self, *args):
        """Run a command inside the desktop user's session (pactl needs their runtime dir)."""
        user = self.cfg.get("user")
        if not user:
            return None
        import pwd
        uid = pwd.getpwnam(user).pw_uid
        return self.run(["runuser", "-u", user, "--", "env", "LC_ALL=C", f"XDG_RUNTIME_DIR=/run/user/{uid}",
                         f"DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/{uid}/bus", *args],
                        capture_output=True, text=True, timeout=15)

    def _deadman(self, unit, *cmd):
        """Restore audio after 300s even if this daemon dies mid-pairing."""
        user = self.cfg.get("user")
        if user:
            self.run(["systemd-run", "--user", f"--machine={user}@", "--on-active=300",
                      f"--unit={unit}", *cmd], capture_output=True, timeout=15)

    @staticmethod
    def _out(res):
        return getattr(res, "stdout", "") or ""

    def enter_pair_mode(self):
        """Hide call/audio from the phone while it bonds: pause the user's Bluetooth audio
        (WirePlumber or PulseAudio, only if running) and run bluetoothd without a2dp/avrcp."""
        if rfkill_unblock_bluetooth():
            log("pairing: Bluetooth was soft-blocked (꺼짐) — unblocked")
        wp = self._out(self._user_systemctl("is-active", "wireplumber")).strip() == "active"
        if wp:
            self._user_systemctl("stop", "wireplumber")
            self._deadman("bthid-wp-deadman", "systemctl", "--user", "start", "wireplumber")
        pulse = False
        if not wp and shutil.which("pactl"):
            info = self._out(self._user_cmd("pactl", "info")).lower()
            mods = self._out(self._user_cmd("pactl", "list", "short", "modules"))
            # real PulseAudio: "Server Name: pulseaudio"; pipewire-pulse mentions PipeWire → skip
            if "pulseaudio" in info and "pipewire" not in info and "module-bluetooth-discover" in mods:
                self._user_cmd("pactl", "unload-module", "module-bluetooth-discover")   # rc is 0 even on failure
                pulse = True
                self._deadman("bthid-pa-deadman", "pactl", "load-module", "module-bluetooth-discover")
        log(f"pairing: audio paused (wireplumber={wp}, pulseaudio={pulse})")
        os.makedirs(os.path.dirname(PAIR_DROPIN), exist_ok=True)
        with open(PAIR_DROPIN, "w") as f:
            f.write(f"# bthid wp={int(wp)} pulse={int(pulse)}\n"      # read back by exit_pair_mode
                    "[Service]\nExecStart=\n"
                    f"ExecStart={find_bluetoothd()} -C --noplugin=input,a2dp,avrcp\n")
        self.bluez_ready = False
        self.run(["systemctl", "daemon-reload"], timeout=30)
        self.run(["systemctl", "restart", "bluetooth"], timeout=30)

    def exit_pair_mode(self):
        """Every step runs even if an earlier one fails — audio must come back.
        Only restarts what enter_pair_mode actually stopped (flags kept in the drop-in)."""
        wp = pulse = False
        steps = []
        if os.path.exists(PAIR_DROPIN):
            try:
                head = open(PAIR_DROPIN).readline()
                if head.startswith("# bthid"):
                    wp, pulse = "wp=1" in head, "pulse=1" in head
                else:            # drop-in from an older version: it always stopped wireplumber
                    wp = True
            except OSError:
                wp = True
            steps += [lambda: os.remove(PAIR_DROPIN),
                      lambda: self.run(["systemctl", "daemon-reload"], timeout=30),
                      lambda: self.run(["systemctl", "restart", "bluetooth"], timeout=30)]
        if wp:
            steps += [lambda: self._user_systemctl("start", "wireplumber")]
        if pulse:
            steps += [lambda: self._user_cmd("pactl", "load-module", "module-bluetooth-discover")]
        steps += [lambda: self._user_systemctl("stop", "bthid-wp-deadman.timer", "bthid-pa-deadman.timer")]
        for step in steps:
            try: step()
            except Exception as e: log("exit_pair_mode step failed:", e)

    def pair_start(self):
        if self.state != "idle":
            return {"ok": False, "error": "이미 페어링 진행 중이에요"}
        self.state = "preparing"; self.pair_mac = None
        self.pair_deadline = time.time() + self.PREPARE_TIMEOUT
        self.relay.disconnected()
        log("pairing: entering pair mode (audio paused)")
        try:
            self.enter_pair_mode()
        except Exception as e:
            log("enter_pair_mode failed:", e)
            self.pair_finish(); return {"ok": False, "error": str(e)}
        return {"ok": True}

    def _open_window(self):
        """preparing → pairing once the adapter is usable; retried from tick()."""
        try:
            if self.bluez:
                self.bluez.set_discoverable(True, PAIR_WINDOW)
        except Exception as e:
            log("pairing: adapter not ready yet:", e); return
        self.state = "pairing"; self.pair_deadline = time.time() + PAIR_WINDOW
        log(f"pairing: discoverable for {PAIR_WINDOW}s")

    def on_bluez_up(self):
        """Called (main thread) whenever org.bluez appears; bluetoothd restarts wipe registrations."""
        try:
            if self.bluez:
                self.bluez.register()
        except Exception as e:
            log("bluez register failed:", e)
        self.bluez_ready = True
        if self.state == "preparing":
            self._open_window()

    def on_paired(self, mac):
        if self.state != "pairing":
            return
        log("pairing: bonded with", mac)
        self.pair_mac = mac
        self.relay.disconnected()                 # drop any link to a previous phone
        self.cfg["target"] = mac
        if mac not in self.cfg["devices"]:
            self.cfg["devices"].append(mac)
        try: save_config(self.cfg)
        except OSError as e: log("config save failed (kept in memory):", e)
        self.state = "finishing"
        self.pair_deadline = time.time() + PAIR_SETTLE   # let the phone finish SDP first
        if self.bluez:
            for call in (lambda: self.bluez.set_trusted(mac, False),   # untrusted → audio must be authorized → rejected
                         lambda: self.bluez.set_discoverable(False)):
                try: call()
                except Exception as e: log("on_paired:", e)

    def tick(self):
        """1s timer on the main thread. Must never raise: GLib drops a source whose callback raises."""
        try:
            self._tick()
        except Exception as e:
            log("tick error:", repr(e))
        return True

    def _tick(self):
        now = time.time()
        if self.state == "preparing":
            if now >= self.pair_deadline:
                log("pairing: bluetoothd did not come back in time"); self.pair_finish()
            elif self.bluez_ready:
                self._open_window()
        elif self.state in ("pairing", "finishing") and now >= self.pair_deadline:
            if self.state == "pairing":
                log("pairing: timed out")
            self.pair_finish()
        if self.input_thread is not None and not self.input_thread.is_alive():
            log("WATCHDOG: input thread died — forcing PC input back and restarting it")
            self.relay.force_off()
            self.start_input_thread()
        if self.relay.capturing and now >= self.capture_until:
            self.relay.capturing = False
        if self.bluez and now - self.last_agent_claim > 30:   # GNOME's panel may steal default-agent
            self.last_agent_claim = now
            try: self.bluez.claim_default_agent()
            except Exception: pass

    def pair_finish(self):
        if self.bluez and self.state == "pairing":
            try: self.bluez.set_discoverable(False)
            except Exception: pass
        self.state = "idle"
        self.exit_pair_mode()
        log("pairing: back to normal")

    # ── control protocol ──
    def handle_request(self, req):
        cmd = req.get("cmd")
        if cmd == "status":
            return self.status()
        if cmd == "pair_start":
            return self.pair_start()
        if cmd == "pair_cancel":
            if self.state in ("preparing", "pairing", "finishing"):
                self.pair_finish()
            return {"ok": True}
        if cmd == "relay_off":                    # emergency: give PC input back
            self.relay.force_off()
            return {"ok": True}
        if cmd == "capture":                      # tray is recording a new hotkey
            on = bool(req.get("on"))
            self.relay.capturing = on
            self.capture_until = time.time() + self.CAPTURE_TIMEOUT
            if on: self.relay.reset_held()
            return {"ok": True}
        if cmd == "remove":
            mac = (req.get("mac") or "").upper()
            if mac not in [m.upper() for m in self.cfg["devices"]]:
                return {"ok": False, "error": "이 앱으로 페어링한 기기가 아니에요"}
            if self.bluez:
                try: self.bluez.remove_device(mac)
                except Exception as e: log("remove:", e)
            self.cfg["devices"] = [m for m in self.cfg["devices"] if m.upper() != mac]
            if (self.cfg["target"] or "").upper() == mac:
                self.cfg["target"] = None
                self.relay.disconnected()
            save_config(self.cfg)
            return {"ok": True}
        if cmd == "set_hotkey":
            codes = req.get("codes")
            err = validate_hotkey(codes)
            if err:
                return {"ok": False, "error": err}
            self.cfg["hotkey"] = codes; save_config(self.cfg)
            self.relay.set_hotkey(codes)
            return {"ok": True}
        return {"ok": False, "error": f"unknown cmd {cmd!r}"}

    def status(self):
        info, powered = {}, None
        if self.bluez:
            try: info = self.bluez.devices_info(self.cfg["devices"])
            except Exception as e: log("devices_info failed:", e)
            try: powered = self.bluez.adapter_powered()
            except Exception: powered = False
        target = self.cfg["target"]
        return {
            "ok": True, "state": self.state,
            "pair_remaining": max(0, int(self.pair_deadline - time.time())) if self.state == "pairing" else 0,
            "hid_connected": self.relay.intr is not None,
            "relay": self.relay.active,
            "target": target,
            "devices": [dict(info.get(m.upper(), {}), mac=m) for m in self.cfg["devices"]],
            "hotkey": self.cfg["hotkey"],
            "powered": powered,
        }

    # ── background loops ──
    def reconnect_once(self, delay):
        """One reconnect attempt; returns the next delay (5s → 30s backoff)."""
        target = self.cfg["target"]
        if target and self.state == "idle" and self.relay.intr is None:
            return 5 if self.connect_phone(target) else min(delay * 2, 30)
        return delay

    def reconnect_loop(self):
        delay = 5
        while True:
            delay = self.reconnect_once(delay)
            time.sleep(delay)

    def connect_phone(self, mac):
        ctrl = intr = None
        try:
            ctrl = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_SEQPACKET, socket.BTPROTO_L2CAP)
            ctrl.setsockopt(SOL_BLUETOOTH, BT_SECURITY, struct.pack("BB", 2, 0))
            ctrl.settimeout(10); ctrl.connect((mac, PSM_CTRL)); ctrl.settimeout(None)
            intr = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_SEQPACKET, socket.BTPROTO_L2CAP)
            intr.setsockopt(SOL_BLUETOOTH, BT_SECURITY, struct.pack("BB", 2, 0))
            intr.settimeout(10); intr.connect((mac, PSM_INTR))
            self.relay.attach(intr, ctrl)
            threading.Thread(target=handle_ctrl, args=(ctrl, self.relay), daemon=True).start()
            log("HID connected to", mac, "(PC-initiated)")
            return True
        except OSError as e:
            close_quietly(ctrl, intr)
            log("connect to phone failed:", e)
            return False

    def accept_pair(self, ctrl, intr, addr):
        """Handle one incoming ctrl+intr pair from accept_loop."""
        target = self.cfg["target"]
        if self.state != "idle" or not target or addr.upper() != target.upper():
            log("rejecting HID connection from", addr)
            close_quietly(ctrl, intr); return False
        self.relay.disconnected()
        self.relay.attach(intr, ctrl)
        threading.Thread(target=handle_ctrl, args=(ctrl, self.relay), daemon=True).start()
        log("HID connected from", addr, "(phone-initiated)")
        return True

    def accept_loop(self, make_server=None):
        make_server = make_server or make_l2cap_server
        while True:
            ctrl_srv = intr_srv = None
            try:
                ctrl_srv = make_server(PSM_CTRL)
                intr_srv = make_server(PSM_INTR)
                while True:
                    ctrl, addr = ctrl_srv.accept()
                    intr_srv.settimeout(5)                  # a device that opens only ctrl must not block us
                    try:
                        intr, _ = intr_srv.accept()
                    except OSError:
                        close_quietly(ctrl); continue
                    self.accept_pair(ctrl, intr, addr[0])
            except OSError as e:
                log("accept loop error:", e, "— retrying in 5s")
                close_quietly(ctrl_srv, intr_srv)          # free the PSMs before rebinding
                time.sleep(5)

    def start_input_thread(self):
        self.input_thread = threading.Thread(target=self.input_loop, daemon=True)
        self.input_thread.start()

    def input_loop(self, evdev_mod=None):
        """Never let an exception leave the PC grabbed: ungrab in finally (watchdog restarts us)."""
        try:
            self._input_loop(evdev_mod)
        finally:
            self.relay.force_off()
            with self.relay.lock:
                for d in self.relay.devices:
                    try: d.close()
                    except Exception: pass
                self.relay.devices = []

    def _input_loop(self, evdev_mod=None, max_iter=None):
        evdev = evdev_mod
        if evdev is None:
            import evdev
        sel = selectors.DefaultSelector()
        known = {}                                   # path -> is_mouse
        last_scan = 0; n = 0
        while max_iter is None or n < max_iter:
            n += 1
            if time.time() - last_scan > 5:          # hotplug: rescan every 5s
                last_scan = time.time()
                for path in evdev.list_devices():
                    if path in known: continue
                    kind = classify(path, evdev)
                    if kind is None: continue
                    try:
                        d = evdev.InputDevice(path)
                    except OSError:
                        continue
                    known[path] = kind == "mouse"
                    sel.register(d, selectors.EVENT_READ)
                    with self.relay.lock:
                        self.relay.devices.append(d)
                        if self.relay.active:
                            try: d.grab()
                            except OSError: pass
                    log("input device:", d.name)
            for key, _ in sel.select(timeout=1.0):
                d = key.fileobj
                try:
                    for ev in d.read():
                        try:
                            if ev.type == 1: self.relay.on_key(ev.code, ev.value, known.get(d.path, False))
                            elif ev.type == 2: self.relay.on_rel(ev.code, ev.value)
                            elif ev.type == 0 and ev.code == 0: self.relay.on_syn()
                        except Exception as e:        # a bad event must not kill input handling
                            log("event handling error:", repr(e))
                except OSError:                       # unplugged
                    sel.unregister(d); known.pop(d.path, None)
                    with self.relay.lock:
                        if d in self.relay.devices: self.relay.devices.remove(d)
                    self.relay.reset_held()
                    log("input device removed:", d.path)

def classify(path, evdev=None):
    if evdev is None:
        import evdev
    try:
        d = evdev.InputDevice(path)
        caps = d.capabilities(); keys = caps.get(1, [])
        d.close()
    except OSError:
        return None
    if 2 in caps or 272 in keys: return "mouse"
    if 30 in keys and 3 not in caps: return "keyboard"
    return None

def make_l2cap_server(psm):
    s = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_SEQPACKET, socket.BTPROTO_L2CAP)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.setsockopt(SOL_BLUETOOTH, BT_SECURITY, struct.pack("BB", 1, 0))
    s.bind(("00:00:00:00:00:00", psm))
    s.listen(1)
    return s

# ─── BlueZ D-Bus bridge (main thread only) ────────────────────────────────────
class BluezBridge:
    AGENT_PATH, PROFILE_PATH = "/bthid/agent", "/bthid/profile"

    def __init__(self, daemon):
        import dbus, dbus.service
        self.dbus = dbus
        self.daemon = daemon
        self.bus = dbus.SystemBus()
        d = daemon
        Rejected = type("Rejected", (dbus.DBusException,), {"_dbus_error_name": "org.bluez.Error.Rejected"})

        def mac_of(path):
            return str(path).rsplit("dev_", 1)[-1].replace("_", ":").upper()

        class Agent(dbus.service.Object):
            def _window(self):
                if d.state != "pairing":
                    log("agent: rejected (not in pairing window)"); raise Rejected("not pairing")
            @dbus.service.method("org.bluez.Agent1", in_signature="", out_signature="")
            def Release(self): pass
            @dbus.service.method("org.bluez.Agent1", in_signature="o", out_signature="s")
            def RequestPinCode(self, dev): self._window(); return "0000"
            @dbus.service.method("org.bluez.Agent1", in_signature="o", out_signature="u")
            def RequestPasskey(self, dev): self._window(); return dbus.UInt32(0)
            @dbus.service.method("org.bluez.Agent1", in_signature="ouq", out_signature="")
            def DisplayPasskey(self, dev, passkey, entered): pass
            @dbus.service.method("org.bluez.Agent1", in_signature="os", out_signature="")
            def DisplayPinCode(self, dev, pin): pass
            @dbus.service.method("org.bluez.Agent1", in_signature="ou", out_signature="")
            def RequestConfirmation(self, dev, passkey): self._window(); log("agent: confirm", mac_of(dev))
            @dbus.service.method("org.bluez.Agent1", in_signature="o", out_signature="")
            def RequestAuthorization(self, dev): self._window()
            @dbus.service.method("org.bluez.Agent1", in_signature="os", out_signature="")
            def AuthorizeService(self, dev, uuid):
                ok = should_authorize(mac_of(dev), str(uuid), d.cfg["target"])
                log(f"agent: AuthorizeService {mac_of(dev)} {uuid} -> {'accept' if ok else 'REJECT'}")
                if not ok: raise Rejected("HID only")
            @dbus.service.method("org.bluez.Agent1", in_signature="", out_signature="")
            def Cancel(self): pass

        class Profile(dbus.service.Object):
            @dbus.service.method("org.bluez.Profile1", in_signature="", out_signature="")
            def Release(self): pass
            @dbus.service.method("org.bluez.Profile1", in_signature="oha{sv}", out_signature="")
            def NewConnection(self, path, fd, props): os.close(fd.take())
            @dbus.service.method("org.bluez.Profile1", in_signature="o", out_signature="")
            def RequestDisconnection(self, path): pass

        self.agent = Agent(self.bus, self.AGENT_PATH)
        self.profile = Profile(self.bus, self.PROFILE_PATH)
        self.mac_of = mac_of
        self.bus.watch_name_owner("org.bluez", self._owner_changed)
        self.bus.add_signal_receiver(self._props_changed, "PropertiesChanged",
                                     "org.freedesktop.DBus.Properties", "org.bluez",
                                     path_keyword="path")

    def _owner_changed(self, owner):
        if owner:
            log("bluez up")
            self.daemon.on_bluez_up()

    def _props_changed(self, iface, changed, invalidated, path=None):
        if iface == "org.bluez.Device1":
            if changed.get("Paired"):
                self.daemon.on_paired(self.mac_of(path))
            if "Connected" in changed and not changed["Connected"]:
                t = self.daemon.cfg["target"]
                if t and self.mac_of(path) == t.upper():
                    self.daemon.relay.disconnected()
            if changed.get("Connected") or changed.get("ServicesResolved"):
                from gi.repository import GLib
                for delay in (2, 6, 15):     # audio profiles connect after the ACL; retry a few times
                    GLib.timeout_add_seconds(delay, lambda p=path: self._drop_audio(p) and False)

    def _drop_audio(self, path):
        """Backstop: if the target phone connects audio profiles anyway, disconnect them."""
        t = self.daemon.cfg["target"]
        if not t or self.mac_of(path) != t.upper():
            return
        try:
            dev = self.bus.get_object("org.bluez", path)
            uuids = dev.Get("org.bluez.Device1", "UUIDs", dbus_interface="org.freedesktop.DBus.Properties")
            for u in uuids:
                if str(u).lower()[4:8] in ("110a", "110b", "110d", "1108", "1112", "111e", "111f"):
                    try:
                        dev.DisconnectProfile(u, dbus_interface="org.bluez.Device1")
                        log("audio profile disconnected:", u)
                    except self.dbus.DBusException:
                        pass
        except self.dbus.DBusException as e:
            log("drop_audio:", e)

    def _adapter(self):
        om = self.dbus.Interface(self.bus.get_object("org.bluez", "/"), "org.freedesktop.DBus.ObjectManager")
        for path, ifaces in om.GetManagedObjects().items():
            if "org.bluez.Adapter1" in ifaces:
                return path
        raise RuntimeError("no Bluetooth adapter")

    def _set(self, path, iface, prop, value):
        obj = self.bus.get_object("org.bluez", path)
        obj.Set(iface, prop, value, dbus_interface="org.freedesktop.DBus.Properties")

    def register(self):
        xml = open(os.path.join(APP_DIR, "hid.xml")).read()
        pm = self.dbus.Interface(self.bus.get_object("org.bluez", "/org/bluez"), "org.bluez.ProfileManager1")
        am = self.dbus.Interface(self.bus.get_object("org.bluez", "/org/bluez"), "org.bluez.AgentManager1")
        for call in (lambda: pm.RegisterProfile(self.PROFILE_PATH, HID_UUID,
                         {"ServiceRecord": xml, "Role": "server",
                          "RequireAuthentication": False, "RequireAuthorization": False}),
                     lambda: am.RegisterAgent(self.AGENT_PATH, "NoInputNoOutput"),
                     lambda: am.RequestDefaultAgent(self.AGENT_PATH)):
            try: call()
            except self.dbus.DBusException as e: log("register:", e.get_dbus_message())
        try:
            a = self._adapter()
            self._set(a, "org.bluez.Adapter1", "Powered", self.dbus.Boolean(True))
        except Exception as e:
            log("power on:", e)
        log("HID profile + agent registered")

    def adapter_powered(self):
        obj = self.bus.get_object("org.bluez", self._adapter())
        return bool(obj.Get("org.bluez.Adapter1", "Powered", dbus_interface="org.freedesktop.DBus.Properties"))

    def claim_default_agent(self):
        am = self.dbus.Interface(self.bus.get_object("org.bluez", "/org/bluez"), "org.bluez.AgentManager1")
        am.RequestDefaultAgent(self.AGENT_PATH)

    def set_discoverable(self, on, timeout=0):
        a = self._adapter()
        if on:
            self._set(a, "org.bluez.Adapter1", "Pairable", self.dbus.Boolean(True))
            self._set(a, "org.bluez.Adapter1", "DiscoverableTimeout", self.dbus.UInt32(timeout))
            am = self.dbus.Interface(self.bus.get_object("org.bluez", "/org/bluez"), "org.bluez.AgentManager1")
            try: am.RequestDefaultAgent(self.AGENT_PATH)   # GNOME's panel may have taken default
            except self.dbus.DBusException: pass
        self._set(a, "org.bluez.Adapter1", "Discoverable", self.dbus.Boolean(on))

    def set_trusted(self, mac, on):
        self._set(f"{self._adapter()}/dev_{mac.replace(':', '_')}", "org.bluez.Device1",
                  "Trusted", self.dbus.Boolean(on))

    def remove_device(self, mac):
        a = self._adapter()
        ad = self.dbus.Interface(self.bus.get_object("org.bluez", a), "org.bluez.Adapter1")
        try:
            ad.RemoveDevice(f"{a}/dev_{mac.replace(':', '_')}")
        except self.dbus.DBusException as e:
            log("remove:", e.get_dbus_message())

    def devices_info(self, macs):
        want = {m.upper() for m in macs}
        om = self.dbus.Interface(self.bus.get_object("org.bluez", "/"), "org.freedesktop.DBus.ObjectManager")
        out = {}
        for path, ifaces in om.GetManagedObjects().items():
            dev = ifaces.get("org.bluez.Device1")
            if dev and str(dev.get("Address", "")).upper() in want:
                out[str(dev["Address"]).upper()] = {
                    "name": str(dev.get("Alias", dev.get("Name", ""))),
                    "paired": bool(dev.get("Paired", False)),
                    "connected": bool(dev.get("Connected", False)),
                }
        return out

# ─── Control socket (GLib io watch → main thread) ─────────────────────────────
def serve_control(daemon, GLib):
    os.makedirs(os.path.dirname(SOCK_PATH), exist_ok=True)
    try: os.unlink(SOCK_PATH)
    except FileNotFoundError: pass
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(SOCK_PATH)
    user = daemon.cfg.get("user")
    if user:
        import pwd
        os.chown(SOCK_PATH, 0, pwd.getpwnam(user).pw_gid)
    os.chmod(SOCK_PATH, 0o660)
    srv.listen(4)

    def on_conn(fd, cond):
        try:
            conn, _ = srv.accept()
        except OSError:
            return True
        conn.settimeout(0.5)
        try:
            line = conn.makefile().readline()
            resp = daemon.handle_request(json.loads(line))
        except Exception as e:
            resp = {"ok": False, "error": str(e)}
        try:
            conn.sendall((json.dumps(resp, ensure_ascii=False) + "\n").encode())
        except OSError:
            pass
        close_quietly(conn)
        return True                      # returning False/raising would remove the watch
    GLib.io_add_watch(srv.fileno(), GLib.IO_IN, on_conn)
    return srv

def main():
    import dbus.mainloop.glib
    from gi.repository import GLib
    if os.geteuid() != 0:
        sys.exit("bthidd must run as root")
    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    cfg = load_config()
    daemon = Daemon(cfg)
    if os.path.exists(PAIR_DROPIN):          # crash during pairing last time → restore audio
        log("found stale pair-mode drop-in, restoring")
        daemon.exit_pair_mode()
    daemon.bluez = BluezBridge(daemon)       # watch_name_owner fires on_bluez_up if bluez is running
    _srv = serve_control(daemon, GLib)
    daemon.start_input_thread()
    for fn in (daemon.accept_loop, daemon.reconnect_loop):
        threading.Thread(target=fn, daemon=True).start()
    GLib.timeout_add_seconds(1, daemon.tick)
    log("started; target =", cfg["target"], "hotkey =", cfg["hotkey"])
    GLib.MainLoop().run()

if __name__ == "__main__":
    main()
