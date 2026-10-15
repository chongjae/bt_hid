#!/usr/bin/env python3
"""
BT HID Relay — PC keyboard/mouse → S25 Edge via Bluetooth.

Architecture: PC = HID device server (PSM 0x11/0x13).
Phone connects as HID host when '텍스트 입력' is toggled ON.

Run:    sudo python3 bt_hid.py
Toggle: Right-Ctrl + Right-Alt
Quit:   Ctrl-C
"""
import os, sys, socket, struct, selectors, threading, time, subprocess, atexit, ctypes

PHONE_MAC   = "AA:BB:CC:11:22:33"
BT_MAC      = "AA:BB:CC:00:00:01"   # hci0
PSM_CTRL    = 0x11
PSM_INTR    = 0x13
TOGGLE_KEYS = {100, 97}   # RIGHT_ALT(100) + RIGHT_CTRL(97)

SOL_BLUETOOTH  = 274
BT_SECURITY    = 4
BT_SECURITY_LOW = 1    # accept any security level

# ─── HID descriptors ──────────────────────────────────────────────────────────
KBD_DESC = bytes([
    0x05,0x01, 0x09,0x06, 0xA1,0x01, 0x85,0x01,
    0x05,0x07, 0x19,0xE0, 0x29,0xE7,
    0x15,0x00, 0x25,0x01, 0x75,0x01, 0x95,0x08, 0x81,0x02,
    0x95,0x01, 0x75,0x08, 0x81,0x01,
    0x95,0x06, 0x75,0x08, 0x15,0x00, 0x25,0x65,
    0x05,0x07, 0x19,0x00, 0x29,0x65, 0x81,0x00, 0xC0,
])
MOUSE_DESC = bytes([
    0x05,0x01, 0x09,0x02, 0xA1,0x01, 0x85,0x02,
    0x09,0x01, 0xA1,0x00,
    0x05,0x09, 0x19,0x01, 0x29,0x03,
    0x15,0x00, 0x25,0x01, 0x95,0x03, 0x75,0x01, 0x81,0x02,
    0x95,0x01, 0x75,0x05, 0x81,0x01,
    0x05,0x01, 0x09,0x30, 0x09,0x31, 0x09,0x38,
    0x15,0x81, 0x25,0x7F, 0x75,0x08, 0x95,0x03, 0x81,0x06,
    0xC0, 0xC0,
])
HID_DESC = KBD_DESC + MOUSE_DESC

# ─── SDP registration ─────────────────────────────────────────────────────────
_sdp_lib = _sdp_sess = None

def _seq(items):
    body = b"".join(items)
    return (bytes([0x35, len(body)]) + body if len(body) <= 255
            else bytes([0x36]) + struct.pack(">H", len(body)) + body)

def remove_stale_hid_sdp():
    out = subprocess.run(['sdptool', 'browse', 'local'],
                         capture_output=True, text=True).stdout
    handles, cur = [], None
    for line in out.splitlines():
        if 'Service RecHandle:' in line and '0x' in line:
            cur = int(line.split('0x')[1].strip(), 16)
        elif ('0x1124' in line or 'Human Interface Device' in line) and cur:
            handles.append(cur); cur = None
    for h in handles:
        subprocess.run(['sdptool', 'del', f'0x{h:x}'], capture_output=True)
    if handles:
        print(f"[BT HID] Removed {len(handles)} stale HID SDP record(s)")

