"""Unit tests for bthidd core + tray. Run: python3 -m unittest discover -s tests"""
import json, os, socket, sys, tempfile, threading, time, types, unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import bthidd as B

HAVE_DISPLAY = bool(os.environ.get("DISPLAY"))
if HAVE_DISPLAY:
    import bthid_tray as T
else:   # helpers only
    T = None


class Intr:
    def __init__(self): self.sent = []; self.closed = False; self.timeout = None
    def send(self, b): self.sent.append(bytes(b))
    def settimeout(self, t): self.timeout = t
    def close(self): self.closed = True

class SlowIntr(Intr):
    def send(self, b): raise socket.timeout("timed out")

class Dev:
    def __init__(self, path="/dev/input/event9", name="kbd"):
        self.grabbed = False; self.path = path; self.name = name; self.closed = False
    def grab(self): self.grabbed = True
    def ungrab(self): self.grabbed = False
    def close(self): self.closed = True

def kbd(mods, *keys):
    return bytes([0xA1, 0x01, mods, 0] + (list(keys) + [0] * 6)[:6])

def tap_hotkey(r, codes=(97, 100)):
    for c in codes: r.on_key(c, 1)
    for c in reversed(codes): r.on_key(c, 0)


class RelayTest(unittest.TestCase):
    def setUp(self):
        self.r = B.Relay(); self.i = Intr(); self.d = Dev()
        self.r.attach(self.i); self.r.devices = [self.d]

    def test_attach_sets_send_timeout(self):
        self.assertEqual(self.i.timeout, B.SEND_TIMEOUT)

    def test_on_waits_for_release_then_grabs(self):
        self.r.on_key(97, 1); self.r.on_key(100, 1)
        self.assertFalse(self.r.active)                 # still held → X must see the key-ups first
        self.r.on_key(100, 0); self.assertFalse(self.r.active)
        self.r.on_key(97, 0); self.assertTrue(self.r.active); self.assertTrue(self.d.grabbed)

    def test_off_is_immediate(self):
        tap_hotkey(self.r); self.r.on_key(97, 1); self.r.on_key(100, 1)
        self.assertFalse(self.r.active); self.assertFalse(self.d.grabbed)

    def test_autorepeat_does_not_flap(self):
        self.r.on_key(97, 1); self.r.on_key(100, 1)
        for _ in range(9): self.r.on_key(100, 2); self.r.on_key(97, 2)
        self.r.on_key(100, 0); self.r.on_key(97, 0)
        self.assertTrue(self.r.active)
        self.r.on_key(97, 1); self.r.on_key(100, 1)
        for _ in range(9): self.r.on_key(100, 2)
        self.assertFalse(self.r.active)

    def test_toggle_ignored_without_phone(self):
        r = B.Relay(); r.devices = [self.d]; tap_hotkey(r)
        self.assertFalse(r.active); self.assertFalse(self.d.grabbed)

    def test_capture_mode_ignores_hotkey(self):
        self.r.capturing = True; tap_hotkey(self.r); self.assertFalse(self.r.active)

    def test_keys_forwarded_only_when_active(self):
        self.r.on_key(30, 1); self.assertEqual(self.i.sent, [])
        tap_hotkey(self.r); self.i.sent.clear()
        self.r.on_key(30, 1); self.assertEqual(self.i.sent[-1], kbd(0, 0x04))
        self.r.on_key(30, 2); self.r.on_key(30, 0); self.assertEqual(self.i.sent[-1], kbd(0))

    def test_modifiers(self):
        tap_hotkey(self.r); self.r.on_key(42, 1); self.assertEqual(self.i.sent[-1], kbd(0x02))
        self.r.on_key(42, 0); self.assertEqual(self.i.sent[-1], kbd(0))

    def test_hangul_tap_sends_shift_space(self):
        tap_hotkey(self.r); self.i.sent.clear()
        self.r.on_key(100, 1); self.r.on_key(100, 0)
        self.assertEqual(self.i.sent[:3], [kbd(0x02, 0x2C), kbd(0x02), kbd(0)])

    def test_hangul_tap_cancelled_by_other_key(self):
        tap_hotkey(self.r); self.r.on_key(100, 1); self.r.on_key(30, 1); self.i.sent.clear()
        self.r.on_key(100, 0)
        self.assertNotIn(kbd(0x02, 0x2C), self.i.sent)

    def test_key_hangeul_122(self):
        tap_hotkey(self.r); self.i.sent.clear(); self.r.on_key(122, 1)
        self.assertEqual(self.i.sent[0], kbd(0x02, 0x2C))

    def test_hangul_key_outside_hotkey_switches_on_press(self):
        r = B.Relay(hotkey=(29, 56)); i = Intr(); r.attach(i)
        tap_hotkey(r, (29, 56)); self.assertTrue(r.active); i.sent.clear()
        r.on_key(100, 1); self.assertEqual(i.sent[0], kbd(0x02, 0x2C))

    def test_send_timeout_releases_input(self):
        tap_hotkey(self.r); self.assertTrue(self.d.grabbed)
        self.r.intr = SlowIntr()
        self.r.on_key(30, 1)
        self.assertFalse(self.r.active); self.assertFalse(self.d.grabbed); self.assertIsNone(self.r.intr)

    def test_disconnected_releases_input(self):
        tap_hotkey(self.r); self.r.disconnected()
        self.assertFalse(self.d.grabbed); self.assertTrue(self.i.closed)

    def test_disconnected_stale_ctrl_ignored(self):
        ctrl = Intr(); self.r.attach(self.i, ctrl)
        self.r.disconnected(ctrl=Intr()); self.assertIs(self.r.intr, self.i)
        self.r.disconnected(ctrl=ctrl); self.assertIsNone(self.r.intr)

    def test_force_off_releases_keys_on_phone(self):
        tap_hotkey(self.r); self.r.on_key(14, 1)          # Backspace held
        self.i.sent.clear(); self.r.force_off()
        self.assertFalse(self.r.active); self.assertFalse(self.d.grabbed)
        self.assertEqual(self.i.sent[0], kbd(0)); self.assertEqual(self.i.sent[1], bytes([0xA1, 2, 0, 0, 0, 0]))

    def test_disconnect_wakes_blocked_ctrl_reader(self):
        a, b = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        r = B.Relay(); r.attach(Intr(), b)
        t = threading.Thread(target=B.handle_ctrl, args=(b, r)); t.start(); time.sleep(0.1)
        r.disconnected()                                   # from another thread
        t.join(2); self.assertFalse(t.is_alive()); a.close()

    def test_mouse_buttons_and_batched_motion(self):
        tap_hotkey(self.r); self.i.sent.clear()
        self.r.on_key(272, 1, is_mouse=True); self.assertEqual(self.i.sent[-1], bytes([0xA1, 2, 1, 0, 0, 0]))
        self.r.on_key(272, 0, is_mouse=True); self.i.sent.clear()
        self.r.on_rel(0, 3); self.r.on_rel(1, -5); self.r.on_rel(8, 1)
        self.assertEqual(self.i.sent, [])                       # nothing until SYN_REPORT
        self.r.on_syn()
        self.assertEqual(self.i.sent, [bytes([0xA1, 2, 0, 3, (-5) & 0xFF, 1])])
        self.i.sent.clear(); self.r.on_rel(0, 300); self.r.on_syn()   # chunked into ±127
        self.assertEqual([p[3] for p in self.i.sent], [127, 127, 46])
        self.i.sent.clear(); self.r.on_syn(); self.assertEqual(self.i.sent, [])

    def test_rel_ignored_when_inactive(self):
        self.r.on_rel(0, 5); self.r.on_syn(); self.assertEqual(self.i.sent, [])

    def test_set_hotkey_and_reset_held(self):
        self.r.set_hotkey([29, 42]); tap_hotkey(self.r, (29, 42)); self.assertTrue(self.r.active)
        self.r.on_key(29, 1); self.r.reset_held(); self.assertEqual(self.r.held, set())


