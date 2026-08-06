# Raspberry Pi I2C / SPI / GPIO Control

라즈베리파이 주변장치를 GUI로 제어하는 PyQt5 애플리케이션입니다.
STM32의 `DAC + I2C + SPI + TIM` 제어 GUI와 같은 구성을 라즈베리파이 환경에 맞춰 옮긴 것으로,
버스 트랜잭션·핀 상태·파형 출력을 한 화면에서 다루고 모든 동작을 하단 로그 콘솔에 남깁니다.

```
python3 main.py
```

## 화면 구성

| 탭 | 기능 |
|---|---|
| **I2C** | 버스 열기/닫기, `i2cdetect` 방식 주소 스캔(0x03~0x77), 레지스터 R/W, Raw 전송 |
| **SPI** | 장치 열기/닫기, 클럭·모드(0~3)·비트 순서 설정, 전이중 전송 및 쓰기 전용 전송 |
| **GPIO** | 40핀 헤더 뷰, 입/출력 설정, 풀업·풀다운, HIGH/LOW/토글, 입력 실시간 감시(200ms) |
| **PWM** | 2채널 소프트웨어 PWM, 주파수·듀티 실시간 조정, 프리셋(LED/서보/부저) |
| **DAC** | 외부 12비트 DAC 수동 출력 및 파형 생성(Sine/Triangle/Sawtooth/Square/DC) |
| **Sequence** | I2C·SPI·GPIO·PWM·DAC 명령을 표로 쌓아 순차 실행, 저장/불러오기 |

하단 로그 콘솔은 모든 TX/RX와 상태 변화를 타임스탬프와 함께 기록하며 파일로 저장할 수 있습니다.
`도움말 → 40핀 핀맵 보기`에서 `pin map/pinmaip.png`를 확대해 볼 수 있습니다.

## Sequence 탭

각 행이 명령 한 개입니다. 표에 쌓아 두고 `시퀀스 실행`을 누르면 위에서 아래로 실행합니다.

| 열 | 의미 |
|---|---|
| 설명 | 사람이 읽을 이름 (실행에는 영향 없음) |
| 모드 | 수행할 동작 (아래 표) |
| 명령 | 쉼표로 구분한 인자. 선택한 모드의 형식이 하단 `명령 형식`과 입력칸 힌트에 표시됩니다 |
| 읽기 길이 | 읽기 모드에서만 활성화 |
| 지연(ms) | 이 단계 실행 후 대기 시간 |
| 결과 | 성공은 초록, 실패는 빨강으로 표시 |
| 사용 | 체크 해제 시 건너뜀 |
| 실행 | 그 단계만 즉시 실행 |
| 반복 | 그 단계의 반복 횟수 |

하단 `반복`은 시퀀스 전체 사이클 수입니다. `오류 시 중단`을 끄면 실패해도 계속 진행합니다.
실행 중에는 다른 탭이 잠깁니다(같은 버스를 동시에 건드리지 않기 위해서입니다).
`저장`/`불러오기`로 시퀀스를 JSON 파일에 보관할 수 있습니다.

### 모드와 명령 형식

| 모드 | 명령 | 비고 |
|---|---|---|
| `I2C Write` | `0x62, 40 00` | 데이터는 16진 |
| `I2C Read` | `0x62` | 읽기 길이 사용 |
| `I2C Reg Write` | `0x48, 0x01, 84 83` | 주소, 레지스터, 데이터 |
| `I2C Reg Read` | `0x48, 0x00` | 읽기 길이 사용 |
| `I2C Scan` | (없음) | 응답한 주소 목록 반환 |
| `SPI Transfer` | `30 80` | 전이중, MISO 반환 |
| `SPI Write` | `30 80` | 쓰기 전용 |
| `GPIO Mode` | `17, out` / `27, in, up` | 풀은 up/down/none |
| `GPIO Write` | `17, 1` | 미설정 핀은 자동으로 출력 확보 |
| `GPIO Read` | `27` | 미설정 핀은 자동으로 입력 확보 |
| `GPIO Toggle` | `17` | |
| `PWM Start` | `12, 1000, 50` | 핀, Hz, 듀티% |
| `PWM Stop` | `12` | |
| `DAC Write I2C` | `0x62, 2048` | MCP4725 |
| `DAC Read I2C` | `0x62` | DAC/EEPROM 현재값 |
| `DAC Write SPI` | `0, 2048` | 채널(0=A, 1=B), 값 |
| `Delay` | (없음) | 지연 열만 사용 |

핀 번호와 주파수는 10진수, 바이트 데이터는 16진수로 해석합니다.
`0x` 접두사를 붙이면 어디서든 16진수로 읽습니다.

> 실행 전에 해당 탭에서 버스를 먼저 열어야 합니다.
> I2C 클럭은 실행 중 변경할 수 없습니다 — `config.txt`의
> `dtparam=i2c_arm_baudrate=400000` 처럼 설정한 뒤 재부팅해야 하며,
> 현재 값은 `버스 정보`에 표시됩니다.

## 준비

### 1. 인터페이스 활성화

`/boot/firmware/config.txt`에서 다음 두 줄의 주석을 해제한 뒤 **재부팅**합니다.

```
dtparam=i2c_arm=on
dtparam=spi=on
```