def register_hid_sdp():
    global _sdp_lib, _sdp_sess
    lib = ctypes.CDLL("libbluetooth.so.3", use_errno=True)
    _sdp_lib = lib

    class bdaddr_t(ctypes.Structure):
        _fields_ = [("b", ctypes.c_uint8 * 6)]
    def str2ba(s):
        ba = bdaddr_t()
        for i, v in enumerate(reversed([int(x, 16) for x in s.split(":")])):
            ba.b[i] = v
        return ba

    lib.sdp_connect.restype  = ctypes.c_void_p
    lib.sdp_connect.argtypes = [ctypes.POINTER(bdaddr_t), ctypes.POINTER(bdaddr_t), ctypes.c_uint32]
    lib.sdp_device_record_register_binary.restype  = ctypes.c_int
    lib.sdp_device_record_register_binary.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(bdaddr_t), ctypes.c_char_p,
        ctypes.c_uint32, ctypes.c_uint8, ctypes.POINTER(ctypes.c_uint32)]
    lib.sdp_close.restype  = ctypes.c_int
    lib.sdp_close.argtypes = [ctypes.c_void_p]

    u8  = lambda v: bytes([0x08, v])
    u16 = lambda v: bytes([0x09]) + struct.pack(">H", v)
    bl  = lambda v: bytes([0x28, 0x01 if v else 0x00])
    tx  = lambda s: (bytes([0x25, len(s)]) + s if len(s) <= 255
                     else bytes([0x26]) + struct.pack(">H", len(s)) + s)
    uid = lambda v: bytes([0x19]) + struct.pack(">H", v)
    seq = lambda *a: _seq(a)
    att = lambda i, e: bytes([0x09]) + struct.pack(">H", i) + e

    record = _seq([
        att(0x0001, seq(uid(0x1124))),
        att(0x0004, seq(seq(uid(0x0100), u16(0x0011)), seq(uid(0x0011)))),
        att(0x0005, seq(uid(0x1002))),
        att(0x0006, seq(u16(0x656E), u16(0x006A), u16(0x0100))),  # LanguageBaseAttributeIDList (mandatory)
        att(0x000D, seq(seq(seq(uid(0x0100), u16(0x0013)), seq(uid(0x0011))))),
        att(0x0009, seq(seq(uid(0x1124), u16(0x0101)))),
        att(0x0100, tx(b"BT HID Relay")),
        att(0x0200, u16(0x0111)),
        att(0x0201, u8(0xC0)),
        att(0x0202, u8(0x21)),
        att(0x0203, bl(True)),   # HIDVirtualCable
        att(0x0204, bl(False)),  # HIDReconnectInitiate — host(phone) connects to us
        att(0x0205, seq(seq(u8(0x22), tx(HID_DESC)))),
        att(0x0206, seq(seq(u16(0x0409), u16(0x0100)))),
        att(0x0209, bl(False)),
        att(0x020A, bl(False)),
        att(0x020B, u16(0x0100)),
        att(0x020C, u16(0x0C80)),
        att(0x020D, bl(True)),   # HIDNormallyConnectable — we're always listening
        att(0x020E, bl(True)),
    ])

    any_ = str2ba("00:00:00:00:00:00")
    loc  = str2ba("FF:FF:FF:00:00:00")
    sess = lib.sdp_connect(ctypes.byref(any_), ctypes.byref(loc), 1)
    if not sess:
        raise RuntimeError(f"sdp_connect failed: {os.strerror(ctypes.get_errno())}")

    handle = ctypes.c_uint32(0)
    # flag=0: session-scoped (auto-removed when process exits / session closes)
    ret = lib.sdp_device_record_register_binary(
        sess, ctypes.byref(any_), record, len(record), 0, ctypes.byref(handle))
    if ret < 0:
        lib.sdp_close(sess)
        raise RuntimeError(f"SDP register failed: {os.strerror(ctypes.get_errno())}")
    _sdp_sess = sess
    atexit.register(lambda: lib.sdp_close(sess) if sess else None)
    return handle.value