class PolicyTest(unittest.TestCase):
    T = "AA:BB:CC:11:22:33"
    def test_target_gets_hid_only(self):
        self.assertTrue(B.should_authorize(self.T.lower(), B.HID_UUID.upper(), self.T))
        self.assertFalse(B.should_authorize(self.T, "0000110b-0000-1000-8000-00805f9b34fb", self.T))
        self.assertFalse(B.should_authorize(self.T, "0000111f-0000-1000-8000-00805f9b34fb", self.T))
    def test_other_devices_untouched(self):
        self.assertTrue(B.should_authorize("AA:BB:CC:44:55:66", "0000110b-0000-1000-8000-00805f9b34fb", self.T))
        self.assertTrue(B.should_authorize("AA:BB:CC:44:55:66", "0000110b-0000-1000-8000-00805f9b34fb", None))

    def test_validate_hotkey(self):
        self.assertIsNone(B.validate_hotkey([97, 100]))
        for bad in ([97], [30, 31], [29, 29], "97,100", [29, 42, 56, 125, 30], [29, 9999]):
            self.assertIsNotNone(B.validate_hotkey(bad), bad)


class HidpTest(unittest.TestCase):
    def test_replies(self):
        self.assertEqual(B.hidp_reply(bytes([0x71])), b"\x00")
        self.assertEqual(B.hidp_reply(bytes([0x60])), b"\xa0\x01")
        self.assertEqual(B.hidp_reply(bytes([0x41])), b"\x03")
        self.assertEqual(B.hidp_reply(bytes([0x90])), b"\x00")
        self.assertIsNone(B.hidp_reply(bytes([0x10])))

    def test_handle_ctrl_unplug_disconnects(self):
        a, b = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        r = B.Relay(); i = Intr(); r.attach(i, b)
        t = threading.Thread(target=B.handle_ctrl, args=(b, r)); t.start()
        a.send(bytes([0x70])); self.assertEqual(a.recv(8), b"\x00")
        a.send(bytes([0x15]))                    # virtual cable unplug
        t.join(2); self.assertFalse(t.is_alive()); self.assertIsNone(r.intr); a.close()

    def test_handle_ctrl_eof_disconnects(self):
        a, b = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        r = B.Relay(); r.attach(Intr(), b)
        t = threading.Thread(target=B.handle_ctrl, args=(b, r)); t.start()
        a.close(); t.join(2); self.assertFalse(t.is_alive()); self.assertIsNone(r.intr)


