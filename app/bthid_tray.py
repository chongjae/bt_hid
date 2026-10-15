#!/usr/bin/python3
"""bthid-tray — system tray UI for bthidd (runs as the desktop user)."""
import json, os, socket, threading

SOCK_PATH = os.environ.get("BTHID_SOCK", "/run/bthid/ctl.sock")

def request(cmd, timeout=2, **kw):
    """One JSON request to bthidd. Returns dict; {'ok': False, 'error': ...} on failure."""
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            s.connect(SOCK_PATH)
            s.sendall((json.dumps(dict(kw, cmd=cmd)) + "\n").encode())
            return json.loads(s.makefile().readline())
    except (OSError, ValueError) as e:
        return {"ok": False, "error": f"bthidd 연결 실패: {e}", "daemon_down": True}

def key_name(code):
    names = {29: "L-Ctrl", 97: "R-Ctrl(한자)", 42: "L-Shift", 54: "R-Shift", 56: "L-Alt",
             100: "R-Alt(한/영)", 125: "L-Win", 126: "R-Win", 57: "Space"}
    if code in names:
        return names[code]
    try:
        from evdev import ecodes
        n = ecodes.KEY.get(code, f"KEY_{code}")
        return (n[0] if isinstance(n, list) else n).replace("KEY_", "")
    except ImportError:
        return f"KEY_{code}"

def hotkey_text(codes):
    return " + ".join(key_name(c) for c in codes)

STATE_TEXT = {"idle": "대기", "preparing": "페어링 준비 중 (블루투스 재시작)",
              "pairing": "페어링 대기 중", "finishing": "페어링 마무리 중"}

def summary(st):
    """One-line status for the tray menu / window header."""
    if not st.get("ok"):
        return "데몬 미실행" if st.get("daemon_down") else f"오류: {st.get('error')}"
    state = st.get("state", "idle")
    if state != "idle":
        extra = f" ({st.get('pair_remaining', 0)}초 남음)" if state == "pairing" else ""
        return STATE_TEXT.get(state, state) + extra
    if st.get("powered") is False:
        return "PC 블루투스 꺼짐 (페어링을 누르면 켜져요)"
    if not st.get("target"):
        return "페어링된 폰 없음"
    if st.get("relay"):
        return "입력 전달 중 (PC 입력 차단)"
    return "연결됨 — 입력 전달 꺼짐" if st.get("hid_connected") else "폰 연결 대기 중"

ICON_CHOICES = {   # first name present in the icon theme wins (Yaru/Humanity/Adwaita/Breeze differ)
    "error":     ["dialog-error", "dialog-error-symbolic", "image-missing"],
    "relay":     ["input-keyboard", "input-keyboard-symbolic", "preferences-desktop-keyboard"],
    "connected": ["bluetooth-active", "bluetooth-active-symbolic", "bluetooth", "network-wireless"],
    "idle":      ["bluetooth-disabled", "bluetooth-disabled-symbolic", "bluetooth", "network-offline"],
}

def icon_key(st):
    if not st.get("ok"): return "error"
    if st.get("relay"): return "relay"
    if st.get("hid_connected"): return "connected"
    return "idle"

def pick_icon(names, has_icon):
    return next((n for n in names if has_icon(n)), names[0])

def icon_for(st, has_icon=lambda n: True):
    return pick_icon(ICON_CHOICES[icon_key(st)], has_icon)


import gi
gi.require_version("Gtk", "3.0")
gi.require_version("AyatanaAppIndicator3", "0.1")
from gi.repository import Gtk, Gdk, GLib, AyatanaAppIndicator3 as AI