def register_hid_profile():
    """Register HID via BlueZ ProfileManager1 so the phone sees UUID 0x1124 in SDP/EIR.
    We still own the L2CAP sockets (bluetoothd runs with --noplugin=input)."""
    import dbus, dbus.service, dbus.mainloop.glib
    from gi.repository import GLib

    class HidProfile(dbus.service.Object):
        @dbus.service.method("org.bluez.Profile1", in_signature="", out_signature="")
        def Release(self): pass
        @dbus.service.method("org.bluez.Profile1", in_signature="oha{sv}", out_signature="")
        def NewConnection(self, path, fd, props): os.close(fd.take())
        @dbus.service.method("org.bluez.Profile1", in_signature="o", out_signature="")
        def RequestDisconnection(self, path): pass

    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    bus = dbus.SystemBus()
    path = "/bthid/profile"
    register_hid_profile.obj = HidProfile(bus, path)   # keep a reference alive
    xml = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "hid.xml")).read()
    mgr = dbus.Interface(bus.get_object("org.bluez", "/org/bluez"), "org.bluez.ProfileManager1")
    mgr.RegisterProfile(path, "00001124-0000-1000-8000-00805f9b34fb",
                        {"ServiceRecord": xml, "Role": "server",
                         "RequireAuthentication": False, "RequireAuthorization": False})
    threading.Thread(target=GLib.MainLoop().run, daemon=True).start()

# ─── L2CAP server ─────────────────────────────────────────────────────────────
def make_l2cap_server(psm, retries=15, delay=2):
    for i in range(retries):
        try:
            s = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_SEQPACKET, socket.BTPROTO_L2CAP)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            # Accept at any security level — phone link is already AUTH+ENCRYPT
            s.setsockopt(SOL_BLUETOOTH, BT_SECURITY,
                         struct.pack("BB", BT_SECURITY_LOW, 0))
            # Bind to ANY adapter so connection isn't filtered by adapter address
            s.bind(("00:00:00:00:00:00", psm))
            s.listen(1)
            print(f"[BT HID] Listening on PSM 0x{psm:02x}")
            return s
        except OSError as e:
            s.close()
            if i < retries - 1:
                print(f"[BT HID] PSM 0x{psm:02x} busy, retry {i+1}/{retries} in {delay}s...")
                time.sleep(delay)
            else:
                raise

# ─── evdev ────────────────────────────────────────────────────────────────────
MODIFIER = {29:0x01,42:0x02,56:0x04,125:0x08,97:0x10,54:0x20,100:0x40,126:0x80}
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
MOUSE_BTN = {272:0, 273:1, 274:2}

HANGUL_KEYS = {100, 122}   # 한/영: RIGHT_ALT on kr104 layout, KEY_HANGEUL on others
LANG_SWITCH = (0x02, 0x2C) # Shift+Space = Samsung Keyboard 한/영 전환 (Ctrl+Space: (0x01, 0x2C))

