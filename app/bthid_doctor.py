#!/usr/bin/python3
"""bthid-doctor — BT HID Relay 의존성·환경 자가 진단 (표준 라이브러리만 사용).

usage: bthid-doctor          설치 후 전체 진단
       bthid-doctor --pre    설치 전 진단 (설치 결과물 검사는 건너뜀)
종료 코드: FAIL이 하나라도 있으면 1
"""
import glob, os, re, shutil, socket, subprocess, sys

SYS_PY = "/usr/bin/python3"
APT_PKGS = ["python3-gi", "python3-dbus", "python3-evdev", "gir1.2-glib-2.0", "gir1.2-gtk-3.0",
            "gir1.2-ayatanaappindicator3-0.1", "bluez"]
GNOME_PKGS = ["gnome-shell-extension-appindicator"]
BLUETOOTHD_CANDIDATES = ["/usr/libexec/bluetooth/bluetoothd", "/usr/lib/bluetooth/bluetoothd",
                         "/usr/sbin/bluetoothd"]
PY_CHECK = r'''
import socket
socket.AF_BLUETOOTH, socket.BTPROTO_L2CAP
import gi
gi.require_version("Gtk", "3.0"); gi.require_version("Gdk", "3.0")
gi.require_version("AyatanaAppIndicator3", "0.1")
from gi.repository import Gtk, Gdk, GLib, AyatanaAppIndicator3
import dbus, dbus.mainloop.glib, dbus.service
import evdev
print("ok")
'''
PY_MODULE_HINT = {
    "AF_BLUETOOTH": "파이썬이 블루투스 소켓 없이 빌드됨 → 배포판 기본 /usr/bin/python3 사용",
    "gi": "python3-gi", "Gtk": "gir1.2-gtk-3.0", "Gdk": "gir1.2-gtk-3.0",
    "AyatanaAppIndicator3": "gir1.2-ayatanaappindicator3-0.1", "dbus": "python3-dbus",
    "evdev": "python3-evdev",
}

def run(cmd, **kw):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=15, **kw)
    except (OSError, subprocess.TimeoutExpired) as e:
        return subprocess.CompletedProcess(cmd, 127, "", str(e))

def find_bluetoothd():
    return next((p for p in BLUETOOTHD_CANDIDATES if os.access(p, os.X_OK)), None)

class Report:
    def __init__(self): self.rows = []
    def add(self, level, name, detail=""): self.rows.append((level, name, detail))
    def ok(self, n, d=""): self.add("OK", n, d)
    def warn(self, n, d=""): self.add("WARN", n, d)
    def fail(self, n, d=""): self.add("FAIL", n, d)
    @property
    def failed(self): return any(l == "FAIL" for l, _, _ in self.rows)
    def print(self):
        for level, name, detail in self.rows:
            print(f"[{level:4}] {name}" + (f" — {detail}" if detail else ""))
        n = {k: sum(1 for l, _, _ in self.rows if l == k) for k in ("OK", "WARN", "FAIL")}
        print(f"\n결과: OK {n['OK']} / WARN {n['WARN']} / FAIL {n['FAIL']}")

def os_release(path="/etc/os-release"):
    info = {}
    try:
        for line in open(path):
            k, _, v = line.strip().partition("=")
            info[k] = v.strip('"')
    except OSError:
        pass
    return info

def check_os(r, info=None):
    info = info if info is not None else os_release()
    name, ver = info.get("ID", "?"), info.get("VERSION_ID", "?")
    try:
        new_enough = float(ver) >= 22.04
    except ValueError:
        new_enough = False
    if name == "ubuntu" and new_enough:
        r.ok("운영체제", f"Ubuntu {ver}")
    else:
        r.warn("운영체제", f"{info.get('PRETTY_NAME', name)} — Ubuntu 22.04+에서만 확인됨")

