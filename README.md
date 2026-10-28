<div align="center">

<img src="assets/logo.png" width="112" height="112" alt="Modu Vision 로고" />

# Modu Vision (모두의 비전)

**이미지 준비부터 라벨링·학습·평가·검사까지 연결하는 비전 AI 스튜디오**

설치 앱으로, 또는 이 PC의 브라우저에서 사용합니다. 앱 이름은 **Vision AI Studio**입니다.

[![Linux 전체 회귀](https://github.com/JaeHongChoe/modu-vision/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/JaeHongChoe/modu-vision/actions/workflows/ci.yml)
[![Node.js 24](https://img.shields.io/badge/Node.js-24-339933)](package.json)
[![Python 3.13](https://img.shields.io/badge/Python-3.13-3776AB)](CONTRIBUTING.md)
[![Electron 44](https://img.shields.io/badge/Electron-44-47848F)](package-lock.json)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

[빠른 시작](#빠른-시작) · [6단계 작업 흐름](#작업-흐름) · [ChatGPT 도우미](#chatgpt-도우미) · [앱과 웹의 구조](#앱과-웹의-구조) · [주요 기능](#주요-기능) · [문서](#문서)

</div>

![이미지와 라벨을 준비하고 모델을 학습·평가한 뒤 검사 흐름을 저장·실행하는 6단계 개념도](assets/readme/studio-workflow.svg)

프로젝트 안에서 원본 이미지, 라벨, 학습 작업, 저장 모델과 검사 이력을 연결합니다. 화면의 **간단히 보기**는 지금 필요한 작업을 안내하고, **모두 보기**와 고급 편집에서는 세부 설정을 다룹니다. 도우미에 작업을 요청해 지원되는 준비·학습·평가 동작을 맡길 수도 있습니다.

현재 **0.1.0 개발 버전**입니다. 기능 구현과 자동 검사, 대상 OS의 설치·서명, 실제 장비·모델 품질의 확인 범위는 [배포 지원 표](docs/release-platform-matrix.md)와 [검증 근거](docs/service-upgrade-evidence.json)에서 구분합니다.

## 빠른 시작

| 사용 방식 | 시작 방법 | 준비할 것 |
| --- | --- | --- |
| **설치 앱** | 전달받은 설치 파일로 설치한 뒤 **Vision AI Studio** 실행 | 해당 OS·아키텍처의 설치본. Python 런타임은 앱에 포함됩니다. |
| **이 PC의 브라우저** | 소스 환경을 준비한 뒤 `npm run web` → [https://localhost:8443](https://localhost:8443) | Node·Python 의존성, 최초 로컬 계정 설정 |
| **소스 개발** | [개발 환경 설치](#개발-환경-설치) 후 `npm run dev` | Git, Node.js 24, Python 3.13 |

### 설치 앱으로 시작

설치 파일을 전달받았다면 일반 사용자는 아래의 개발 환경 설치를 먼저 할 필요가 없습니다. macOS arm64는 앱/DMG·ZIP, Windows x64는 NSIS, Linux x64는 AppImage·deb 빌드 경로가 있습니다. **빌드 경로와 실제 설치·서명·복구 확인은 구분**하며, 대상 환경의 조건은 [배포 지원 표](docs/release-platform-matrix.md)를 따릅니다. 설치본을 직접 만들려면 [데스크톱 패키징](#데스크톱-패키징)을 보세요.

앱을 연 뒤 **프로젝트 만들기·열기 → 데이터 준비**부터 시작합니다. 원본을 가져오고 클래스를 확인한 뒤 6단계를 따라 진행하세요.

### 브라우저로 시작

[개발 환경 설치](#개발-환경-설치)를 완료한 저장소의 터미널에서 실행합니다.

```bash
npm run web
```

첫 실행에는 **터미널에서 로컬 계정을 생성**하고, 열린 브라우저에서 그 계정으로 로그인합니다. 로컬 HTTPS 인증서 확인은 사용자가 직접 진행합니다. 사용 중에는 실행 터미널을 유지하세요.

이 실행기는 **해당 PC의 localhost용**입니다. LAN·외부 호스팅용 서버와는 구분됩니다. 현재 Windows 로컬 웹은 TLS 키 권한 검사가 준비되지 않아 실행을 거절하며, Linux 웹 런타임은 미검증입니다. 실행 옵션·지원 범위·데이터 보관 위치는 [로컬 브라우저 안내](docs/local-browser.md)를 확인하세요.

## 작업 흐름

Studio의 실제 화면 이름을 기준으로 한 **6단계**입니다.

| 단계 | 화면 | 여기서 하는 일 |
| --- | --- | --- |
| **01** | **데이터 준비** | 이미지·LabelMe 가져오기, 클래스 확인, 학습·검증·시험 분할 |
| **02** | **라벨 달기** | 도형·마스크 편집, AI 초안 검토, 라벨 저장·검수 |
| **03** | **모델 학습** | 모델과 실행 자원 선택, 학습 시작·상태·저장 결과 확인 |
| **04** | **결과 평가** | 모델 성능과 과검·미검 이미지 확인, 모델 비교·보고서 |
| **05** | **검사 흐름 만들기** | 모델과 판정 규칙 연결 → 흐름 저장 → 시험 이미지로 전체 흐름 확인 |
| **06** | **검사 실행** | 저장한 흐름으로 검사, 결과·검토 이력 확인, 패키지 내보내기 |

**4단계는 모델 자체를, 5단계는 전처리·모델·판정 규칙이 연결된 저장 흐름을 확인**합니다. 5단계의 기본 안내와 고급 편집은 [검사 흐름 준비 안내](docs/inspection-flow.md)를 보세요. 별도의 작업 센터에서는 실행 중인 작업과 저장 이력을, 운영자 화면에서는 반복 검사를 관리합니다.

<details>
<summary><strong>합성 프로젝트의 검사 준비 화면 보기</strong></summary>

![합성 자료를 사용한 5단계 검사 준비 화면 예시](docs/verification/assets/step5-guided-flow/preparation.png)

합성 자료를 사용한 화면 예시입니다. [촬영·검증 범위](docs/verification/2026-10-27-step5-guided-flow.md)를 함께 확인하세요.

</details>

## ChatGPT 도우미

“이 자료로 학습을 준비해줘”, “이 오류가 왜 뜨는지 확인해줘”처럼 요청합니다. 도우미는 현재 프로젝트·화면·작업 상태를 읽고 **지원되는 앱 동작을 계획해, 확인한 계획을 실행**합니다. 프로젝트 준비, 자료 가져오기·분할, 지원되는 라벨 초안·학습·평가·시험 검사·보고서 저장과 화면 이동을 맡길 수 있습니다.

**요청 작성 → 계획·입력값 검토 → 계획 확인하고 실행 → 결과 확인·필요한 사람 검토**

| 상황 | 다음 행동 |
| --- | --- |
| 폴더 선택이나 검토를 기다림 | 카드에 표시된 **폴더 선택하고 이어하기**, **검토 화면 열기**, **정답 확인 화면 열기** 사용 |
| 이전 계획 대신 다른 일을 맡김 | **이 계획 멈추고 새 요청**. 이전 기록과 작성 중인 질문은 보존되며, 질문을 자동 전송하지 않습니다. |
| 예전 진행·결과를 확인함 | **계획 기록 새로 보기**. 기록을 여는 것만으로 실행되지 않습니다. |
| 상태 조회가 오래 걸림 | **상태 확인 대기 중지**. 조회는 기본 15초 제한이며, 실행 중인 학습·평가를 취소하거나 재시도하지 않습니다. |
| 대화 내용만 정리함 | **대화만 비우기**. 저장된 계획은 유지됩니다. |

진행 중이거나 결과가 확인되지 않은 작업은 먼저 저장 결과를 확인하도록 안내합니다. **계획 계속·이어하기**는 저장 결과를 확인한 뒤 이미 승인한 남은 작업을 실행할 수 있습니다. AI 라벨은 검토 전까지 초안이며, **라벨 채택·정답 확정·최종 승인·배포는 사람이 결정**합니다. 새 작업 실행은 현재 macOS/Linux 경로이며 Windows 지원은 준비 중입니다.

ChatGPT 연결에는 **텍스트와 구조화된 앱 상태**를 제공하며, 이 연결은 원본 이미지·화면 캡처를 전송하지 않습니다. 로컬 웹 계정 로그인, 컴퓨터의 네이티브 기능 허용, ChatGPT 계정 연결은 각각 별개입니다. [도우미 사용·복구 안내](docs/assistant-tasks.md)에서 지원 동작과 확인 범위를 확인하세요.

## 앱과 웹의 구조

![설치 앱과 localhost 웹이 각각 Python 엔진을 사용하며 웹은 별도 Electron 보조 프로그램과 연결되는 구조도](assets/readme/studio-runtime.svg)

- **설치 앱:** React 화면과 Electron이 이 PC의 Python/FastAPI 엔진에 연결됩니다.
- **localhost 웹:** 브라우저가 로컬 HTTPS 서버에 연결되고, 창 없는 Electron 보조 프로그램이 폴더 창·파일 열기·OS 보호 저장소를 담당합니다.
- **저장 공간:** 설치 앱과 웹의 계정·프로젝트 저장 공간은 자동으로 합쳐지지 않습니다. 웹은 전용 폴더에 보관합니다.
- **원격 계산:** SSH 실행 프로필은 필요할 때 선택합니다. localhost 웹을 열었다고 외부 GPU나 서버가 자동으로 사용되지는 않습니다.

## 주요 기능

### 데이터와 라벨링

- 폴더·지원 LabelMe 형상 가져오기, 데이터셋 분할과 버전·원본 출처 관리
- BBox, 회전 BBox, polygon, brush·eraser와 자동 선택 도구
- 확대·이동·화면 맞춤과 픽셀 확인, 선택 형상 이동, 실행 취소·다시 실행
- 원본을 유지하는 파생 이미지 편집과 라벨 세트 관리
- 프로젝트별 라벨 기준서, 담당 배정·우선순위, 편집 잠금, 1인·2인 검수와 의견 조정
- 선택적 승인 입력 정책: 라벨·기준·정책이 바뀌면 이전 검수를 무효화하고 학습 입력의 출처 기록

### 10개 모델군

| 모델군 | 구현된 학습·처리 경로 |
| --- | --- |
| 이미지 분류 | DINOv3 기본 구조와 ResNet·ConvNeXt·EfficientNet 대체 구조 |
| 영역 분할 | DINOv3 기반 분할과 U-Net 경로 |
| 객체 검출 | YOLO 기본 구조와 Faster R-CNN 대체 경로 |
| 이상탐지 | PaDiM·PatchCore 통계 구성, DINOv3 합성 결함 학습 |
| 패치 분류 | 원본 해시·패치 좌표·클래스를 연결한 학습 |
| 문자 인식 | CTC 인식, 수평 문자 영역 제안, 다중 줄 정렬과 문자열 규칙 |
| 회전 객체 검출 | 회전 박스 모델과 명시적으로 가져온 로컬 YOLO OBB 가중치 경로 |
| 정방향 보정 | 정답 보정각을 이용한 각도 예측 |
| 이미지 개선 | 개선 전·후 정답 이미지 쌍을 이용한 학습 |
| 결함 이미지 생성 | 결함 crop GAN 학습, 후보 생성·검토·train 입력 채택 |

GAN은 생성 데이터를 검토해 학습에 채택하는 용도이며, 검사 플로우의 판정 모델로 사용하지 않습니다. OCR의 영역 제안은 고전적 수평 영역 분석 방식입니다. 모델군별 의존성·입력·실행 단계는 [모델 카탈로그](backend/engine/model_catalog.py)에 정의되어 있습니다.

### 학습과 평가

- 사용 가능한 CPU·Apple MPS·CUDA 자원과 SSH 원격 실행 프로필 선택
- 로컬·SSH 후보 탐색: 지원 recipe 안에서 후보 수·시간·자원 예산을 제한하는 AutoDL
- 학습 상태·loss·자원 사용량 표시, 작업 취소·재연결·재열기와 부모 모델 연결
- 호환성을 검사한 체크포인트에서 학습 재개, 준비 입력과 저장 모델의 식별자·해시 기록
- 이미지별 예측, 혼동 행렬과 과검·미검 사례, 같은 이미지 집합에 대한 두 모델 비교
- HTML·JSON 보고서 생성과 브라우저 인쇄를 통한 PDF 저장

직접 학습과 자동 후보 탐색의 지원 범위는 다릅니다. 원격 AutoDL은 단일 CPU/CUDA worker 경로이며, exact resume·다중 줄 OCR·네이티브 YOLO OBB 후보 등에는 제한이 있습니다. 학습 화면의 준비 검사와 선택한 recipe의 지원 범위를 확인하세요.

### 검사 플로우와 운영자 작업 공간

- 모델, ROI, 전처리, Blob 측정과 결과 집계를 연결하는 플로우 편집·저장·실행
- 운영자 검사 화면의 수동 입력, 프로젝트 폴더 감시, 카메라·RTSP 입력 경로
- 부품·제품·Lot와 재검사 연결, 모델 판정과 운영자 검수 분리
- 접수 시 검사 recipe 고정, 용량이 제한된 입력 대기열과 backpressure, 재처리·ACK 전송 재시도
- 검사 결과와 검토 이력 조회, CSV·JSON 내보내기

### 패키지와 외부 연동

- 그래프·모델·체크섬을 함께 묶는 전체 플로우 패키지와 패키지 보관함
- 앱과 별도로 실행하는 검사 서비스, 인증된 HTTP 작업 접수와 결과 내보내기
- Python·CLI·REST 연동, CPython 런타임을 사용하는 C ABI/C++·C# 래퍼
- 단계 적용·롤백, drift·sampling, 보존 정책·복구 가능한 휴지통과 진단 자료
- macOS LaunchAgent, Linux systemd user, Windows 로그온 작업을 통한 사용자 시작 등록
- 별도의 Windows SCM 서비스 사전 검사·등록 파일 준비·등록 경로. 관리자 권한과 서비스 계정 등 대상 환경의 사전 조건 필요

통합 방법은 [검사 서비스·SDK 안내](docs/api-integration.md)와 [학습 CLI·REST 안내](docs/training-engine-cli-rest.md)를 참고하세요.

## 개발 환경 설치

<details>
<summary><strong>소스 설치·개발 실행 명령 펼치기</strong></summary>

### 준비할 도구

**Git, Node.js 24, Python 3.13**을 준비합니다. 고정 버전은 **Python**: 3.13, **Node.js**: 24, **Electron**: 44입니다(Electron 44는 `package-lock.json`이 고정합니다). Windows·Linux 명령은 CPU용 해시 잠금 파일을 사용합니다. macOS arm64는 별도 잠금 파일이 없어 `requirements.txt`를 사용합니다. GPU 학습에는 장치에 맞는 드라이버와 Python 의존성 구성이 추가로 필요합니다.

### 1. 저장소 받기

```bash
git clone https://github.com/JaeHongChoe/modu-vision.git
cd modu-vision
```

### 2. 운영체제별 설치·실행

<details>
<summary><strong>Windows 11 x64 · PowerShell</strong></summary>

가상환경의 Python과 `npm.cmd`를 직접 사용합니다.

```powershell
py -3.13 -m venv .venv
.venv\Scripts\python.exe -m pip install --require-hashes --only-binary=:all: -r build/ci/requirements-windows-py313-cpu.lock
npm.cmd ci
$env:VISION_AI_PYTHON = (Resolve-Path .venv\Scripts\python.exe).Path
npm.cmd run dev
```

자세한 첫 실행·문제 해결 순서는 [Windows 빠른 시작](docs/quickstart-windows.md)을 확인하세요.

</details>

<details>
<summary><strong>macOS arm64 · zsh 또는 bash</strong></summary>

```bash
python3.13 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt pytest==8.3.4
npm ci
export VISION_AI_PYTHON="$PWD/.venv/bin/python"
npm run dev
```

</details>

<details>
<summary><strong>Linux x64 · bash</strong></summary>

```bash
python3.13 -m venv .venv
source .venv/bin/activate
python -m pip install --require-hashes --only-binary=:all: -r build/ci/requirements-ubuntu-py313-cpu.lock
npm ci
export VISION_AI_PYTHON="$PWD/.venv/bin/python"
npm run dev
```

</details>

`npm run dev`가 Vite 개발 서버, Electron 코드 감시와 데스크톱 앱·백엔드를 실행합니다. 백엔드 Python 코드를 바꿨다면 앱을 재시작하세요.

**사전학습 가중치는 소스와 기본 설치본에 포함되지 않습니다.** 해당 모델을 사용하려면 제공자에게서 내려받거나 사용자가 가져와야 합니다. 오프라인 사용에는 허용된 가중치와 선택적 런타임을 미리 준비해야 합니다. DICOM·OpenVINO·의미 기반 라벨링 등의 선택적 환경은 해당 기능의 준비 조건을 확인하세요.

### 3. CPU 데모

설치 후 작은 합성 이미지로 정방향 보정 모델을 학습·평가할 수 있습니다. GPU와 가중치 다운로드는 필요하지 않습니다.

```bash
python scripts/cpu_demo.py
```

Windows에서는 `python` 대신 `.venv\Scripts\python.exe`를 사용합니다. 결과를 보존하려면 `--keep` 또는 `--workdir 경로`를 지정하세요. 데모 데이터와 작업 기록은 격리된 작업 폴더에 저장됩니다.

</details>

## 개발 검사

변경한 영역의 백엔드 테스트와 타입·화면 검사를 먼저 실행합니다.

```bash
python -m pytest backend/tests/test_service_s1_04.py -q
npm run typecheck
npm run test:renderer
npm run build
```

화면 검사는 `npm run test:e2e:browser`, 데스크톱 검사는 `npm run test:e2e:electron`입니다. Playwright 브라우저와 테스트용 Python 설정, 회귀 범위는 [기여 안내](CONTRIBUTING.md)에 있습니다. Windows에서는 `npm.cmd`와 가상환경 Python을 사용하세요.

## 데스크톱 패키징

**대상 OS·아키텍처에서 frozen 백엔드를 먼저 빌드한 뒤 Electron 패키지를 만듭니다.** 패키지 빌더는 백엔드의 실행·체크섬 기록과 소스 일치를 검사합니다. 다른 플랫폼의 백엔드 빌드 결과를 재사용할 수 없습니다.

빠른 시작의 의존성을 설치한 환경에 PyInstaller를 추가합니다. macOS 패키징에는 Xcode Command Line Tools(macOS SDK·clang), Linux 패키징에는 C 컴파일러와 해당 Python 개발 헤더가 필요합니다.

**macOS arm64**

```bash
.venv/bin/python -m pip install pyinstaller
.venv/bin/python scripts/build_backend_binary.py
npm run package:mac
```

**Windows x64 · PowerShell**

```powershell
.venv\Scripts\python.exe -m pip install --no-deps --require-hashes --only-binary=:all: -r build/ci/requirements-windows-packaging.lock
.venv\Scripts\python.exe scripts/build_backend_binary.py
npm.cmd run package:win
```

출력은 `release/`에 생성됩니다. macOS는 DMG·ZIP, Windows는 NSIS 설정을 사용하며 Linux x64의 AppImage·deb 빌드 경로도 있습니다. 설치본의 백엔드 Python 런타임은 번들에 포함됩니다. 플랫폼별 서명, 설치·제거·복구, 오프라인 준비 절차는 [배포 지원 표](docs/release-platform-matrix.md)를 따릅니다.

macOS 기본 빌더 설정은 `mac.identity: "-"`이며 실제 산출물의 서명 상태는 빌드 환경과 지정한 인증서에 따라 확인해야 합니다. 빌드 뒤 대상 `.app`에서 `codesign --verify --deep --strict`와 `codesign --display --verbose=4`의 검증 결과·서명 종류·TeamIdentifier를 보관하세요. ad-hoc 서명은 배포자 확인이나 공증을 뜻하지 않습니다. 다운로드한 DMG/ZIP의 Gatekeeper 동작과 설치·첫 실행은 대상 macOS에서 별도로 확인해야 합니다. `backend_bin`은 `backend-release.json`의 체크섬과 연결돼 패키징 재서명 대상에서 제외합니다. Developer ID 배포는 셸과 frozen 백엔드를 같은 배포자 신원으로 준비하고, 백엔드 서명 후 inventory/acceptance를 다시 만드는 절차가 필요합니다. 이후 바이트가 바뀌면 기존 검증 기록을 재사용할 수 없습니다. 서명·공증·설치/제거·복구의 최종 확인은 [배포 지원 표](docs/release-platform-matrix.md)를 따릅니다.

## 라벨링 단축키

캔버스에 포커스가 있을 때 사용합니다. 자동 선택과 100% 배율은 도구 모음에서 선택합니다.

| 키 | 동작 |
| --- | --- |
| `1` ~ `6` | 선택, BBox, 회전 BBox, polygon, brush, eraser 순서로 전환 |
| `F` | 이미지 화면 맞춤 |
| `Space` + 드래그 | 캔버스 이동 |
| 방향키 / `Shift` + 방향키 | 선택 형상 1px / 10px 이동 |
| `Ctrl` / `Cmd` + `Z` | 실행 취소 |
| `Ctrl` / `Cmd` + `Shift` + `Z` 또는 `Ctrl` / `Cmd` + `Y` | 다시 실행 |
| `Delete` / `Backspace` | 선택 라벨 삭제 |
| `Enter` / `Esc` | polygon 완성 / 현재 편집 취소 |

## 프로젝트 구조

| 경로 | 역할 |
| --- | --- |
| `src/main/`, `src/preload/` | Electron 실행, 백엔드 프로세스 관리와 데스크톱 bridge |
| `src/renderer/` | React Studio·운영자 화면과 상태 관리 |
| `backend/api/`, `backend/contracts/` | REST·WebSocket API, 인증·프로젝트 문맥과 실행 계약 |
| `backend/engine/` | 데이터·학습·평가·플로우 엔진과 독립 검사 서비스 |
| `backend/remote/` | SSH 원격 실행 조정자와 worker |
| `native_runtime/`, `examples/` | 외부 프로그램 연동 래퍼와 예제 |
| `backend/tests/`, `scripts/e2e/` | 백엔드 회귀와 브라우저·Electron 검사 |
| `build/`, `scripts/` | 패키징 설정, 의존성 잠금 파일과 개발·검증 도구 |
| `docs/` | 사용·연동·배포 안내와 기능별 구현·검증 기록 |

## 문서

| 문서 | 내용 |
| --- | --- |
| [로컬 브라우저 실행](docs/local-browser.md) | localhost 실행, 계정·저장 공간, 네이티브 기능 허용 |
| [말로 작업을 맡기는 도우미](docs/assistant-tasks.md) | 작업 계획, 실행·진단, 사람 확인 대기와 복구 |
| [검사 흐름 준비](docs/inspection-flow.md) | 5단계 기본 준비 순서와 고급 편집 |
| [기여 안내](CONTRIBUTING.md) | 새 환경 설치, CPU 데모, 테스트와 지원 범위 |
| [Windows 빠른 시작](docs/quickstart-windows.md) | Windows 설치·첫 실행·진단 |
| [문제 해결](docs/troubleshooting.md) | 오류 재현과 진단 자료 |
| [코드 읽기·리팩토링 검토](docs/CODE_GUIDE.md) | 핵심 진입점, 상태 구분과 구조 개선 우선순위 |
| [검사 서비스·SDK](docs/api-integration.md) | 전체 플로우 패키지, Python·CLI·REST·C++·C# 연동 |
| [학습 CLI·REST](docs/training-engine-cli-rest.md) | 외부 프로그램에서 학습 엔진 사용 |
| [배포 지원 표](docs/release-platform-matrix.md) | 네이티브 빌드, 서명, 설치와 복구 조건 |
| [모델 라이선스 표](docs/model-license-matrix.md) | 사전학습·파생 모델과 가중치 배포 조건 |
| [작업 계획](docs/service-upgrade-program.json) · [검증 근거](docs/service-upgrade-evidence.json) | 기능별 범위와 확인 상태 |
| [팀 작업 흐름](docs/implementation-ledger/TEAM-WORKFLOW.md) · [배포 기록](docs/implementation-ledger/TEAM-DELIVERY.md) | 영역별 구현과 남은 확인 사항 |

## 기여와 라이선스

버그 제보와 변경 제안은 [Issues](https://github.com/JaeHongChoe/modu-vision/issues), 코드 기여는 [기여 안내](CONTRIBUTING.md)를 이용하세요. 보안 문제 제보 방법은 [SECURITY.md](SECURITY.md)에 있습니다.

프로젝트 코드는 [MIT License](LICENSE)를 따릅니다. 외부 라이브러리와 모델 가중치에는 각각의 조건이 적용됩니다. 특히 DINOv3·YOLO 등 사전학습 모델과 파생 모델의 이용·재배포 조건은 [모델 라이선스 표](docs/model-license-matrix.md)와 [외부 구성 요소 고지](THIRD_PARTY_NOTICES.md)를 확인하세요.