class Relay:
    def __init__(self):
        self.intr = None; self.modifiers = 0; self.keys = []
        self.buttons = 0; self.active = False; self.toggle_held = set()
        self.devices = []; self.hangul_tap = False

    def set_active(self, on):
        """ON: grab PC keyboard/mouse so input goes only to the phone. OFF: give it back."""
        self.active = on
        for d in self.devices:
            try: d.grab() if on else d.ungrab()
            except OSError: pass
        print(f"\n[BT HID] relay {'ON  ↑ (PC 입력 차단)' if on else 'OFF ↓ (PC 입력 복귀)'}")
        self.modifiers = 0; self.keys = []; self.buttons = 0; self.send_kbd()

    def disconnected(self):
        print("[BT HID] intr send failed — disconnected")
        self.intr = None
        if self.active: self.set_active(False)   # never leave the PC locked with no phone

    def send_kbd(self):
        if not self.intr: return
        try:
            self.intr.send(bytes([0xA1,0x01,self.modifiers,0x00]+(self.keys+[0]*6)[:6]))
        except OSError:
            self.disconnected()

    def send_mouse(self, dx=0, dy=0, wheel=0):
        if not self.intr: return
        clamp = lambda v: max(-127, min(127, v)) & 0xFF
        try:
            self.intr.send(bytes([0xA1,0x02,self.buttons,clamp(dx),clamp(dy),clamp(wheel)]))
        except OSError:
            self.disconnected()

    def lang_switch(self):
        mods, key = LANG_SWITCH
        saved = self.modifiers, self.keys
        self.modifiers, self.keys = mods, [key]; self.send_kbd()
        self.modifiers, self.keys = mods, [];    self.send_kbd()
        self.modifiers, self.keys = saved;       self.send_kbd()

    def on_key(self, code, value, is_mouse):
        if value == 2: value = 1   # autorepeat counts as held
        if code in TOGGLE_KEYS:
            if value: self.toggle_held.add(code)
            else:     self.toggle_held.discard(code)
            if TOGGLE_KEYS <= self.toggle_held:
                self.hangul_tap = False
                self.set_active(not self.active)
            elif code in HANGUL_KEYS:
                # 한/영 tap alone (press→release, nothing in between) = language switch
                if value: self.hangul_tap = True
                elif self.hangul_tap and self.active:
                    self.hangul_tap = False; self.lang_switch()
            return
        if value: self.hangul_tap = False
        if not self.active: return
        if code in HANGUL_KEYS:
            if value: self.lang_switch()
            return
        if is_mouse:
            if code in MOUSE_BTN:
                bit = 1 << MOUSE_BTN[code]
                self.buttons = (self.buttons|bit) if value else (self.buttons&~bit)
                self.send_mouse()
        else:
            if code in MODIFIER:
                self.modifiers = (self.modifiers|MODIFIER[code]) if value else (self.modifiers&~MODIFIER[code])
                self.send_kbd()
            elif code in KEYMAP:
                hid = KEYMAP[code]
                if value and hid not in self.keys: self.keys.append(hid)
                elif not value and hid in self.keys: self.keys.remove(hid)
                self.send_kbd()

    def on_rel(self, code, value):
        if not self.active: return
        if   code == 0: self.send_mouse(dx=value)
        elif code == 1: self.send_mouse(dy=value)
        elif code == 8: self.send_mouse(wheel=value)

# ─── HIDP control handler ─────────────────────────────────────────────────────
def handle_ctrl(ctrl_sock, relay):
    proto = [1]
    while True:
        try:
            data = ctrl_sock.recv(64)
        except OSError:
            break
        if not data:
            break
        mt = (data[0] >> 4) & 0xF
        mp =  data[0]       & 0xF
        print(f"[BT HID] ctrl rx: {data.hex()}")
        try:
            if   mt == 0x7: ctrl_sock.send(bytes([0x00]))           # SET_PROTOCOL → SUCCESS
            elif mt == 0x6: ctrl_sock.send(bytes([0xA3, proto[0]])) # GET_PROTOCOL
            elif mt in (0x4, 0x8): ctrl_sock.send(bytes([0x03]))    # unsupported
            elif mt in (0x5, 0x9): ctrl_sock.send(bytes([0x00]))    # SUCCESS
            elif mt == 0x1 and mp == 0x5:
                relay.intr = None; break                            # VIRTUAL_CABLE_UNPLUG
        except OSError:
            break

# ─── Accept loop ──────────────────────────────────────────────────────────────
def accept_loop(ctrl_srv, intr_srv, relay):
    while True:
        print("[BT HID] Waiting for phone to connect on PSM 0x11...")
        try:
            ctrl_client, addr = ctrl_srv.accept()
        except OSError as e:
            print(f"[BT HID] ctrl_srv accept error: {e}")
            break
        print(f"[BT HID] PSM 0x11 connected from {addr[0]}")
        threading.Thread(target=handle_ctrl, args=(ctrl_client, relay), daemon=True).start()

        print("[BT HID] Waiting for PSM 0x13...")
        try:
            intr_client, _ = intr_srv.accept()
        except OSError as e:
            print(f"[BT HID] intr_srv accept error: {e}")
            continue
        print("[BT HID] PSM 0x13 connected — HID READY")
        print("[BT HID] >>> Right-Ctrl + Right-Alt to toggle relay ON <<<")
        relay.intr = intr_client
        if relay.active: relay.set_active(False)