`sudo raspi-config` → `Interface Options`로도 동일하게 설정할 수 있습니다.
활성화되면 `/dev/i2c-1`과 `/dev/spidev0.0`, `/dev/spidev0.1`이 생깁니다.

> `/dev/i2c-20`, `/dev/i2c-21`은 HDMI 모니터 EDID용 DDC 버스입니다.
> 주변장치가 붙는 버스가 아니므로 GUI에서 `(HDMI DDC)`로 표시합니다.

### 2. 권한

사용자가 `i2c`, `spi`, `gpio` 그룹에 속해 있어야 합니다.

```bash
sudo usermod -aG i2c,spi,gpio $USER   # 적용하려면 재로그인
```

### 3. 패키지

라즈베리파이 OS(Bookworm)에서는 apt 패키지로 모두 제공됩니다.

```bash
sudo apt install python3-pyqt5 python3-smbus2 python3-spidev \
                 python3-libgpiod python3-lgpio
```

## 하드웨어 연결

### SPI0

| 신호 | GPIO | 물리 핀 |
|---|---|---|
| MOSI | GPIO10 | 19 |
| MISO | GPIO9 | 21 |
| SCLK | GPIO11 | 23 |
| CE0 | GPIO8 | 24 |
| CE1 | GPIO7 | 26 |

### I2C1

| 신호 | GPIO | 물리 핀 |
|---|---|---|
| SDA | GPIO2 | 3 |
| SCL | GPIO3 | 5 |

### DAC

라즈베리파이에는 내장 DAC가 없어 외부 12비트 DAC를 사용합니다.

- **MCP4725** — I2C, 기본 주소 `0x62`(A0=High면 `0x63`), DAC 값을 EEPROM에 저장 가능
- **MCP4921 / MCP4922** — SPI, 4922는 A/B 2채널

DAC 탭의 `Vref` 값은 실제 기준 전압과 맞춰야 전압 표시가 정확합니다.

## 시뮬레이션 모드

각 탭의 `시뮬레이션 모드`를 켜면 장치 노드 없이도 GUI 전체를 사용할 수 있습니다.
인터페이스를 아직 켜지 않았거나 하드웨어가 없을 때 유용합니다.

- I2C — `0x62` MCP4725(DAC 레지스터·EEPROM 동작 모델), `0x48` ADS1115, `0x68` DS3231.
  등록되지 않은 주소에 접근하면 실제와 같이 NACK 오류가 납니다.
- SPI — MISO가 MOSI의 비트 반전값으로 응답합니다.
- GPIO — 핀 상태를 메모리에 유지합니다.

## 구조

```
main.py                 진입점
core/                   하드웨어 접근 계층 (UI 비의존)
  context.py            I2C/SPI/GPIO 핸들 공유
  i2c_bus.py            smbus2 래퍼 + 시뮬레이션
  spi_bus.py            spidev 래퍼 + 시뮬레이션
  gpio_ctrl.py          libgpiod v2 입출력 + lgpio PWM
  sim_devices.py        가상 I2C 슬레이브 모델
  dac.py                MCP4725 / MCP49x1 드라이버
  waveform.py           파형 테이블 생성 + 출력 스레드
  sequence.py           시퀀스 모드 정의 + 실행 스레드
  pinmap.py             40핀 헤더 정의
  logbus.py             전역 로그 시그널
  util.py               바이트/숫자 파싱
ui/                     PyQt5 화면
  main_window.py        탭 + 로그 콘솔 + 상태 표시줄
  tab_*.py              탭별 화면
  widgets.py            공용 위젯 (파형 미리보기 등)
  style.py              다크 테마 QSS
pin map/pinmaip.png     40핀 핀맵 이미지
```

## 구현 참고 사항

### GPIO 백엔드가 두 개인 이유

- **입출력·풀 저항**은 `libgpiod v2`(`gpiod`)로 처리합니다.
  현재 라즈베리파이 커널에서 `lgpio`가 사용하는 GPIO v1 uAPI 경로는
  `BIAS_PULL_UP` 요청을 거부합니다(`xGpioHandleRequest: Invalid argument`).
  풀다운과 bias-disable은 되지만 풀업만 실패하므로, 풀 설정이 필요한 경로는 v2를 씁니다.
- **PWM**은 `lgpio.tx_pwm`을 씁니다. libgpiod에는 대응 기능이 없습니다.

한 핀은 둘 중 한쪽만 점유합니다. PWM을 시작하면 gpiod 점유를 해제하고 lgpio로 넘기며,
정지하면 다시 gpiod 출력으로 되돌립니다.

### 파형 출력

STM32의 `TIM → DAC → DMA` 경로에 해당하는 부분을 파이썬 스레드로 구현했습니다.
한 주기를 256포인트 테이블로 만들어 `time.perf_counter()` 기준으로 송출하고,
`TABLE_LEN × 주파수`가 `최대 레이트`를 넘으면 포인트 수를 줄여 출력 주파수를 유지합니다.
버스가 못 따라가면 누적 지연을 재동기화하며, 화면의 `실제 S/s` 표시로 확인할 수 있습니다.

리눅스는 실시간 OS가 아니므로 수백 Hz 이상에서는 지터가 있습니다.
정밀한 파형이 필요하면 DAC 내장 파형 생성 기능이나 별도 MCU를 쓰는 편이 낫습니다.