class Settings(Gtk.Window):
    def __init__(self, app):
        super().__init__(title="BT HID Relay 설정")
        self.app = app
        self.set_border_width(14); self.set_default_size(420, -1)
        self.connect("delete-event", lambda *a: self.hide() or True)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10); self.add(box)

        grid = Gtk.Grid(column_spacing=12, row_spacing=6); box.pack_start(grid, False, False, 0)
        self.vals = {}
        for i, (k, label) in enumerate([("status", "상태"), ("hid", "HID 연결"), ("relay", "입력 전달"),
                                        ("name", "기기 이름"), ("mac", "MAC 주소"),
                                        ("btconn", "블루투스 링크"), ("hotkey", "전달 단축키")]):
            l = Gtk.Label(label=label, xalign=0); l.get_style_context().add_class("dim-label")
            v = Gtk.Label(label="-", xalign=0, selectable=True)
            grid.attach(l, 0, i, 1, 1); grid.attach(v, 1, i, 1, 1); self.vals[k] = v

        box.pack_start(Gtk.Separator(), False, False, 4)
        self.btn_pair = Gtk.Button(label="신규 장비 페어링"); self.btn_pair.connect("clicked", self.on_pair)
        self.btn_remove = Gtk.Button(label="기존 장비 페어링 삭제"); self.btn_remove.connect("clicked", self.on_remove)
        self.btn_hotkey = Gtk.Button(label="relay 단축키 설정"); self.btn_hotkey.connect("clicked", self.on_hotkey)
        for b in (self.btn_pair, self.btn_remove, self.btn_hotkey):
            box.pack_start(b, False, False, 0)

    def refresh(self, st):
        yes = lambda b: "예" if b else "아니오"
        v = self.vals
        v["status"].set_text(summary(st))
        if not st.get("ok"):
            return
        v["hid"].set_text(yes(st["hid_connected"])); v["relay"].set_text("켜짐" if st["relay"] else "꺼짐")
        tgt = next((d for d in st["devices"] if d["mac"] == st["target"]), None)
        v["name"].set_text(tgt.get("name") or "-" if tgt else "-")
        v["mac"].set_text(st["target"] or "-")
        v["btconn"].set_text(yes(tgt.get("connected")) if tgt else "-")
        v["hotkey"].set_text(hotkey_text(st["hotkey"]))
        pairing = st["state"] != "idle"
        self.btn_pair.set_label("페어링 취소" if pairing else "신규 장비 페어링")
        self.btn_remove.set_sensitive(not pairing and bool(st["devices"]))
        self.btn_hotkey.set_sensitive(not pairing)

    def msg(self, text, kind=Gtk.MessageType.INFO, buttons=Gtk.ButtonsType.OK):
        d = Gtk.MessageDialog(transient_for=self, modal=True, message_type=kind,
                              buttons=buttons, text=text)
        r = d.run(); d.destroy(); return r

    def on_pair(self, _b):
        if self.app.last.get("state", "idle") != "idle":
            request("pair_cancel"); return
        if self.msg("PC를 2분간 검색 가능 상태로 만들어요.\n\n"
                    "• 페어링하는 동안 PC 블루투스 오디오(이어폰 등)가 잠시 끊겨요.\n"
                    "• 폰: 블루투스 → 이 PC 선택 → 페어링\n"
                    "• 통화/오디오 없이 '텍스트 입력'으로만 연결돼요.\n\n시작할까요?",
                    Gtk.MessageType.QUESTION, Gtk.ButtonsType.OK_CANCEL) != Gtk.ResponseType.OK:
            return
        # pair_start restarts bluetoothd (seconds) → run off the GTK thread
        self.btn_pair.set_sensitive(False)
        def work():
            r = request("pair_start", timeout=90)
            def done():
                self.btn_pair.set_sensitive(True)
                if not r.get("ok"):
                    self.msg(r.get("error", "실패"), Gtk.MessageType.ERROR)
            GLib.idle_add(done)
        threading.Thread(target=work, daemon=True).start()

    def on_remove(self, _b):
        devs = self.app.last.get("devices", [])
        d = Gtk.Dialog(title="페어링 삭제", transient_for=self, modal=True)
        d.add_buttons("취소", Gtk.ResponseType.CANCEL, "삭제", Gtk.ResponseType.OK)
        area = d.get_content_area(); area.set_spacing(6); area.set_border_width(10)
        area.pack_start(Gtk.Label(label="이 앱으로 페어링한 기기만 표시돼요.", xalign=0), False, False, 0)
        combo = Gtk.ComboBoxText()
        for dev in devs:
            combo.append(dev["mac"], f"{dev.get('name') or '(이름 없음)'}  {dev['mac']}")
        combo.set_active(0); area.pack_start(combo, False, False, 0); d.show_all()
        ok = d.run() == Gtk.ResponseType.OK; mac = combo.get_active_id(); d.destroy()
        if ok and mac:
            r = request("remove", mac=mac)
            self.msg("삭제했어요. 폰에서도 이 PC를 '등록 해제'해 주세요." if r.get("ok")
                     else r.get("error", "실패"),
                     Gtk.MessageType.INFO if r.get("ok") else Gtk.MessageType.ERROR)

    def on_hotkey(self, _b):
        d = Gtk.Dialog(title="relay 단축키 설정", transient_for=self, modal=True)
        d.add_buttons("다시 입력", 1, "취소", Gtk.ResponseType.CANCEL, "저장", Gtk.ResponseType.OK)
        area = d.get_content_area(); area.set_border_width(12); area.set_spacing(8)
        area.pack_start(Gtk.Label(label="새 단축키를 동시에 눌렀다 떼세요 (2~4키, Ctrl/Alt/Shift/Win 포함).\n"
                                        "단축키에 쓴 키는 폰으로 전달되지 않아요.", xalign=0), False, False, 0)
        shown = Gtk.Label(label="(입력 대기)"); area.pack_start(shown, False, False, 0)
        codes, down = [], set()
        def on_press(_w, ev):
            c = ev.hardware_keycode - 8          # X11/XKB keycode → evdev code
            if not down: codes.clear()
            down.add(c)
            if c not in codes: codes.append(c)
            shown.set_text(hotkey_text(codes)); return True
        def on_release(_w, ev):
            down.discard(ev.hardware_keycode - 8); return True
        d.connect("key-press-event", on_press); d.connect("key-release-event", on_release)
        d.show_all()
        request("capture", on=True)          # current hotkey must not toggle the relay meanwhile
        keep = GLib.timeout_add_seconds(20, lambda: bool(request("capture", on=True)) or True)  # daemon expires it after 60s
        try:
            while True:
                r = d.run()
                if r == 1:
                    codes.clear(); shown.set_text("(입력 대기)"); continue
                break
        finally:
            GLib.source_remove(keep)
            request("capture", on=False)
        d.destroy()
        if r == Gtk.ResponseType.OK and codes:
            res = request("set_hotkey", codes=list(codes))
            if not res.get("ok"):
                self.msg(res.get("error", "실패"), Gtk.MessageType.ERROR)