class FakeBluez:
    def __init__(self, fail=()): self.calls = []; self.fail = set(fail)
    def _c(self, *c):
        self.calls.append(c if len(c) > 1 else c[0])
        if c[0] in self.fail: raise RuntimeError(c[0] + " failed")
    def register(self): self._c("register")
    def set_discoverable(self, on, timeout=0): self._c("disc", on)
    def set_trusted(self, mac, on): self._c("trusted", mac, on)
    def remove_device(self, mac): self._c("remove", mac)
    def claim_default_agent(self): self._c("claim")
    def devices_info(self, macs): return {m.upper(): {"name": "S25", "connected": True} for m in macs}


class DaemonTest(unittest.TestCase):
    MAC = "AA:BB:CC:11:22:33"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg_path = os.path.join(self.tmp.name, "config.json")
        self.dropin = os.path.join(self.tmp.name, "d", "zz.conf")
        for p in (mock.patch.object(B, "CONFIG_PATH", self.cfg_path),
                  mock.patch.object(B, "PAIR_DROPIN", self.dropin)):
            p.start(); self.addCleanup(p.stop)
        self.runs = []
        cfg = B.load_config(); cfg["user"] = "tester"
        self.d = B.Daemon(cfg, run=lambda args, **kw: self.runs.append(args))
        self.d.bluez = FakeBluez()

    def tearDown(self): self.tmp.cleanup()

    def test_config_roundtrip(self):
        cfg = B.load_config(os.path.join(self.tmp.name, "missing.json"))
        self.assertEqual(cfg["hotkey"], [97, 100])
        cfg["target"] = self.MAC; B.save_config(cfg)
        self.assertEqual(B.load_config()["target"], self.MAC)

    def test_status_no_target(self):
        st = self.d.handle_request({"cmd": "status"})
        self.assertTrue(st["ok"]); self.assertEqual(st["state"], "idle"); self.assertIsNone(st["target"])
        self.assertFalse(st["hid_connected"])

    def test_full_pairing_flow(self):
        self.assertTrue(self.d.handle_request({"cmd": "pair_start"})["ok"])
        self.assertEqual(self.d.state, "preparing")
        self.assertIn("-C --noplugin=input,a2dp,avrcp", open(self.dropin).read())
        self.assertIn(["systemctl", "--user", "--machine=tester@", "stop", "wireplumber"], self.runs)
        self.assertTrue(any("bthid-wp-deadman" in " ".join(r) for r in self.runs))
        self.assertFalse(self.d.handle_request({"cmd": "pair_start"})["ok"])   # already pairing
        self.d.on_bluez_up()
        self.assertEqual(self.d.state, "pairing"); self.assertIn(("disc", True), self.d.bluez.calls)
        self.assertGreater(self.d.status()["pair_remaining"], 100)
        old = Intr(); self.d.relay.attach(old)
        self.d.on_paired(self.MAC)
        self.assertTrue(old.closed)                                   # previous phone link dropped
        self.assertEqual(self.d.cfg["target"], self.MAC); self.assertIn(self.MAC, self.d.cfg["devices"])
        self.assertIn(("trusted", self.MAC, False), self.d.bluez.calls)
        self.assertEqual(self.d.state, "finishing")
        self.d.pair_deadline = 0; self.d.tick()
        self.assertEqual(self.d.state, "idle"); self.assertFalse(os.path.exists(self.dropin))
        self.assertIn(["systemctl", "--user", "--machine=tester@", "start", "wireplumber"], self.runs)
        self.assertEqual(B.load_config()["target"], self.MAC)

    def test_preparing_retries_then_times_out(self):
        self.d.bluez = FakeBluez(fail={"disc"})
        self.d.pair_start(); self.d.on_bluez_up()
        self.assertEqual(self.d.state, "preparing")                   # adapter not ready → stay, retry
        self.d.bluez.fail.clear(); self.d.tick(); self.assertEqual(self.d.state, "pairing")
        self.d.state = "preparing"; self.d.pair_deadline = 0; self.d.tick()
        self.assertEqual(self.d.state, "idle")

    def test_on_paired_survives_bluez_errors(self):
        self.d.bluez = FakeBluez(fail={"trusted", "disc"})
        self.d.state = "pairing"; self.d.on_paired(self.MAC)
        self.assertEqual(self.d.state, "finishing")

    def test_pairing_timeout_and_cancel(self):
        self.d.pair_start(); self.d.on_bluez_up(); self.d.pair_deadline = 0; self.d.tick()
        self.assertEqual(self.d.state, "idle"); self.assertIsNone(self.d.cfg["target"])
        self.d.pair_start(); self.assertTrue(self.d.handle_request({"cmd": "pair_cancel"})["ok"])
        self.assertEqual(self.d.state, "idle"); self.assertFalse(os.path.exists(self.dropin))

    def test_on_paired_ignored_outside_window(self):
        self.d.on_paired(self.MAC); self.assertIsNone(self.d.cfg["target"])

    def test_enter_pair_mode_failure_restores(self):
        def boom(args, **kw):
            if args[:2] == ["systemctl", "restart"]: raise RuntimeError("restart failed")
            self.runs.append(args)
        self.d.run = boom
        r = self.d.pair_start()
        self.assertFalse(r["ok"]); self.assertEqual(self.d.state, "idle")
        self.assertIn(["systemctl", "--user", "--machine=tester@", "start", "wireplumber"], self.runs)

    def test_exit_pair_mode_restores_audio_even_if_restart_hangs(self):
        os.makedirs(os.path.dirname(self.dropin)); open(self.dropin, "w").close()
        import subprocess
        def hang(args, **kw):
            if args[:2] == ["systemctl", "restart"]: raise subprocess.TimeoutExpired(args, 30)
            self.runs.append(args)
        self.d.run = hang; self.d.exit_pair_mode()
        self.assertIn(["systemctl", "--user", "--machine=tester@", "start", "wireplumber"], self.runs)
        self.assertIn(["systemctl", "--user", "--machine=tester@", "stop", "bthid-wp-deadman.timer"], self.runs)

    def test_remove(self):
        self.d.cfg.update(target=self.MAC, devices=[self.MAC])
        self.assertFalse(self.d.handle_request({"cmd": "remove", "mac": "AA:BB:CC:DD:EE:FF"})["ok"])
        self.assertTrue(self.d.handle_request({"cmd": "remove", "mac": self.MAC.lower()})["ok"])
        self.assertIsNone(self.d.cfg["target"]); self.assertEqual(self.d.cfg["devices"], [])
        self.assertIn(("remove", self.MAC), self.d.bluez.calls)

    def test_status_with_device(self):
        self.d.cfg.update(target=self.MAC, devices=[self.MAC])
        st = self.d.status()
        self.assertEqual(st["devices"][0]["name"], "S25"); self.assertEqual(st["devices"][0]["mac"], self.MAC)

    def test_set_hotkey(self):
        self.assertFalse(self.d.handle_request({"cmd": "set_hotkey", "codes": [30]})["ok"])
        self.assertTrue(self.d.handle_request({"cmd": "set_hotkey", "codes": [29, 56]})["ok"])
        self.assertEqual(self.d.relay.toggle, {29, 56})
        self.assertEqual(B.load_config()["hotkey"], [29, 56])

    def test_relay_off_and_capture_cmds(self):
        dev = Dev(); self.d.relay.devices = [dev]; self.d.relay.attach(Intr()); tap_hotkey(self.d.relay)
        self.assertTrue(dev.grabbed)
        self.assertTrue(self.d.handle_request({"cmd": "relay_off"})["ok"]); self.assertFalse(dev.grabbed)
        self.d.handle_request({"cmd": "capture", "on": True}); self.assertTrue(self.d.relay.capturing)
        self.d.capture_until = 0; self.d.tick(); self.assertFalse(self.d.relay.capturing)   # auto-expire

    def test_unknown_cmd(self):
        self.assertFalse(self.d.handle_request({"cmd": "nope"})["ok"])

    def test_tick_never_raises(self):
        with mock.patch.object(self.d, "_tick", side_effect=RuntimeError("can't start new thread")):
            self.assertTrue(self.d.tick())

    def test_tick_claims_default_agent(self):
        self.d.last_agent_claim = 0; self.d.tick(); self.assertIn("claim", self.d.bluez.calls)

    def test_watchdog_restarts_dead_input_thread(self):
        dev = Dev(); self.d.relay.devices = [dev]; self.d.relay.attach(Intr()); tap_hotkey(self.d.relay)
        dead = threading.Thread(target=lambda: None); dead.start(); dead.join()
        self.d.input_thread = dead
        with mock.patch.object(self.d, "start_input_thread") as restart:
            self.d.tick()
        self.assertFalse(dev.grabbed); restart.assert_called_once()

    def test_reconnect_backoff(self):
        self.assertEqual(self.d.reconnect_once(5), 5)            # no target → unchanged
        self.d.cfg["target"] = self.MAC
        with mock.patch.object(self.d, "connect_phone", return_value=False):
            self.assertEqual(self.d.reconnect_once(5), 10); self.assertEqual(self.d.reconnect_once(20), 30)
        with mock.patch.object(self.d, "connect_phone", return_value=True):
            self.assertEqual(self.d.reconnect_once(30), 5)

    def _fake_socket(self, fail_connect):
        made = []
        class S:
            def __init__(s, *a): made.append(s); s.closed = False
            def setsockopt(s, *a): pass
            def settimeout(s, t): pass
            def connect(s, addr):
                if fail_connect: raise OSError("Host is down")
            def recv(s, n): time.sleep(0.05); return b""
            def send(s, b): pass
            def close(s): s.closed = True
        return S, made

    def test_connect_phone_failure_closes_sockets(self):
        S, made = self._fake_socket(True)
        with mock.patch.object(B.socket, "socket", S):
            self.assertFalse(self.d.connect_phone(self.MAC))
        self.assertTrue(all(s.closed for s in made))

    def test_connect_phone_success_attaches(self):
        S, made = self._fake_socket(False)
        with mock.patch.object(B.socket, "socket", S):
            self.assertTrue(self.d.connect_phone(self.MAC))
        self.assertIsNotNone(self.d.relay.intr)

    def test_accept_pair_policy(self):
        a, b = Intr(), Intr()
        self.assertFalse(self.d.accept_pair(a, b, self.MAC)); self.assertTrue(a.closed)     # no target
        self.d.cfg["target"] = self.MAC; self.d.state = "pairing"
        self.assertFalse(self.d.accept_pair(Intr(), Intr(), self.MAC))                       # pairing → reject
        self.d.state = "idle"
        self.assertFalse(self.d.accept_pair(Intr(), Intr(), "AA:BB:CC:DD:EE:FF"))           # other device
        with mock.patch.object(B, "handle_ctrl"):
            self.assertTrue(self.d.accept_pair(Intr(), Intr(), self.MAC.lower()))
        self.assertIsNotNone(self.d.relay.intr)

    def test_accept_loop_closes_servers_before_rebind(self):
        servers = []
        class Srv:
            def __init__(s, psm): s.closed = False; servers.append(s)
            def settimeout(s, t): pass
            def accept(s): raise OSError("boom")
            def close(s): s.closed = True
        calls = {"n": 0}
        def make(psm):
            calls["n"] += 1
            if calls["n"] > 4: raise SystemExit      # stop the loop
            return Srv(psm)
        with mock.patch.object(B.time, "sleep"), self.assertRaises(SystemExit):
            self.d.accept_loop(make_server=make)
        self.assertTrue(all(s.closed for s in servers))

    def test_accept_loop_accepts_pair(self):
        self.d.cfg["target"] = self.MAC
        class Srv:
            n = 0
            def __init__(s, psm): s.psm = psm
            def settimeout(s, t): pass
            def accept(s):
                Srv.n += 1
                if Srv.n > 2: raise SystemExit
                return Intr(), (self.MAC, 17)
            def close(s): pass
        with mock.patch.object(B, "handle_ctrl"), self.assertRaises(SystemExit):
            self.d.accept_loop(make_server=Srv)
        self.assertIsNotNone(self.d.relay.intr)