def check_python(r):
    if not os.access(SYS_PY, os.X_OK):
        r.fail("시스템 파이썬", f"{SYS_PY} 없음 → sudo apt install python3"); return
    res = run([SYS_PY, "-c", PY_CHECK])
    if res.returncode == 0 and "ok" in res.stdout:
        r.ok("파이썬 모듈", "gi(Gtk/Gdk/GLib/AyatanaAppIndicator3), dbus, evdev, AF_BLUETOOTH")
    else:
        err = (res.stderr.strip().splitlines() or ["?"])[-1]
        hint = next((h for k, h in PY_MODULE_HINT.items() if k in err), "sudo ./install.sh 재실행")
        r.fail("파이썬 모듈", f"{err} → {hint}")
    env_py = shutil.which("python3")
    if env_py and os.path.realpath(env_py) != os.path.realpath(SYS_PY):
        r.warn("PATH의 python3", f"{env_py} (conda/venv?) — 프로그램은 {SYS_PY}로만 실행되므로 무관, "
                                  f"직접 'python3 bthid_tray.py'로 띄우면 gi 오류 남")

def is_gnome():
    return "GNOME" in os.environ.get("XDG_CURRENT_DESKTOP", "").upper() or bool(shutil.which("gnome-shell"))

def missing_packages(pkgs):
    out = []
    for p in pkgs:
        res = run(["dpkg-query", "-W", "-f=${Status}", p])
        if "ok installed" not in res.stdout:
            out.append(p)
    return out

def check_packages(r):
    if not shutil.which("dpkg-query"):
        r.warn("apt 패키지", "dpkg 없음 (Debian/Ubuntu 계열 아님) — 파이썬 모듈 검사 결과를 보세요"); return
    pkgs = APT_PKGS + (GNOME_PKGS if is_gnome() else [])
    miss = missing_packages(pkgs)
    if miss:
        r.fail("apt 패키지", "없음: " + " ".join(miss) + " → sudo apt install " + " ".join(miss))
    else:
        r.ok("apt 패키지", f"{len(pkgs)}개 설치됨")

def check_adapter(r, sysfs="/sys/class/bluetooth", rfkill="/sys/class/rfkill"):
    hcis = [os.path.basename(p) for p in glob.glob(os.path.join(sysfs, "hci*")) if ":" not in os.path.basename(p)]
    if not hcis:
        r.fail("블루투스 어댑터", "없음 — USB 동글/내장 블루투스 확인 (lsusb, dmesg | grep -i bluetooth)")
    else:
        r.ok("블루투스 어댑터", ", ".join(sorted(hcis)))
    for d in glob.glob(os.path.join(rfkill, "rfkill*")):
        try:
            if open(os.path.join(d, "type")).read().strip() != "bluetooth":
                continue
            hard = open(os.path.join(d, "hard")).read().strip() == "1"
            soft = open(os.path.join(d, "soft")).read().strip() == "1"
        except OSError:
            continue
        if hard:
            r.fail("rfkill", f"{os.path.basename(d)} 하드웨어 차단 — 노트북 무선 스위치/BIOS 확인")
        elif soft:
            r.warn("rfkill", f"{os.path.basename(d)} 소프트 차단(블루투스 꺼짐) — 페어링 시 자동 해제, 또는 설정에서 블루투스 켜기")

def systemd_version():
    res = run(["systemctl", "--version"])
    m = re.search(r"systemd (\d+)", res.stdout)
    return int(m.group(1)) if m else 0