class App:
    def __init__(self):
        self.last = {}
        self.ind = AI.Indicator.new("bthid-tray", "bluetooth-disabled",
                                    AI.IndicatorCategory.APPLICATION_STATUS)
        self.ind.set_status(AI.IndicatorStatus.ACTIVE)
        self.ind.set_title("BT HID Relay")
        menu = Gtk.Menu()
        self.item_status = Gtk.MenuItem(label="..."); self.item_status.set_sensitive(False)
        item_open = Gtk.MenuItem(label="설정 열기"); item_open.connect("activate", self.open)
        item_off = Gtk.MenuItem(label="입력 전달 끄기"); item_off.connect("activate", lambda *_: request("relay_off"))
        item_quit = Gtk.MenuItem(label="트레이 종료"); item_quit.connect("activate", lambda *_: Gtk.main_quit())
        for it in (self.item_status, Gtk.SeparatorMenuItem(), item_open, item_off, item_quit):
            menu.append(it)
        menu.show_all(); self.ind.set_menu(menu)
        self.win = Settings(self)
        self.poll(); GLib.timeout_add_seconds(1, self.poll)

    def open(self, *_):
        self.win.refresh(request("status"))
        self.win.show_all(); self.win.present()

    def poll(self):
        st = request("status")
        self.last = st if st.get("ok") else {}
        self.item_status.set_label(summary(st))
        theme = Gtk.IconTheme.get_default()
        self.ind.set_icon_full(icon_for(st, theme.has_icon if theme else (lambda n: True)), summary(st))
        if self.win.get_visible():
            self.win.refresh(st)
        return True


def main():
    App()
    Gtk.main()

if __name__ == "__main__":
    main()