class FakeEv:
    def __init__(self, t, c, v): self.type, self.code, self.value = t, c, v

class FakeInputDevice:
    instances = {}
    def __init__(self, path):
        self.path = path; self.name = "fake " + path; self.grabbed = False
        self.r, self.w = os.pipe(); self.events = []; self.fail = None
        FakeInputDevice.instances[path] = self
    def fileno(self): return self.r
    def capabilities(self): return {1: [30, 272] if "mouse" in self.path else [30], 2: [0, 1]} if "mouse" in self.path else {1: [30]}
    def read(self):
        os.read(self.r, 64)
        if self.fail: raise self.fail
        ev, self.events = self.events, []
        return ev
    def push(self, *evs):
        self.events.extend(FakeEv(*e) for e in evs); os.write(self.w, b"x")
    def grab(self): self.grabbed = True
    def ungrab(self): self.grabbed = False
    def close(self): pass

class InputLoopTest(unittest.TestCase):
    def setUp(self):
        FakeInputDevice.instances = {}
        self.ev = types.SimpleNamespace(list_devices=lambda: ["/dev/input/kbd0", "/dev/input/mouse0", "/dev/input/other"],
                                        InputDevice=FakeInputDevice)
        orig = FakeInputDevice.capabilities
        def caps(dev):
            if "other" in dev.path: return {3: [0]}
            return orig(dev)
        p = mock.patch.object(FakeInputDevice, "capabilities", caps); p.start(); self.addCleanup(p.stop)
        self.d = B.Daemon(dict(B.DEFAULT_CONFIG, devices=[]), run=lambda *a, **k: None)
        self.d.relay.attach(Intr())

    def test_events_flow_and_bad_event_does_not_kill_loop(self):
        th = threading.Thread(target=self.d.input_loop, args=(self.ev,), daemon=True)
        th.start(); time.sleep(0.2)
        kbd_dev = FakeInputDevice.instances["/dev/input/kbd0"]
        self.assertEqual(len(self.d.relay.devices), 2)                 # "other" not classified
        kbd_dev.push((1, 97, 1), (1, 100, 1), (1, 100, 0), (1, 97, 0)); time.sleep(0.2)
        self.assertTrue(self.d.relay.active); self.assertTrue(kbd_dev.grabbed)
        with mock.patch.object(self.d.relay, "on_key", side_effect=ValueError("bad")):
            kbd_dev.push((1, 30, 1)); time.sleep(0.2)
        self.assertTrue(th.is_alive())                                 # survived the exception
        mouse = FakeInputDevice.instances["/dev/input/mouse0"]
        mouse.push((2, 0, 5), (0, 0, 0)); time.sleep(0.2)
        self.assertEqual(self.d.relay.intr.sent[-1][3], 5)
        mouse.fail = OSError("unplugged"); mouse.push(); time.sleep(0.2)
        self.assertEqual(len(self.d.relay.devices), 1)

    def test_fatal_error_ungrabs(self):
        dev = Dev(); self.d.relay.devices = [dev]; tap_hotkey(self.d.relay); self.assertTrue(dev.grabbed)
        with mock.patch.object(self.d, "_input_loop", side_effect=RuntimeError("fatal")):
            with self.assertRaises(RuntimeError):
                self.d.input_loop()
        self.assertFalse(dev.grabbed); self.assertFalse(self.d.relay.active); self.assertTrue(dev.closed)

    def test_classify(self):
        self.assertEqual(B.classify("/dev/input/mouse0", self.ev), "mouse")
        self.assertEqual(B.classify("/dev/input/kbd0", self.ev), "keyboard")
        self.assertIsNone(B.classify("/dev/input/other", self.ev))
        bad = types.SimpleNamespace(InputDevice=mock.Mock(side_effect=OSError))
        self.assertIsNone(B.classify("/x", bad))