# ─── Device-initiated connect (like a real keyboard reconnecting) ─────────────
def connect_to_phone(relay):
    # ponytail: retries forever every 5s while not connected; stop when relay.intr is set
    while True:
        if relay.intr is None:
            ctrl = intr = None
            try:
                ctrl = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_SEQPACKET, socket.BTPROTO_L2CAP)
                ctrl.setsockopt(SOL_BLUETOOTH, BT_SECURITY, struct.pack("BB", 2, 0))  # MEDIUM: encrypted
                ctrl.connect((PHONE_MAC, PSM_CTRL))
                intr = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_SEQPACKET, socket.BTPROTO_L2CAP)
                intr.setsockopt(SOL_BLUETOOTH, BT_SECURITY, struct.pack("BB", 2, 0))
                intr.connect((PHONE_MAC, PSM_INTR))
                print("[BT HID] Connected to phone (PC-initiated) — HID READY")
                print("[BT HID] >>> Right-Ctrl + Right-Alt to toggle relay ON <<<")
                threading.Thread(target=handle_ctrl, args=(ctrl, relay), daemon=True).start()
                relay.intr = intr
            except OSError as e:
                print(f"[BT HID] connect to phone failed: {e}")
                for s in (ctrl, intr):
                    if s: s.close()
        time.sleep(5)

# ─── evdev detection ──────────────────────────────────────────────────────────
def find_input_devices():
    import evdev
    keyboards, mice = [], []
    for path in evdev.list_devices():
        try:
            d = evdev.InputDevice(path)
            caps = d.capabilities()
            ev_key = caps.get(1, [])
            if (2 in caps) or (272 in ev_key):
                mice.append(d)
            elif (30 in ev_key) and (2 not in caps) and (3 not in caps):
                keyboards.append(d)
        except Exception:
            pass
    return keyboards, mice

# ─── Main ─────────────────────────────────────────────────────────────────────
def main():
    if os.geteuid() != 0:
        sys.exit("Run as root: sudo python3 bt_hid.py")

    remove_stale_hid_sdp()
    print("[BT HID] Registering HID profile via BlueZ D-Bus...")
    register_hid_profile()   # fail loudly: without it the phone never sees us as a keyboard
    print("[BT HID] HID profile registered")

    os.system("hciconfig hci0 class 0x002540 2>/dev/null")
    os.system("hciconfig hci0 piscan 2>/dev/null")   # ensure connectable+discoverable

    ctrl_srv = make_l2cap_server(PSM_CTRL)
    intr_srv = make_l2cap_server(PSM_INTR)

    relay = Relay()
    threading.Thread(target=accept_loop, args=(ctrl_srv, intr_srv, relay),
                     daemon=True).start()
    threading.Thread(target=connect_to_phone, args=(relay,), daemon=True).start()

    print(f"[BT HID] On S25 Edge: Bluetooth → [this PC] → 텍스트 입력 ON")

    import evdev
    keyboards, mice = find_input_devices()
    all_devices = keyboards + mice
    mouse_set = set(id(d) for d in mice)
    relay.devices = all_devices
    print(f"[BT HID] Keyboards: {[d.name for d in keyboards]}")
    print(f"[BT HID] Mice:      {[d.name for d in mice]}")
    if not all_devices:
        sys.exit("[BT HID] No input devices found!")

    sel = selectors.DefaultSelector()
    for d in all_devices:
        sel.register(d, selectors.EVENT_READ)

    print("[BT HID] Monitoring input. Ctrl-C to quit.")
    try:
        while True:
            for key, _ in sel.select(timeout=1.0):
                dev = key.fileobj
                is_mouse = id(dev) in mouse_set
                try:
                    for ev in dev.read():
                        if ev.type == 1: relay.on_key(ev.code, ev.value, is_mouse)
                        elif ev.type == 2: relay.on_rel(ev.code, ev.value)
                except OSError:
                    pass
    except KeyboardInterrupt:
        print("\n[BT HID] Quit.")

if __name__ == "__main__":
    main()
