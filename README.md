<div align="center">

<img src="assets/logo.png" width="128" height="128" alt="Modu Vision Logo" style="border-radius: 28px; box-shadow: 0 8px 24px rgba(0,0,0,0.4);" />

# 👁️ Modu Vision (모두의 비전)
### 데스크톱 비전 AI 학습·검사 스튜디오
**데이터, 라벨, 학습, 평가, 저장된 검사 플로우와 이력을 연결하는 개발 프로젝트**

<br />

[![macOS](https://img.shields.io/badge/macOS-Apple%20Silicon%20(MPS)-000000?style=for-the-badge&logo=apple&logoColor=white)](https://apple.com)
[![Windows](https://img.shields.io/badge/Windows-10%2F11%20(CUDA%20%7C%20CPU)-0078D6?style=for-the-badge&logo=windows&logoColor=white)](https://microsoft.com)
[![Electron](https://img.shields.io/badge/Electron-33.x-47848F?style=for-the-badge&logo=electron&logoColor=white)](https://electronjs.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688?style=for-the-badge&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![PyTorch](https://img.shields.io/badge/PyTorch-2.4+-EE4C2C?style=for-the-badge&logo=pytorch&logoColor=white)](https://pytorch.org)
[![TypeScript](https://img.shields.io/badge/TypeScript-5.7-3178C6?style=for-the-badge&logo=typescript&logoColor=white)](https://www.typescriptlang.org)
[![TailwindCSS](https://img.shields.io/badge/TailwindCSS-3.4-38B2AC?style=for-the-badge&logo=tailwind-css&logoColor=white)](https://tailwindcss.com)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg?style=for-the-badge)](LICENSE)

<br />

[✨ 핵심 기능](#-핵심-기능-key-features) •
[📸 스튜디오 갤러리](#-스튜디오-갤러리-studio-gallery) •
[🏗 아키텍처](#-시스템-아키텍처-system-architecture) •
[⚡ 빠른 시작](#-빠른-시작-quick-start) •
[📦 패키징](#-데스크톱-패키징-desktop-packaging)

<br />

---

### 🖥️ Main Screen Preview
<img src="assets/screenshots/02_canvas_labeling.png" width="95%" alt="Modu Vision Main Canvas" style="border-radius: 12px; border: 1px solid #2B3547; box-shadow: 0 16px 36px rgba(0,0,0,0.6);" />

*이미지 라벨링 화면 예시*

---

</div>

<br />

## 💡 프로젝트 소개 (Overview)

**Modu Vision(모두의 비전)**은 이미지 가져오기, 라벨링, PyTorch 모델 학습·평가, 저장된 검사 플로우, 검사 이력을 연결하는 Electron/FastAPI 데스크톱 앱입니다. 분류·검출·분할·이상탐지와 출처 연결 Patch Classification 경로가 있습니다. 학습은 Fast/Precision 고정 설정으로 시작하며 구조·초매개변수 자동 탐색을 제공하지 않습니다.

저장된 플로우는 단일·병렬 모델과 ROI, Blob 측정, 결과 집계를 실행할 수 있습니다. 전체 플로우 패키지와 별도 프로세스 검사 서비스도 구현돼 있습니다. 현재 QA는 제한된 실제 이미지와 CPU/한 원격 GPU 환경의 **기능 동작**을 확인한 범위입니다. 독립 검증된 정상(OK) 코호트, 현장 장비 신호, 연속 운전·택트시간, 모델 품질과 운영 승인은 별도로 필요합니다. 범위별 근거는 [기능 비교표](docs/NEUROT_PARITY_MATRIX_2026-09-29.md)와 [Phase 0~4 QA](docs/QA_NEUROT_PHASE_0_4_2026-09-30.md)에 있습니다.

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
* **분류·검출·분할·이상탐지**: Fast/Precision preset에서 ResNet/ConvNeXt, Faster R-CNN, U-Net, PaDiM/PatchCore 경로를 선택한다. YOLO와 DeepLabV3+ 학습 경로는 없다.
* **Patch Classification**: 출처 이미지·좌표·라벨·해시를 기록한 patch manifest로 학습·평가하고 ROI에서 패치별 결과를 얻는다. 원격 패치 학습은 지원하지 않는다.
* **실험 모델군**: 단일 행 OCR, 회전 객체 검출, 결함 크롭 GAN은 명시적 정답·영역과 원본 해시를 사용한다. 3단계에서 후보를 학습·평가하거나 생성 이미지를 미리 볼 수 있다. 이 후보가 5단계 범용 플로우나 현장 서비스에 자동 배포되지는 않는다.
* **로컬 재학습 후보**: 완료 모델의 구조·클래스·체크포인트 해시를 확인해 가중치를 이어 학습하고 부모 계보를 남긴다. 기존 성능 보존 여부와 실제 승격은 별도의 OK/NG 검증·승인이 필요하다.
* **학습 상태**: loss와 CPU·메모리·GPU 메모리 등의 텔레메트리를 표시한다. 전력 소비량과 학습 FPS의 실측 보장은 없다.
* **장치 선택**: 사용 가능한 환경에서 CPU/MPS/CUDA 경로를 사용한다. OS·장치 조합별 검증 범위는 다르다.

### 🛡️ 4. 판정 검토와 보고서
* **임계값 검토**: 평가 데이터의 판정 변화를 확인할 수 있다. 정상·불량을 대표하는 별도 검증 집합 없이 유출 0%나 과검률을 보장할 수 없다.
* **이미지별 평가·모델 비교**: 혼동 행렬과 오검·미검 사례, 두 완료 모델의 같은 이미지 판정 차이를 확인한다.
* **보고서**: 독립 HTML·JSON 보고서를 생성한다. HTML은 브라우저에서 인쇄해 PDF로 저장할 수 있으며 직접 PDF 파일을 생성하는 API는 없다.

<br />

---

## 📸 스튜디오 갤러리 (Studio Gallery)

<table align="center" width="100%">
  <tr>
    <td width="50%" align="center">
      <b>Stage 1: 데이터 스튜디오 & 인공 결함 합성기</b><br />
      <img src="assets/screenshots/01_data_studio.png" width="100%" alt="Data Studio" />
      <p align="left"><sub>• 로컬 폴더 대량 로드, LabelMe 동기화, 절차적 인공 결함(균열, 쇼트, 기포) 자동 생성</sub></p>
    </td>
    <td width="50%" align="center">
      <b>Stage 2: 3-Layer 정밀 라벨링 캔버스</b><br />
      <img src="assets/screenshots/02_canvas_labeling.png" width="100%" alt="Labeling Canvas" />
      <p align="left"><sub>• bbox·회전 bbox·polygon·brush 등 라벨 도구와 확대 화면의 원본 픽셀 확인</sub></p>
    </td>
  </tr>
  <tr>
    <td width="50%" align="center">
      <b>Stage 3: 모델 학습 & 하드웨어 텔레메트리</b><br />
      <img src="assets/screenshots/03_automl_training.png" width="100%" alt="AutoML Training" />
      <p align="left"><sub>• 4대 기본 작업과 Patch Classification, Fast/Precision preset, loss·메모리 표시</sub></p>
    </td>
    <td width="50%" align="center">
      <b>Stage 4: 평가·판정 검토</b><br />
      <img src="assets/screenshots/04_zero_escape_eval.png" width="100%" alt="Evaluation Studio" />
      <p align="left"><sub>• 이미지별 결과, 모델 비교, HTML/JSON 보고서. 성능 승인은 대표 OK/NG 자료가 필요</sub></p>
    </td>
  </tr>
  <tr>
    <td width="50%" align="center">
      <b>Stage 5: 다단계 검사 플로우차트 DAG 파이프라인</b><br />
      <img src="assets/screenshots/05_flowchart_inspection.png" width="100%" alt="Flowchart Pipeline" />
      <p align="left"><sub>• ROI·검출·분할·패치 분류·Blob 측정·집계 노드의 제한된 DAG</sub></p>
    </td>
    <td width="50%" align="center">
      <b>Stage 6: 검사 이력·패키지 내보내기</b><br />
      <img src="assets/screenshots/06_inference_center.png" width="100%" alt="Inference Center" />
      <p align="left"><sub>• 저장된 플로우 검사, 작업자 검토 이력, 독립 패키지·서비스. 실장비 신호는 미검증</sub></p>
    </td>
  </tr>
</table>

<br />

---

## 🏗 시스템 아키텍처 (System Architecture)

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                 Desktop Client Layer (Electron 33 + React 18)               │
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
│   │ (LabelMe Import)      │ │ (Faster R-CNN, U-Net)  │ │ & Audit Trail  │  │
│   └───────────────────────┘ └────────────────────────┘ └────────────────┘  │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │ Hardware Direct Compute
┌──────────────────────────────────────▼──────────────────────────────────────┐
│       Apple Silicon MPS         │  NVIDIA CUDA       │  CPU               │
└─────────────────────────────────────────────────────────────────────────────┘
```

전체 플로우 패키지의 [독립 검사 서비스](backend/engine/inspection_service.py)는 데스크톱 앱과 별도 프로세스로 실행한다. 저장된 파일·HTTP 작업과 이력을 처리하지만 PLC 판정 신호 또는 양산 지연시간 보증은 포함하지 않는다.

<br />

---

## ⚡ 빠른 시작 (Quick Start)

### 필수 요구 조건
* **Node.js**: v18.0.0 이상
* **Python**: 3.10 이상. 설치할 PyTorch·torchvision 등 의존성이 해당 Python/OS 조합을 지원하는지 확인한다.
* **OS**: macOS 12+ (Apple Silicon) 또는 Windows 10/11 (64-bit)

### 저장소 복제 및 설치
```bash
# 1. 저장소 복제
git clone https://github.com/JaeHongChoe/modu-vision.git
cd modu-vision

# 2. 파이썬 가상환경 생성 및 의존성 설치
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 3. 프론트엔드 모듈 설치
npm install
```

### 개발 모드 실행
```bash
# UI와 백엔드 데몬을 동시에 핫 리로드 모드로 실행
npm run dev
```

<br />

---

## 📦 데스크톱 패키징 (Desktop Packaging)

`package:mac`과 `package:win`은 Electron 패키지를 만든다. 현재 패키징 설정은 백엔드 소스를 포함하고, 실행 시 별도 빌드한 백엔드 바이너리를 찾거나 Python 환경을 사용한다. Python이 없는 대상 PC에서의 완전 독립 실행과 모든 의존성 포함 여부는 각 플랫폼의 산출물 설치·첫 실행으로 검증해야 한다.

### 🍎 macOS 애플리케이션 번들 (.app)
```bash
npm run package:mac
# 출력 위치: release/ 아래의 macOS 산출물
```

### 🪟 Windows 설치 파일 (NSIS)
```bash
# 별도 백엔드 바이너리 빌드
npm run build:backend

# Electron 설치 파일 생성
npm run package:win
# 출력 위치: release/ 아래의 NSIS 산출물
```

바이너리 빌드와 Electron 패키징은 별도 스크립트다. 현재 `electron-builder` 설정에서 빌드된 백엔드 바이너리를 설치 파일에 포함했는지 확인하고 대상 Windows PC에서 첫 실행을 시험해야 한다.

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
├── 📂 assets/                     # 고해상도 로고 및 6단계 스튜디오 스크린샷
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

버그 제보, 기능 제안 및 풀 리퀘스트(PR)는 언제나 환영합니다!
1. 이슈를 등록하여 구현 계획을 논의해 주세요.
2. 피처 브랜치를 생성합니다 (`git checkout -b feature/amazing-feature`).
3. 변경 사항을 커밋합니다 (`git commit -m 'feat: Add amazing feature'`).
4. 브랜치에 푸시합니다 (`git push origin feature/amazing-feature`).
5. 풀 리퀘스트(PR)를 오픈합니다.

<br />

---

## 📄 라이선스 (License)

This project is licensed under the **MIT License** - see the [LICENSE](LICENSE) file for details.

<div align="center">
  <sub>Built with ❤️ by the Modu Vision Open Source Team.</sub>
</div>