# ─── fake dbus for BluezBridge ───────────────────────────────────────────────
class FakeDBusException(Exception):
    def get_dbus_message(self): return str(self)

class FakeObj:
    def __init__(self, bus, path): self.bus, self.path = bus, path
    def Set(self, iface, prop, value, dbus_interface=None): self.bus.sets.append((self.path, prop, value))
    def Get(self, iface, prop, dbus_interface=None): return self.bus.uuids
    def DisconnectProfile(self, uuid, dbus_interface=None):
        self.bus.disconnected.append(uuid)
        if uuid.startswith("0000110a"): raise FakeDBusException("not connected")
    def GetManagedObjects(self): return self.bus.objects
    def RegisterProfile(self, *a): self.bus.registered.append("profile")
    def RegisterAgent(self, *a): self.bus.registered.append("agent")
    def RequestDefaultAgent(self, *a):
        self.bus.registered.append("default")
        if self.bus.fail_default: raise FakeDBusException("denied")
    def RemoveDevice(self, path):
        self.bus.removed.append(path)
        if "EE_FF" in path: raise FakeDBusException("Does Not Exist")

class FakeBus:
    def __init__(self):
        self.sets, self.registered, self.removed, self.disconnected = [], [], [], []
        self.fail_default = False
        self.uuids = ["0000110a-0000-1000-8000-00805f9b34fb", "0000111f-0000-1000-8000-00805f9b34fb",
                      B.HID_UUID]
        self.objects = {"/org/bluez/hci0": {"org.bluez.Adapter1": {}},
                        "/org/bluez/hci0/dev_AA_BB_CC_11_22_33": {"org.bluez.Device1": {
                            "Address": "AA:BB:CC:11:22:33", "Alias": "S25 Edge", "Paired": True, "Connected": True}},
                        "/org/bluez/hci0/dev_AA_BB_CC_44_55_66": {"org.bluez.Device1": {"Address": "AA:BB:CC:44:55:66"}}}
        self.owner_cb = None; self.sig_cb = None
    def get_object(self, name, path): return FakeObj(self, path)
    def watch_name_owner(self, name, cb): self.owner_cb = cb
    def add_signal_receiver(self, cb, *a, **k): self.sig_cb = cb