def check_bluez(r, installed):
    btd = find_bluetoothd()
    if not btd:
        r.fail("bluetoothd", "실행 파일 없음 → sudo apt install bluez"); return
    res = run([btd, "--version"])
    ver = res.stdout.strip()
    try:
        old = tuple(int(x) for x in ver.split(".")[:2]) < (5, 50)
    except ValueError:
        old = False
    (r.warn if old else r.ok)("bluetoothd", f"{btd} {ver}" + (" — 5.50 이상 권장" if old else ""))
    active = run(["systemctl", "is-active", "bluetooth"]).stdout.strip()
    (r.ok if active == "active" else r.fail)("bluetooth.service", active or "?")
    if installed:
        exec_start = run(["systemctl", "show", "-p", "ExecStart", "--value", "bluetooth"]).stdout
        if "--noplugin=input" in exec_start:
            r.ok("bluetoothd 옵션", "--noplugin=input")
        else:
            r.fail("bluetoothd 옵션", "--noplugin=input 미적용 → sudo ./install.sh 재실행")
    sv = systemd_version()
    if sv >= 248:
        r.ok("systemd", str(sv))
    else:
        r.warn("systemd", f"{sv} — 248 미만: 페어링 중 오디오 일시정지/자동복구 기능이 동작하지 않을 수 있어요")

def check_audio(r):
    if shutil.which("wireplumber"):
        r.ok("오디오 스택", "PipeWire/WirePlumber")
    elif shutil.which("pulseaudio"):
        r.warn("오디오 스택", "PulseAudio — 페어링 시 통화 항목이 폰에 보일 수 있음 (연결은 PC가 거절)")
    else:
        r.ok("오디오 스택", "블루투스 오디오 서비스 없음")

def check_installed(r, lib="/usr/local/lib/bthid", sock="/run/bthid/ctl.sock"):
    missing = [f for f in ("bthidd.py", "bthid_tray.py", "hid.xml") if not os.path.exists(os.path.join(lib, f))]
    if missing:
        r.fail("설치 파일", f"{lib}에 없음: {', '.join(missing)} → sudo ./install.sh"); return
    r.ok("설치 파일", lib)
    active = run(["systemctl", "is-active", "bthidd"]).stdout.strip()
    if active == "active":
        r.ok("bthidd 서비스", "active")
    else:
        r.fail("bthidd 서비스", f"{active or '?'} → journalctl -u bthidd -n 50")
    try:
        with socket.socket(socket.AF_UNIX) as s:
            s.settimeout(2); s.connect(sock)
            s.sendall(b'{"cmd": "status"}\n')
            reply = s.makefile().readline()
        (r.ok if '"ok": true' in reply else r.warn)("제어 소켓", sock)
    except PermissionError:
        r.fail("제어 소켓", f"{sock} 권한 없음 — 설치한 사용자로 실행했는지 확인 (/etc/bthid/config.json의 user)")
    except OSError as e:
        r.fail("제어 소켓", f"{sock} 연결 실패: {e}")

def check_desktop(r):
    if os.geteuid() == 0:
        return   # tray/session checks only make sense as the desktop user
    sess = os.environ.get("XDG_SESSION_TYPE", "?")
    if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
        r.warn("데스크톱 세션", "그래픽 세션 밖에서 실행 중 — 트레이 검사는 데스크톱 터미널에서 하세요"); return
    r.ok("데스크톱 세션", f"{os.environ.get('XDG_CURRENT_DESKTOP', '?')} / {sess}")
    if is_gnome() and shutil.which("gnome-extensions"):
        enabled = run(["gnome-extensions", "list", "--enabled"]).stdout
        if "appindicator" in enabled.lower():
            r.ok("GNOME 트레이 확장", "AppIndicator 켜짐")
        else:
            r.fail("GNOME 트레이 확장", "꺼짐 → gnome-extensions enable ubuntu-appindicators@ubuntu.com "
                                     "(안 되면 로그아웃 후 다시 로그인)")
    tray = run(["pgrep", "-f", "bthid_tray.py"]).stdout.strip()
    (r.ok if tray else r.warn)("트레이 실행", "실행 중" if tray else "안 떠 있음 → bthid-tray &")

def main(argv=sys.argv[1:]):
    pre = "--pre" in argv
    r = Report()
    check_os(r); check_python(r); check_packages(r); check_adapter(r); check_bluez(r, installed=not pre)
    check_audio(r)
    if not pre:
        check_installed(r); check_desktop(r)
    r.print()
    return 1 if r.failed else 0

if __name__ == "__main__":
    sys.exit(main())
