# BT HID Relay — PC 키보드·마우스를 폰으로

Ubuntu PC의 키보드와 마우스 입력을 블루투스로 Android 폰(S25 Edge)에 보내는 프로그램이에요.
PC가 **블루투스 키보드·마우스처럼 동작**하고, 폰은 이 PC를 **"텍스트 입력"(입력 장치)으로만** 연결해요. 통화·오디오로는 연결되지 않아요.

고장 난 EasyBlue 동글(ZARAM)을 대신하려고 만들었어요. 동글 없이 PC에 이미 달린 블루투스 어댑터만 써요.

## 상태 (2026-10-15)

| 항목 | 상태 |
|---|---|
| 기능 개발 | 완료 |
| 단위 테스트 | 80개 통과, 커버리지 데몬 90% / 트레이 97% / 진단 93% |
| 독립 검증 | 통과 (구현자와 다른 검증 에이전트) |
| 이 PC 설치 | 완료 (`bthidd` 서비스 동작 중) |
| 실기기 테스트 | 로그로 확인한 것: 페어링 성공, 통화 연결 거절, HID 연결, 전달 On/Off. 폰 화면에 "텍스트 입력"만 보이는지는 직접 확인 필요 |

## 폴더 구성

```
~/bt_hid/
├── README.md            ← 지금 이 문서
├── app/                 ← 실제 프로그램 (설치 대상)
│   ├── bthidd.py        데몬. root, systemd 서비스
│   ├── bthid_tray.py    우측 상단 트레이와 설정 창
│   ├── bthid_doctor.py  의존성·환경 자가 진단 (bthid-doctor)
│   ├── hid.xml          폰에 알려 주는 키보드·마우스 장치 정보 (SDP 레코드)
│   ├── bthidd.service   systemd 유닛
│   ├── bthid-tray.desktop  트레이 자동 시작 항목
│   ├── install.sh / uninstall.sh
│   └── tests/test_bthid.py
├── docs/
│   ├── USAGE.md         설치, 사용법, 문제 해결
│   ├── WORK_LOG.md      전체 작업 기록 (동글 조사 → 블루투스 방식 → 프로그램화)
│   └── TECH_NOTES.md    동작 원리, 설계, 함정
└── legacy/              개발 중에 쓴 실험 스크립트 (참고용, 설치 안 됨)
```

## 빠른 시작

```bash
sudo ~/bt_hid/app/install.sh     # 설치 (한 번만)
bthid-tray &                     # 트레이 실행 (다음 로그인부터는 자동으로 떠요)
bthid-doctor                     # 의존성·환경 자가 진단 (문제가 있을 때)
```

다른 PC에 설치해도 필요한 패키지는 `install.sh`가 자동으로 설치해요. Ubuntu 22.04와 24.04를 지원해요.

1. 트레이 아이콘을 누르고 "설정 열기" → **신규 장비 페어링**을 눌러요.
2. 폰에서 블루투스를 켜고 이 PC를 골라 페어링해요.
3. **오른쪽 Ctrl + 오른쪽 Alt**를 눌렀다 떼면 입력 전달이 켜지고, 다시 누르면 꺼져요.

자세한 내용은 [docs/USAGE.md](docs/USAGE.md)에 있어요.