def fake_dbus_modules(bus):
    dbus = types.ModuleType("dbus"); svc = types.ModuleType("dbus.service")
    class Object:
        def __init__(self, bus, path): self.path = path
    svc.Object = Object
    svc.method = lambda *a, **k: (lambda f: f)
    dbus.service = svc
    dbus.DBusException = FakeDBusException
    dbus.SystemBus = lambda: bus
    dbus.Interface = lambda obj, iface: obj
    dbus.Boolean = bool; dbus.UInt32 = int
    return {"dbus": dbus, "dbus.service": svc}

class BluezBridgeTest(unittest.TestCase):
    MAC = "AA:BB:CC:11:22:33"
    def setUp(self):
        self.bus = FakeBus()
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        for p in (mock.patch.dict(sys.modules, fake_dbus_modules(self.bus)),
                  mock.patch.object(B, "CONFIG_PATH", os.path.join(self.tmp.name, "c.json"))):
            p.start(); self.addCleanup(p.stop)
        self.d = B.Daemon(dict(B.DEFAULT_CONFIG, devices=[], target=self.MAC), run=lambda *a, **k: None)
        self.br = B.BluezBridge(self.d); self.d.bluez = self.br

    def test_owner_change_registers(self):
        self.bus.owner_cb(":1.5")
        self.assertEqual(self.bus.registered[:3], ["profile", "agent", "default"])
        self.assertIn(("/org/bluez/hci0", "Powered", True), self.bus.sets)
        self.bus.owner_cb("")                                   # bluez gone → nothing
        self.assertEqual(len(self.bus.registered), 3)

    def test_agent_window_and_authorize(self):
        a = self.br.agent; dev = "/org/bluez/hci0/dev_AA_BB_CC_11_22_33"
        with self.assertRaises(FakeDBusException): a.RequestConfirmation(dev, 123)
        self.d.state = "pairing"
        a.RequestConfirmation(dev, 123); a.RequestAuthorization(dev)
        self.assertEqual(a.RequestPinCode(dev), "0000"); self.assertEqual(a.RequestPasskey(dev), 0)
        a.DisplayPasskey(dev, 1, 0); a.DisplayPinCode(dev, "1"); a.Release(); a.Cancel()
        a.AuthorizeService(dev, B.HID_UUID)
        with self.assertRaises(FakeDBusException): a.AuthorizeService(dev, "0000110b-0000-1000-8000-00805f9b34fb")
        a.AuthorizeService("/org/bluez/hci0/dev_AA_BB_CC_44_55_66", "0000110b-0000-1000-8000-00805f9b34fb")
        p = self.br.profile; p.Release(); p.RequestDisconnection("/x")
        r, w = os.pipe(); p.NewConnection("/x", types.SimpleNamespace(take=lambda: r), {}); os.close(w)

    def test_props_changed(self):
        self.d.state = "pairing"
        dev = "/org/bluez/hci0/dev_AA_BB_CC_11_22_33"
        self.bus.sig_cb("org.bluez.Device1", {"Paired": True}, [], path=dev)
        self.assertEqual(self.d.state, "finishing")
        self.d.relay.attach(Intr())
        self.bus.sig_cb("org.bluez.Device1", {"Connected": False}, [], path=dev)
        self.assertIsNone(self.d.relay.intr)
        with mock.patch("gi.repository.GLib.timeout_add_seconds") as ta:
            self.bus.sig_cb("org.bluez.Device1", {"ServicesResolved": True}, [], path=dev)
        self.assertEqual(ta.call_count, 3)
        ta.call_args_list[0][0][1]()                            # run one scheduled drop
        self.assertIn("0000111f-0000-1000-8000-00805f9b34fb", self.bus.disconnected)
        self.assertNotIn(B.HID_UUID, self.bus.disconnected)
        self.bus.sig_cb("org.bluez.Adapter1", {"Powered": True}, [], path="/org/bluez/hci0")

    def test_drop_audio_ignores_other_devices(self):
        self.br._drop_audio("/org/bluez/hci0/dev_AA_BB_CC_44_55_66"); self.assertEqual(self.bus.disconnected, [])

    def test_adapter_props_and_devices(self):
        self.br.set_discoverable(True, 120)
        props = [p for _, p, _ in self.bus.sets]
        self.assertEqual(props, ["Pairable", "DiscoverableTimeout", "Discoverable"])
        self.bus.fail_default = True; self.br.set_discoverable(True, 120)      # default-agent refusal tolerated
        self.bus.fail_default = False
        self.br.set_trusted(self.MAC, False)
        self.assertIn(("/org/bluez/hci0/dev_AA_BB_CC_11_22_33", "Trusted", False), self.bus.sets)
        self.br.claim_default_agent()
        self.br.remove_device(self.MAC); self.br.remove_device("AA:BB:CC:DD:EE:FF")
        self.assertEqual(len(self.bus.removed), 2)
        info = self.br.devices_info([self.MAC])
        self.assertEqual(info[self.MAC], {"name": "S25 Edge", "paired": True, "connected": True})

    def test_no_adapter(self):
        self.bus.objects = {}
        with self.assertRaises(RuntimeError): self.br._adapter()
        self.br.register()                                       # power-on failure is logged, not raised


