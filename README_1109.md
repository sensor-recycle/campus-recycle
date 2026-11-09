# 🗑️ Campus Smart Recycling — 작업 인수인계 (11/9, 채현)

## 접속 방법

- File → Open Folder → `/home/pi/smart_bin`
- 터미널은 항상 `cd ~/smart_bin` 치고 시작
- 전원 끌 때: `sudo shutdown -h now` → 20초 → USB-C 뽑기. 전선 꽂을 땐 반드시 전원 끄고

## 환경

- 카메라 인식 완료, 촬영이 안됨.
- gpiozero·git 설치돼 있음. `hx711` 라이브러리는 `.whl` 파일로 설치 필요 (`pip install hx711-*.whl`)
- 와이파이: 학교망(SMU) 설정은 파일에 넣어뒀으나 안 붙음. 인터넷 필요하면 핫스팟 또는 노트북 인터넷 공유.

## 빵판 전원 줄 (⚠️ 섞지 말 것)

| 줄        | 전압 | Pi 핀 | 용도                             |
| --------- | ---- | ----- | -------------------------------- |
| 위 빨강   | 5V   | 2번   | 서보, 초음파 VCC                 |
| 위 파랑   | GND  | 6번   | 서보 GND                         |
| 아래 빨강 | 3.3V | 1번   | IR, HX711 등 센서                |
| 아래 파랑 | GND  | 9번   | 센서 GND, 트랜지스터 E, 어댑터 − |

## 핀 배정 (전부 고정, 바꾸지 말기)

| 부품                            | GPIO          | 물리 핀 | 상태      |
| ------------------------------- | ------------- | ------- | --------- |
| 서보 1 MG996R (분류판)          | GPIO18        | 12      | ✅        |
| 서보 2 (예비)                   | GPIO27        | 13      | 미연결    |
| IR TCRT5000 3핀 (투입 감지)     | GPIO17        | 11      | ✅        |
| 초음파 HC-SR04 TRIG             | GPIO23        | 16      | ✅        |
| 초음파 ECHO (1k+2k 분압 거침)   | GPIO24        | 18      | ✅        |
| LED 스트립 12V (2N2222 통해)    | GPIO22        | 15      | ✅        |
| 부저                            | GPIO25        | 22      | 미연결    |
| HX711 DT / SCK                  | GPIO5 / GPIO6 | 29 / 31 | 납땜 대기 |
| ADS1115 (수분센서용, 빌려야 함) | GPIO2 / GPIO3 | 3 / 5   | —         |
| 금속센서 (옵토커플러 통해)      | GPIO26        | 37      | —         |

## ✅ 오늘 된 것

- **서보** `servo_test.py` — 3방향 동작
- **IR** `ir_test.py` — 손 대면 감지
- **초음파** `ultra_test.py` — cm 출력. ECHO는 빵판 a10 → 1kΩ(b10↔b14) → 14번 줄 → 2kΩ(e14↔파란 줄), a14에서 Pi 18번
- **LED 스트립 12V** `light_test.py` — Pi가 켜고 끔. 2N2222 **가운데 다리 = B**. 20번 줄 GND, 21번 줄 저항 1kΩ(Pi 15번), 22번 줄 스트립 검정. 어댑터는 DC잭 터미널 블록: `+` → 스트립 빨강, `−` → 아래 파란 줄. 2N2222은 작아서 스트립 길게 쓰면 뜨거움 → 20~30cm만
- **통합** `check_all.py`(서보+IR), `check_all2.py`(서보+IR+초음파+LED) — IR 감지하면 LED 켜지고 서보 동작
- **흐름** `main.py` — IR 감지 → 종류 수동 입력 → 서보 배출 → `log.csv` 기록. `input()` 자리에 AI 판별 넣으면 됨

## ❌ 안 된 것

- **카메라 (OV5647 V1)**: Pi가 인식은 함 (`libcamera-hello --list-cameras`). 설정·방향·전원 OK. `Camera frontend has timed out` → 영상이 안 옴. 양쪽 케이블 재장착·저해상도 다 해봄. → **다른 리본 케이블로 교체** 또는 **모듈 위 소형 센서 커넥터 재장착**. 둘 다 안 되면 모듈 불량. 테스트: `libcamera-still -o test.jpg -t 2000`
- **로드셀**: HX711 **납땜 안 됨** (핀헤더 동봉, 6핀+4핀). 납땜 후: 로드셀 빨강→E+, 검정→E−, 흰색→A−, 초록→A+ / HX711 GND→아래 파랑, VCC→아래 빨강, DT→29번, SCK→31번. 코드 `weight_raw.py`(숫자 변하나), `weight.py`(보정). 보정 비율 나오면 여기 적기: `___`
- **수분센서**: 아날로그라 Pi 직접 못 읽음 → **ADC 모듈(ADS1115/MCP3008) 빌려야 함**
- **금속센서 SN04-N**: 12V 어댑터 이제 있음(LED용과 공용). PC817 옵토커플러 보드 거쳐서 GPIO26 — 아직 안 함

## 📂 파일

`servo_test.py` `ir_test.py` `ultra_test.py` `light_test.py` `check_all.py` `check_all2.py` `main.py` `camera.py` `weight_raw.py` `weight.py` `log.csv`
