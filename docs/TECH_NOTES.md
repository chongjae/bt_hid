# 기술 노트

## 구조

```
 [evdev 키보드/마우스] ──(grab)──▶ bthidd (root, systemd)
                                   ├─ 입력 스레드: 키·마우스 → HID 리포트, 단축키 처리
                                   ├─ L2CAP PSM 0x11(제어) / 0x13(데이터): 폰과 연결
                                   ├─ 재연결 스레드: PC → 폰 연결 (5초~30초 간격)
                                   ├─ GLib 메인 루프: BlueZ D-Bus(에이전트, HID 프로필), 제어 소켓, 1초 타이머
                                   └─ /run/bthid/ctl.sock  ◀── bthid-tray (사용자, GTK 트레이)
```

- **PC 쪽 역할:** HID 장치예요. 폰은 HID 호스트예요. bluetoothd의 `input` 플러그인은 꺼야 해요(`--noplugin=input`). 켜져 있으면 PSM 0x11/0x13을 bluetoothd가 차지해요.
- **SDP 등록:** BlueZ `ProfileManager1.RegisterProfile(UUID 0x1124, ServiceRecord=hid.xml)`으로 HID 레코드를 등록해요. 연결 소켓은 데몬이 직접 열어요.
- **D-Bus 처리:** 모든 D-Bus 호출은 메인 스레드에서만 해요. dbus-python은 여러 스레드에서 동시에 쓰면 안전하지 않아요.

## HID 리포트

- **키보드:** `A1 01 [modifier] 00 [key1..key6]`, report ID 1
- **마우스:** `A1 02 [buttons] [dx] [dy] [wheel]`, report ID 2. 이동량은 각각 ±127이에요.
- 마우스 이동은 evdev `SYN_REPORT` 단위로 모아서 한 번에 보내요. 큰 이동은 ±127씩 나눠 보내요.

## 꼭 지켜야 할 것 (실제로 겪은 함정)

1. **`hid.xml` 속성 번호 (HID 스펙)**
   - `0x0201` 파서 버전, `0x0202` 장치 서브클래스(0xC0), `0x0203` 국가 코드, `0x0204` VirtualCable, `0x0205` ReconnectInitiate, **`0x0206` HID 디스크립터 목록**, `0x0207` LANGID, `0x020B` 프로필 버전, `0x020C` 감시 타임아웃, `0x020D` NormallyConnectable, `0x020E` BootDevice
   - 번호가 하나라도 밀리면 Android가 `BTA_HH_ERR_SDP`를 내고 바로 끊어요.
2. **kr104 키 배열**
   - 한/영 = 오른쪽 Alt(evdev 100), 한자 = 오른쪽 Ctrl(97)이에요. 다른 배열에서는 `KEY_HANGEUL`(122)로 들어와요.
   - X/ibus에서는 키심볼이 바뀌어 보여서, 단축키를 기록할 때는 `hardware_keycode - 8`(evdev 코드)을 써요.
3. **오디오 항목 숨기기**
   - WirePlumber를 멈추면 HFP 항목은 사라져요. A2DP·AVRCP 항목은 bluetoothd 플러그인이 등록해서 남아요.
   - 그래서 페어링하는 동안만 런타임 drop-in `/run/systemd/system/bluetooth.service.d/zz-bthid-pair.conf`로 bluetoothd를 다시 띄워요.
4. **입력 잠김 방지 (가장 중요)**
   - 전달 켜기는 폰 연결이 있을 때만 돼요.
   - 전송이 실패하거나 1초 넘게 멈추면 전달을 끄고 grab을 풀어요.
   - 입력 스레드는 `finally`에서 grab을 풀고, 1초 타이머의 감시가 죽은 스레드를 다시 띄워요.
   - 프로세스가 죽으면 커널이 grab을 자동으로 풀어요.
   - 전달은 단축키를 *뗄 때* 켜요. 그래야 X가 키 뗌을 받아서 키가 눌린 채로 남지 않아요.
5. **단축키 자동 반복:** 키를 누른 그 순간(value 1)에만 전환해요. 단축키 키를 모두 떼야 다음 전환이 가능해져요.
6. **bluetoothd 재시작:** 에이전트와 프로필 등록이 사라져요. `org.bluez` 이름 소유자가 바뀌는 걸 감지해서 다시 등록해요.
7. **기본 에이전트:** GNOME 블루투스 설정 창이 기본 에이전트를 가져갈 수 있어요. 그래서 30초마다 `RequestDefaultAgent`를 다시 요청해요.

## 페어링 상태 흐름

```
idle ──pair_start──▶ preparing ──bluez up + 어댑터 준비──▶ pairing (120초 검색 가능)
  ▲                  (WirePlumber 정지, bluetoothd 오디오 플러그인 제외로 재시작,          │ 페어링 완료
  │                   300초 뒤 오디오 강제 복구 타이머)                                       ▼
  └──────── 원래 bluetoothd 재시작, WirePlumber 시작 ◀── finishing (8초: 폰의 SDP 조회 대기)
            (preparing 60초 / pairing 120초가 지나면 자동 종료)
```

## 제어 소켓 명령 (`/run/bthid/ctl.sock`, 한 줄 JSON)

| cmd | 설명 |
|---|---|
| `status` | 상태, HID 연결, 전달 여부, 대상, 기기 목록(이름·연결), 단축키 |
| `pair_start` / `pair_cancel` | 페어링 시작 / 취소 |
| `remove` `{mac}` | 이 앱으로 페어링한 기기만 삭제 |
| `set_hotkey` `{codes}` | 2~4개 키, 수정키 하나 포함 (evdev 코드) |
| `capture` `{on}` | 트레이가 단축키를 기록하는 중. 기존 단축키를 막아요 (60초 뒤 자동 해제) |
| `relay_off` | 비상용: 전달 끄기 + grab 해제 |

## 다른 PC 호환성

- **`bluetoothd` 경로:** 22.04는 `/usr/lib/bluetooth/bluetoothd`, 24.04는 `/usr/libexec/bluetooth/bluetoothd`예요(확인: 깨끗한 컨테이너). `install.sh`와 데몬이 둘 다 자동으로 찾아요.
- **파이썬:** `gi`, `dbus`, `evdev`는 apt 패키지로 깔려서 시스템 파이썬에만 있어요. 그래서 실행 파일 첫 줄, systemd 유닛, 자동 시작 항목 모두 `/usr/bin/python3`로 고정했어요. conda나 venv에서는 동작하지 않아요.
- **systemd 248 미만:** `--machine=user@`를 쓸 수 없어요. 그러면 페어링 중 WirePlumber 일시정지가 동작하지 않고, 페어링은 진행돼요.
- **PulseAudio 환경:** 페어링 중 통화 항목이 폰에 보일 수 있어요. 연결은 에이전트가 거절해요.
- **rfkill:** 데몬이 sysfs(`/sys/class/rfkill/*/soft`)로 직접 해제해서 `rfkill` 명령이 없어도 돼요.

## 디버깅

- **데몬 로그:** `journalctl -u bthidd -f`
- **폰 로그:** 무선 ADB로 연결해서 `logcat --pid=<com.android.bluetooth>`를 봐요. `bta_hh`, `btif_hh`, `hidh_` 태그가 HID 관련이에요.
- **btmon:** 5.72 버전은 텍스트로 출력하면 바로 죽어요. `btmon -w file.snoop`으로 저장해서 보세요.