class ControlSocketTest(unittest.TestCase):
    @unittest.skipUnless(HAVE_DISPLAY, "tray import needs a display")
    def test_roundtrip_through_unix_socket(self):
        from gi.repository import GLib
        tmp = tempfile.mkdtemp(); path = os.path.join(tmp, "ctl.sock")
        d = B.Daemon(dict(B.DEFAULT_CONFIG, devices=[]), run=lambda *a, **k: None)
        with mock.patch.object(B, "SOCK_PATH", path), mock.patch.object(T, "SOCK_PATH", path):
            srv = B.serve_control(d, GLib)
            out = {}
            def client():
                out["st"] = T.request("status"); out["bad"] = T.request("nope")
                s = socket.socket(socket.AF_UNIX); s.connect(path); s.sendall(b"not json\n")
                out["garbage"] = json.loads(s.makefile().readline()); s.close()
            th = threading.Thread(target=client); th.start()
            ctx = GLib.MainContext.default(); end = time.time() + 3
            while th.is_alive() and time.time() < end:
                ctx.iteration(False); time.sleep(0.01)
            th.join(1); srv.close()
        self.assertTrue(out["st"]["ok"]); self.assertEqual(out["st"]["state"], "idle")
        self.assertFalse(out["bad"]["ok"]); self.assertFalse(out["garbage"]["ok"])
        self.assertEqual(oct(os.stat(path).st_mode & 0o777), "0o660")


