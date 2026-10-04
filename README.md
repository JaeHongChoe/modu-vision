<div align="center">

<img src="assets/logo.png" width="128" height="128" alt="Modu Vision Logo" style="border-radius: 28px; box-shadow: 0 8px 24px rgba(0,0,0,0.4);" />

# 👁️ Modu Vision (모두의 비전)
### 데스크톱 비전 AI 학습·검사 스튜디오
**데이터, 라벨, 학습, 평가, 저장된 검사 플로우와 이력을 연결하는 개발 프로젝트**

<br />

[![macOS](https://img.shields.io/badge/macOS-arm64%20%7C%20MPS-000000?style=for-the-badge)](#-빠른-시작-quick-start)
[![Windows](https://img.shields.io/badge/Windows-11%20x64%20%7C%20CUDA%20%7C%20CPU-0078D6?style=for-the-badge)](#-빠른-시작-quick-start)
[![Electron](https://img.shields.io/badge/Electron-44-47848F?style=for-the-badge&logo=electron&logoColor=white)](https://electronjs.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.142-009688?style=for-the-badge&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![PyTorch](https://img.shields.io/badge/PyTorch-2.9-EE4C2C?style=for-the-badge&logo=pytorch&logoColor=white)](https://pytorch.org)
[![TypeScript](https://img.shields.io/badge/TypeScript-5.7-3178C6?style=for-the-badge&logo=typescript&logoColor=white)](https://www.typescriptlang.org)
[![TailwindCSS](https://img.shields.io/badge/TailwindCSS-3.4-38B2AC?style=for-the-badge&logo=tailwind-css&logoColor=white)](https://tailwindcss.com)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg?style=for-the-badge)](LICENSE)

<br />

[✨ 핵심 기능](#-핵심-기능-key-features) •
[🧭 작업 흐름](#-작업-흐름-workflow) •
[🏗 아키텍처](#-시스템-아키텍처-system-architecture) •
[⚡ 빠른 시작](#-빠른-시작-quick-start) •
[📦 패키징](#-데스크톱-패키징-desktop-packaging)

<br />

---


</div>

<br />

## 💡 프로젝트 소개 (Overview)

**Modu Vision(모두의 비전)**은 이미지 가져오기, 라벨링, 모델 학습·평가, 저장된 검사 플로우와 검사 이력을 연결하는 Electron/FastAPI 데스크톱 앱입니다. 10개 모델군의 준비·작업·평가 경로와 프로젝트별 팀 라벨 기준·검수 정책을 제공합니다. Fast/Precision 설정과 제한된 후보·초매개변수 자동 탐색을 지원하며, 자동 탐색은 현재 로컬 실행 전용입니다.

저장된 플로우는 단일·병렬 모델과 ROI, Blob 측정, 결과 집계를 실행할 수 있습니다. 전체 플로우 패키지와 별도 프로세스 검사 서비스도 구현돼 있습니다. 현재 QA는 제한된 실제 이미지와 CPU/한 원격 GPU 환경의 **기능 동작**을 확인한 범위입니다. 독립 검증된 정상(OK) 코호트, 현장 장비 신호, 연속 운전·택트시간, 모델 품질과 운영 승인은 별도로 필요합니다. 범위별 근거는 [기능 상태표](docs/FEATURE_STATUS_2026-09-30.md)와 [Phase 0~4 QA](docs/QA_WORKFLOW_PHASES_0_4_2026-09-30.md)에 있습니다.

<br />

## ✨ 핵심 기능 (Key Features)

### 🔍 1. 고해상도 이미지 라벨링 캔버스
* **화면 영역 렌더링**: 원본 이미지의 보이는 영역을 캔버스에 그려 확대·이동한다. 파일 크기와 장치별 프레임률은 별도 측정이 필요하다.
* **픽셀 확인 모드**: 3배 이상 확대 시 스무딩을 꺼 원본 픽셀 경계를 확인할 수 있다. 표시 방식이 1픽셀 결함의 검출 품질을 보장하지는 않는다.

### 🏷️ 2. LabelMe 가져오기와 라벨링 도구
* **LabelMe 연동**: 인접 JSON의 지원 형상을 가져오고 프로젝트별 Studio 라벨로 편집한다. 모든 LabelMe 변형의 호환성은 검증하지 않았다.
* **캔버스 도구**: 선택/이동, bbox, 회전 bbox, polygon, brush, eraser, Auto-Selector를 제공한다. Auto-Selector의 실제 이미지 클릭은 일부 확인했으며 학습형 대량 Auto-Labeling과 구분한다.
* **형상 변환기**: 지원 형상 사이의 변환 기능이 있다. 변환 결과는 검토 후 저장한다.
* **트랙패드와 마우스**:
  - Mac 트랙패드 핀치 줌 (`Math.exp(-deltaY * 0.008)`) & 두 손가락 2D 캔버스 부드러운 패닝.
  - 마우스 휠 커서 기준 줌 및 스페이스바 팬(Pan) 지원.

### 🤖 3. 모델 학습·평가와 자원 표시
* **분류·분할·패치 분류**: 기본 사전학습 구조는 DINOv3이다. 공식 체크포인트 가져오기, 클래스·원본 해시를 연결한 준비 데이터와 CPU/MPS/CUDA 학습 경로를 제공한다.
* **검출·이상탐지**: 검출 기본 구조는 YOLO이며 Faster R-CNN 대체 경로도 있다. 이상탐지는 PaDiM/PatchCore 통계 재구성과 DINOv3 합성 결함 학습을 구분한다.
* **전문 모델군**: 단일 행 OCR, 회전 객체 검출, 학습형 정방향 보정, 이미지 개선, 결함 크롭 GAN을 지원한다. 명시적 정답과 원본 해시를 사용하며, GAN은 후보 생성·검토·채택에 사용한다. 검사 모델과 전처리 모델은 호환 플로우 노드에 전달할 수 있다.
* **로컬·서버 학습과 재학습 후보**: 10개 모델군에서 선택한 실행 자원과 설정·부모 모델을 유지한다. 실행 작업 ID와 저장 모델 ID를 구분해 취소·재열기·평가·플로우 연결을 처리한다. 사전 준비 검사는 의존성·가중치·입력·부모를 확인하며 실제 학습이나 품질 승인을 대신하지 않는다.
* **팀 데이터 품질**: 라벨 기준서 버전·예시, 담당 배정·우선순위, 공동 편집 권한, 1인/2인 검수와 의견 불일치 조정을 제공한다. 승인 데이터만 학습하는 정책은 선택적으로 활성화한다. 라벨·기준·정책 변경 시 이전 검수는 무효화되며 학습 출처에 기준·정책·입력 집합의 해시를 남긴다.
* **학습 상태**: loss와 CPU·메모리·GPU 메모리 등의 텔레메트리를 표시한다. 전력 소비량과 학습 FPS의 실측 보장은 없다.
* **장치 선택**: 사용 가능한 환경에서 CPU/MPS/CUDA 경로를 사용한다. OS·장치 조합별 검증 범위는 다르다.

### 🛡️ 4. 판정 검토와 보고서
* **임계값 검토**: 평가 데이터의 판정 변화를 확인할 수 있다. 정상·불량을 대표하는 별도 검증 집합 없이 유출 0%나 과검률을 보장할 수 없다.
* **이미지별 평가·모델 비교**: 혼동 행렬과 오검·미검 사례, 두 완료 모델의 같은 이미지 판정 차이를 확인한다.
* **보고서**: 독립 HTML·JSON 보고서를 생성한다. HTML은 브라우저에서 인쇄해 PDF로 저장할 수 있으며 직접 PDF 파일을 생성하는 API는 없다.

<br />

---

## 🧭 작업 흐름 (Workflow)

| 화면 | 주요 동작 |
| --- | --- |
| 1. 데이터 | 폴더·LabelMe 가져오기, 분할, 버전, 합성 후보 |
| 2. 라벨링 | 도형·브러시 편집, 라벨 세트, 기준서·배정·편집 잠금·검수 |
| 3. 학습 | 모델별 준비·실행 자원·부모 선택, 승인 입력 정책, 상태·취소·재열기 |
| 4. 평가 | 이미지별 결과, 모델 비교, 보고서와 승인 검토 |
| 5. 플로우 | 모델·ROI·측정·집계 연결, 검증, 저장 버전 |
| 6. 검사 | 저장된 플로우 실행, 검토 이력, CSV/JSON·패키지 |

최근 변경과 실제 확인 범위는 [팀 작업·전체 흐름 변경 기록](docs/implementation-ledger/TEAM-WORKFLOW.md)에 기록한다. 이전 시점의 실행 근거는 [기능 상태표](docs/FEATURE_STATUS_2026-09-30.md)에 남긴다.

<br />

---

## 🏗 시스템 아키텍처 (System Architecture)

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                 Desktop Client Layer (Electron 44 + React 18)               │
│   ┌───────────────────────┐ ┌────────────────────────┐ ┌────────────────┐  │
│   │ 6-Stage Studio UI     │ │ Labeling Canvas        │ │ Zustand Stores │  │
│   │ (Data → Inference)    │ │ (Base/Mask/Vector)     │ │ (Sync State)   │  │
│   └───────────────────────┘ └────────────────────────┘ └────────────────┘  │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │ IPC / WebSocket Telemetry
┌──────────────────────────────────────▼──────────────────────────────────────┐
│             Backend Process Supervisor (supervisor.ts)                      │
│   ┌───────────────────────────────────┐ ┌────────────────────────────────┐  │
│   │ Compiled backend, if present      │ │ Resolved Python environment    │  │
│   │ (separate build artifact)         │ │ (dependencies required)        │  │
│   └───────────────────────────────────┘ └────────────────────────────────┘  │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │ REST / WebSocket (Port 0 Ephemeral)
┌──────────────────────────────────────▼──────────────────────────────────────┐
│                    FastAPI Backend Daemon & AI Engine                       │
│   ┌───────────────────────┐ ┌────────────────────────┐ ┌────────────────┐  │
│   │ Dataset / Annotations │ │ PyTorch Models Engine  │ │ Saved Flow DAG │  │
│   │ (LabelMe Import)      │ │ (DINOv3, YOLO, etc.)   │ │ & Audit Trail  │  │
│   └───────────────────────┘ └────────────────────────┘ └────────────────┘  │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │ Hardware Direct Compute
┌──────────────────────────────────────▼──────────────────────────────────────┐
│       Metal / MPS               │  CUDA              │  CPU               │
└─────────────────────────────────────────────────────────────────────────────┘
```

전체 플로우 패키지의 [독립 검사 서비스](backend/engine/inspection_service.py)는 데스크톱 앱과 별도 프로세스로 실행한다. 저장된 파일·HTTP 작업과 이력을 처리하지만 PLC 판정 신호 또는 양산 지연시간 보증은 포함하지 않는다.

<br />

---

## ⚡ 빠른 시작 (Quick Start)

### 필수 요구 조건
* **Python**: 3.13 (고정 잠금 파일이 3.13용)
* **Node.js**: 24
* **OS**: Windows 11 x64(주 대상), macOS arm64(개발 환경). 지원 범위와 현재 근거는 [기여 안내](CONTRIBUTING.md#8-지원-범위-support-matrix)에 있다.

### 저장소 복제 및 설치
운영체제별 명령(Windows PowerShell, Linux, macOS)은 [기여 안내의 1단계](CONTRIBUTING.md#1-새-pc에서-시작하기-fresh-checkout)에 있다. Linux x64 예:

```bash
git clone https://github.com/JaeHongChoe/modu-vision.git
cd modu-vision
python3.13 -m venv .venv
source .venv/bin/activate
python -m pip install --require-hashes --only-binary=:all: -r build/ci/requirements-ubuntu-py313-cpu.lock
npm ci
```

### 개발 모드 실행
```bash
# UI와 백엔드 데몬을 동시에 핫 리로드 모드로 실행
npm run dev
```

<br />

---

## 📦 데스크톱 패키징 (Desktop Packaging)

`package:mac`과 `package:win`은 Electron 패키지를 만든다. 패키지는 같은 플랫폼에서 먼저 빌드한 frozen 백엔드(`backend_bin`)와 그 체크섬 목록을 담으며, 대상 PC에 Python 패키지를 설치하지 않는다(빌드·검증 절차는 [docs/release-platform-matrix.md](docs/release-platform-matrix.md)). 서명, 설치본 검증, 실제 장비 실행은 플랫폼별 근거가 따로 필요하다.

먼저 같은 플랫폼에서 가상환경의 Python으로 frozen 백엔드를 빌드한 뒤 Electron 패키지를 만든다. 패키지 빌더는 같은 OS·아키텍처에서 실행 검증을 마친 백엔드 빌드가 없으면 멈춘다.

### 🍎 macOS 애플리케이션 번들 (.app)
```bash
.venv/bin/python scripts/build_backend_binary.py
npm run package:mac
# 출력 위치: release/ 아래의 macOS 산출물
```

### 🪟 Windows 설치 파일 (NSIS, PowerShell)
```powershell
.venv\Scripts\python.exe scripts\build_backend_binary.py
npm.cmd run package:win
# 출력 위치: release/ 아래의 NSIS 산출물
```

서명, 설치본 설치·제거 검증, 대상 Windows PC에서의 첫 실행은 따로 확인해야 한다.

프로젝트 열기 전에 스키마 호환성을 검사하며, 기존 스키마 정규화는 원본 바이트 백업과 해시 연결 기록을 남긴다. 배포 화면은 실제 데스크톱 버전·서명 상태와 사용자가 설정한 HTTPS 업데이트 채널을 표시한다. 검증된 파일의 수동 다운로드를 지원하며 자동 설치는 지원하지 않는다. 검사 서비스의 사용자 시작 등록은 macOS LaunchAgent, Linux systemd user, Windows logon task를 제공한다. 실제 서명·배포 채널과 OS별 재로그인·재부팅 검증은 별도로 필요하다. 상세 근거는 [배포·호환성 기록](docs/implementation-ledger/TEAM-DELIVERY.md)에 있다.

<br />

---

## ⌨️ 키보드 & 마우스 단축키 일람 (Cheatsheet)

| 단축키 | 동작 | 설명 |
| :---: | :--- | :--- |
| `1` ~ `7` | **도구 전환** | 1: 선택, 2: BBox, 3: OBB, 4: 폴리곤, 5: 브러시, 6: 지우개, 7: 오토 셀렉터 |
| `트랙패드 핀치` | **커서 중심 스무스 줌** | 두 손가락으로 모으거나 벌려 부드럽게 지수 확대/축소 |
| `트랙패드 2손가락` | **2D 캔버스 팬(Pan)** | 마우스 없이 두 손가락 드래그로 상하좌우 화면 이동 |
| `Space` + `드래그` | **화면 패닝 (Pan)** | 스페이스바를 누른 채 마우스 좌클릭 드래그로 캔버스 이동 |
| `F` | **화면 맞춤 (Fit)** | 현재 이미지를 화면 정중앙에 꽉 차게 자동 배율 조정 |
| `1` (넘버패드/메뉴) | **100% 배율 (1:1)** | 이미지의 실제 1픽셀을 모니터 1픽셀에 1:1로 원본 매핑 |
| `방향키 (↑, ↓, ←, →)` | **1px 서브픽셀 넛지** | 선택된 BBox/폴리곤을 1픽셀 단위로 정밀 미세 이동 |
| `Shift` + `방향키` | **10px 단위 고속 넛지** | 선택된 어노테이션을 10픽셀 단위로 이동 |
| `Delete` / `Backspace` | **삭제** | 선택된 결함 어노테이션 제거 |
| `Ctrl` / `Cmd` + `Z` | **실행 취소 (Undo)** | 최대 40단계 히스토리 롤백 |
| `Enter` | **폴리곤 완성** | 다각형 그리기 중 시작점으로 닫기 |
| `Esc` | **작업 취소** | 현재 드래프팅 취소 및 선택 해제 |

<br />

---

## 🧪 테스트 무결성 검증 (Verification Suite)

코드 변경 후 실행할 주요 검사. 테스트 통과는 앱 설치, 현장 장비, 모델 품질 승인을 뜻하지 않는다.

```bash
# 백엔드 회귀
python -m pytest backend/tests -q

# TypeScript 검사와 빌드
npm run typecheck
npm run build

# 패키징 규칙 검사 (실제 설치·첫 실행과 별개)
node scripts/verify-packaging.js
```

<br />

---

## 📁 프로젝트 레이아웃 (Repository Layout)

```
modu-vision/
├── 📂 assets/                     # 앱 로고
├── 📂 backend/                    # Python 3 / FastAPI 백엔드 데몬 및 PyTorch 엔진
│   ├── 📂 api/                    # REST 엔드포인트 및 WebSocket 원격 측정
│   ├── 📂 engine/                 # PyTorch 모델, 저장 플로우, 독립 서비스 및 합성기
│   ├── 📂 utils/                  # 2개 국어 에러 카탈로그 및 공통 유틸리티
│   └── 📄 pytest.ini              # 백엔드 테스트 명세서
├── 📂 src/                        # 프론트엔드 UI 및 Electron 소스
│   ├── 📂 main/                   # Electron 메인 프로세스 & 프로세스 감시자(Supervisor)
│   ├── 📂 preload/                # 보안 Context Bridge IPC 프리로드
│   └── 📂 renderer/               # React 18 / TailwindCSS 산업용 UI 컴포넌트
├── 📂 build/                      # 윈도우/맥 패키징 명세, 아이콘 및 인스톨러 스크립트
│   ├── 📄 electron-builder.yml    # macOS DMG 및 Windows NSIS 크로스 패키징 스펙
│   ├── 📄 tsconfig.node.json      # Electron 메인 프로세스 빌드 설정
│   └── 📄 installer.nsh           # 윈도우 무인 자동 설치 스크립트
├── 📂 scripts/                    # 자동화 부트스트랩, 바이너리 컴파일러, 검증 스크립트
├── 📂 tests/                      # E2E 적대적 테스트 및 단위 테스트 스위트
├── 📄 requirements.txt            # 파이썬 의존성 명세서
└── 📄 package.json                # Node.js 프로젝트 명세서
```

<br />

---

## 🤝 기여하기 (Contributing)

버그 제보, 기능 제안 및 풀 리퀘스트(PR)는 언제나 환영합니다.

- [기여 안내](CONTRIBUTING.md): 새 PC 설치(CI와 같은 잠금 파일), GPU 없는 CPU 데모(`python scripts/cpu_demo.py`), 집중 테스트, PR 절차, 모듈 지도, 코드 규칙, 지원 범위와 릴리스 정책
- [Windows 빠른 시작](docs/quickstart-windows.md) · [문제 해결과 진단 자료](docs/troubleshooting.md) · [SDK·REST·배치 연동](docs/api-integration.md)
- [보안 정책](SECURITY.md): 취약점은 공개 이슈가 아닌 비공개 신고로 알려 주세요. 실제 검사 이미지나 비밀정보는 첨부하지 않습니다.
- [행동 강령](CODE_OF_CONDUCT.md)

<br />

---

## 📄 라이선스 (License)

This project is licensed under the **MIT License** - see the [LICENSE](LICENSE) file for details.

<div align="center">
  <sub>Built with ❤️ by the Modu Vision Open Source Team.</sub>
</div>