@unittest.skipUnless(HAVE_DISPLAY, "GTK tests need a display")
class TrayTest(unittest.TestCase):
    ST = {"ok": True, "state": "idle", "pair_remaining": 0, "target": "AA:BB:CC:11:22:33",
          "relay": False, "hid_connected": True, "hotkey": [97, 100],
          "devices": [{"mac": "AA:BB:CC:11:22:33", "name": "S25 Edge", "connected": True}]}

    def test_request_daemon_down(self):
        with mock.patch.object(T, "SOCK_PATH", "/nonexistent/x.sock"):
            r = T.request("status")
        self.assertFalse(r["ok"]); self.assertTrue(r["daemon_down"])
        self.assertEqual(T.summary(r), "데몬 미실행"); self.assertEqual(T.icon_for(r), "dialog-error")

    def test_summary_states(self):
        base = dict(self.ST, hid_connected=False)
        self.assertEqual(T.summary(base), "폰 연결 대기 중"); self.assertEqual(T.icon_for(base), "bluetooth-disabled")
        self.assertIn("꺼짐", T.summary(dict(base, hid_connected=True)))
        self.assertEqual(T.icon_for(dict(base, hid_connected=True)), "bluetooth-active")
        self.assertIn("차단", T.summary(dict(base, relay=True))); self.assertEqual(T.icon_for(dict(base, relay=True)), "input-keyboard")
        self.assertEqual(T.summary(dict(base, target=None)), "페어링된 폰 없음")
        self.assertIn("57초", T.summary(dict(base, state="pairing", pair_remaining=57)))
        self.assertIn("준비", T.summary(dict(base, state="preparing")))
        self.assertIn("오류", T.summary({"ok": False, "error": "x"}))

    def test_hotkey_text(self):
        self.assertEqual(T.hotkey_text([97, 100]), "R-Ctrl(한자) + R-Alt(한/영)")
        self.assertEqual(T.key_name(30), "A")

    def test_app_poll_and_settings_refresh(self):
        with mock.patch.object(T, "request", return_value=self.ST), \
             mock.patch.object(T.GLib, "timeout_add_seconds"):
            app = T.App()
            self.assertEqual(app.item_status.get_label(), "연결됨 — 입력 전달 꺼짐")
            app.open()
            v = app.win.vals
            self.assertEqual(v["name"].get_text(), "S25 Edge"); self.assertEqual(v["mac"].get_text(), self.ST["target"])
            self.assertEqual(v["hotkey"].get_text(), "R-Ctrl(한자) + R-Alt(한/영)")
            self.assertEqual(app.win.btn_pair.get_label(), "신규 장비 페어링")
            app.win.refresh(dict(self.ST, state="pairing", pair_remaining=30))
            self.assertEqual(app.win.btn_pair.get_label(), "페어링 취소")
            self.assertFalse(app.win.btn_remove.get_sensitive())
            app.win.refresh({"ok": False, "daemon_down": True})
            self.assertEqual(v["status"].get_text(), "데몬 미실행")
            app.poll()
            app.win.destroy()

    def _run_dialog(self, script):
        """Patch Gtk.Dialog.run: script(dialog, call_no) → response id."""
        calls = {"n": 0}
        def run(dlg):
            calls["n"] += 1
            return script(dlg, calls["n"])
        return mock.patch.object(T.Gtk.Dialog, "run", run)

    def _key(self, dlg, signal, evdev_code):
        kind = T.Gdk.EventType.KEY_PRESS if "press" in signal else T.Gdk.EventType.KEY_RELEASE
        ev = T.Gdk.Event.new(kind); ev.key.hardware_keycode = evdev_code + 8
        dlg.emit(signal, ev)

    def test_remove_dialog(self):
        with mock.patch.object(T, "request", side_effect=lambda c, **k: self.ST if c == "status" else {"ok": True}) as req, \
             mock.patch.object(T.GLib, "timeout_add_seconds"):
            app = T.App(); app.last = self.ST
            with self._run_dialog(lambda d, n: T.Gtk.ResponseType.OK), mock.patch.object(app.win, "msg") as msg:
                app.win.on_remove(None)
            req.assert_any_call("remove", mac="AA:BB:CC:11:22:33"); msg.assert_called_once()
            req.reset_mock()
            with self._run_dialog(lambda d, n: T.Gtk.ResponseType.CANCEL):
                app.win.on_remove(None)
            self.assertNotIn(mock.call("remove", mac="AA:BB:CC:11:22:33"), req.call_args_list)
            app.win.destroy()

    def test_hotkey_dialog_records_and_saves(self):
        def script(d, n):
            if n == 1:                                   # first: type L-Ctrl+L-Alt, then "다시 입력"
                self._key(d, "key-press-event", 29); self._key(d, "key-press-event", 56)
                self._key(d, "key-release-event", 56); self._key(d, "key-release-event", 29)
                return 1
            self._key(d, "key-press-event", 29); self._key(d, "key-press-event", 42)
            return T.Gtk.ResponseType.OK
        with mock.patch.object(T, "request", side_effect=lambda c, **k: self.ST if c == "status" else {"ok": False, "error": "x"}) as req, \
             mock.patch.object(T.GLib, "timeout_add_seconds"):
            app = T.App()
            with self._run_dialog(script), mock.patch.object(app.win, "msg") as msg, \
                 mock.patch.object(T.GLib, "source_remove") as rm:
                app.win.on_hotkey(None)
            rm.assert_called_once()                       # capture keep-alive timer cancelled
            req.assert_any_call("capture", on=True); req.assert_any_call("capture", on=False)
            req.assert_any_call("set_hotkey", codes=[29, 42])
            msg.assert_called_once()                      # daemon refused → error shown
            app.win.destroy()

    def test_msg_dialog(self):
        with mock.patch.object(T, "request", return_value=self.ST), mock.patch.object(T.GLib, "timeout_add_seconds"):
            app = T.App()
            with mock.patch.object(T.Gtk.Dialog, "run", return_value=T.Gtk.ResponseType.OK):
                self.assertEqual(app.win.msg("hi"), T.Gtk.ResponseType.OK)
            app.win.destroy()

    def test_pair_button_cancel_and_start(self):
        with mock.patch.object(T, "request", return_value=self.ST) as req, \
             mock.patch.object(T.GLib, "timeout_add_seconds"):
            app = T.App()
            app.last = dict(self.ST, state="pairing")
            app.win.on_pair(None); req.assert_called_with("pair_cancel")
            app.last = self.ST
            with mock.patch.object(app.win, "msg", return_value=T.Gtk.ResponseType.CANCEL):
                app.win.on_pair(None)                      # user cancelled the confirm dialog
            with mock.patch.object(app.win, "msg", return_value=T.Gtk.ResponseType.OK), \
                 mock.patch.object(T.threading, "Thread") as th:
                app.win.on_pair(None)
                th.assert_called_once()
                work = th.call_args.kwargs["target"]
            with mock.patch.object(T.GLib, "idle_add", side_effect=lambda f: f()), \
                 mock.patch.object(app.win, "msg") as msg:
                req.return_value = {"ok": False, "error": "x"}; work()
                msg.assert_called_once()
            app.win.destroy()


if __name__ == "__main__":
    unittest.main()
