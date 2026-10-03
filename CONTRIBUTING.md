# 기여 안내 (Contributing)

이 문서는 새 PC에서 저장소를 받아 실행하고, 변경을 시험하고, 풀 리퀘스트(PR)를 여는 과정을 설명합니다. 의존성 설치와 테스트 명령은 CI(`.github/workflows/`)가 실행하는 것과 같은 인자를 씁니다.

## 1. 새 PC에서 시작하기 (Fresh checkout)

필요한 버전은 Python 3.13(잠금 파일이 3.13용입니다)과 Node.js 24입니다. CI와 같은 버전입니다. Python 의존성은 해시로 고정한 CPU 환경이라 GPU 없이 동작하고, 화면과 데스크톱 셸은 `npm ci`로 `package-lock.json` 그대로 설치합니다.

### Windows 11 x64 (PowerShell)

PowerShell의 기본 실행 정책은 `Activate.ps1`과 `npm.ps1` 스크립트를 막을 수 있습니다. 실행 정책을 바꾸지 않도록 가상환경의 Python을 직접 부르고 `npm.cmd`를 씁니다. `python` 명령은 Microsoft Store 안내로 연결될 수 있으므로 Python 런처 `py`로 가상환경을 만듭니다.

```powershell
git clone https://github.com/JaeHongChoe/modu-vision.git
cd modu-vision
py -3.13 -m venv .venv
.venv\Scripts\python.exe -m pip install --require-hashes --only-binary=:all: -r build/ci/requirements-windows-py313-cpu.lock
npm.cmd ci
```

이 안내의 다른 명령에서 `python`은 `.venv\Scripts\python.exe`로, `npm`과 `npx`는 `npm.cmd`와 `npx.cmd`로 바꿔 실행합니다.

### Linux x64 (bash)

```bash
git clone https://github.com/JaeHongChoe/modu-vision.git
cd modu-vision
python3.13 -m venv .venv   # Python 3.13이 없으면 배포판 패키지나 python.org 설치본으로 먼저 설치합니다
source .venv/bin/activate
python -m pip install --require-hashes --only-binary=:all: -r build/ci/requirements-ubuntu-py313-cpu.lock
npm ci
```

### macOS arm64 (zsh 또는 bash)

macOS용 고정 잠금 파일은 아직 없어 `requirements.txt`의 버전을 설치합니다. 테스트 도구는 잠금 파일과 같은 버전으로 함께 설치합니다.

```bash
git clone https://github.com/JaeHongChoe/modu-vision.git
cd modu-vision
python3.13 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt pytest==8.3.4
npm ci
```

개발 모드 실행: `npm run dev`. 화면과 백엔드를 함께 띄웁니다.

### 환경 변수 지정

이 안내의 환경 변수는 셸마다 이렇게 지정합니다.

- bash, zsh: `export MV_E2E_BROWSER_CHANNEL=chrome`
- PowerShell: `$env:MV_E2E_BROWSER_CHANNEL = 'chrome'`

## 2. CPU 데모 (CPU demo)

```bash
python scripts/cpu_demo.py
```

- 작은 합성 데이터를 만듭니다. 알려진 각도로 돌린 비대칭 표식이며, train/val/test로 나뉩니다.
- 앱과 같은 학습 엔진으로 회전 보정 모델을 CPU에서 학습하고, 따로 둔 test 이미지로 평가합니다.
- GPU, 네트워크, 내려받는 가중치 없이 보통 1분 안에 끝납니다.
- 결과로 측정한 각도 오차(`angular_mae_deg`)와 저장된 모델 파일 목록을 출력합니다.
- 이 데모는 설치가 처음부터 끝까지 동작하는지 보여 줄 뿐, 실제 부품에 대한 모델 품질을 뜻하지 않습니다.
- `--keep`이나 `--workdir 경로`를 주면 결과 폴더를 남깁니다.
- 학습 엔진이 남기는 작업 기록과 저장소는 결과 폴더 안의 `app data`에만 쓰며, 사용자의 앱 데이터는 바꾸지 않습니다.

## 3. 집중 테스트 (Focused tests)

작업별 테스트는 `backend/tests/test_service_s<단계>_<번호>.py` 형식입니다. 바꾼 영역의 테스트를 먼저 실행하세요.

```bash
python -m pytest backend/tests/test_service_s1_04.py -q
npm run typecheck
npm run test:renderer
```

- 실제 화면 e2e: `npm run test:e2e:browser`
  - Chromium이 없으면 `npx playwright install chromium`을 실행하거나, 설치된 Chrome을 쓰도록 `MV_E2E_BROWSER_CHANNEL=chrome`을 설정합니다.
  - Windows에서는 가상환경을 활성화하지 않았으므로 e2e가 쓸 Python을 지정합니다: `$env:MV_E2E_PYTHON = (Resolve-Path .venv\Scripts\python.exe)`.
- Electron e2e: `npm run test:e2e:electron`. `npm ci`로 잠금 파일의 Electron 버전을 설치한 뒤 실행합니다.
- 회귀 범위의 기준은 `.github/workflows/ci.yml`(Linux)과 `.github/workflows/windows-native.yml`(Windows)이 실행하는 목록입니다.
- 테스트는 사용자의 데이터를 바꾸지 않습니다. `backend/conftest.py`가 테스트 세션마다 임시 폴더를 만들고, 앱 데이터(작업 기록, 예약), 흐름 템플릿, 저장된 분할, 썸네일을 그 안으로 돌립니다.
  - pytest 밖에서 백엔드 스크립트나 CLI를 직접 실행할 때는 `VISION_AI_STUDIO_USER_DATA_DIR`, `MODU_FLOW_TEMPLATE_DIR`, `MODU_SPLIT_MANIFEST_DIR`, `MODU_THUMBNAIL_CACHE_DIR`를 임시 폴더로 지정하고, `VISION_RESOURCE_LEASE_DB`는 지정하지 않습니다(그러면 앱 데이터 폴더 안에 만들어집니다).
  - 테스트가 만든 가짜 프로세스에는 실제 프로세스 번호를 쓰지 마세요. 종료는 테스트가 시작한 프로세스에만, 그 핸들로 보냅니다.

## 4. 변경과 PR (Changes and pull requests)

### 작업 단위

- 하나의 PR은 하나의 작업(예: `S2-07`)이나 하나의 버그를 다룹니다.
- 브랜치 이름은 `fix/…`, `feat/…`, `docs/…`처럼 짓습니다.

### 변경 순서

1. 바꾸기 전에 실패하는 테스트로 문제를 재현합니다.
2. 변경을 구현합니다.
3. 같은 테스트와 영향을 받는 테스트를 통과시킵니다.

### 커밋 메시지

- 형식은 `fix(flow): …`, `feat(training): …`, `docs(ledger): …`입니다.
- 본문에는 무엇이 왜 바뀌었는지 사용자 관점에서 씁니다.

### PR 열기

1. 저장소를 fork하고, 1단계처럼 fork를 clone합니다.
2. `git switch -c fix/짧은-설명`으로 브랜치를 만듭니다.
3. 커밋한 뒤 `git push -u origin fix/짧은-설명`으로 fork에 올립니다.
4. GitHub에서 fork의 브랜치로 이 저장소 `main`에 대한 PR을 열고, PR 템플릿의 근거 항목을 채웁니다.

### 근거를 구분해 적기

다음은 서로 다른 근거입니다. 실행하지 않은 것은 확인된 것으로 쓰지 않고 "대기"로 남깁니다.

- 코드 구현
- 실제 학습·검사 실행
- native Windows 실행
- 실제 장비
- 모델 품질
- 라이선스·서명·배포 승인

## 5. 구조와 모듈 지도 (Architecture and module map)

| 위치 | 역할 |
| --- | --- |
| `src/main/` | Electron 메인 프로세스. 백엔드를 띄우고 멈추는 `supervisor.ts`, IPC, 배포 상태를 다룹니다. |
| `src/preload/` | 화면에 노출하는 데스크톱 기능(preload bridge)입니다. |
| `src/renderer/` | React 화면입니다. 데스크톱 전용 기능은 반드시 `services/hostAdapter.ts`를 거칩니다. |
| `backend/api/` | FastAPI 경로입니다. 학습, 데이터셋, 플로우, 검사, 작업 사건(`/api/job-events`), 팀 계정을 다룹니다. |
| `backend/engine/` | 학습 엔진, 소유 작업자 프로세스, 작업 원장(`job_store.py`), 플로우 실행, 데이터셋 색인입니다. |
| `backend/remote/` | 계산 서버에서 실행하는 작업자와 조정자입니다. |
| `backend/contracts/` | 인증, 프로젝트 문맥, 권한 규칙입니다. |
| `backend/tests/`, `scripts/e2e/` | 백엔드 테스트, 그리고 실제 화면·Electron e2e 하네스입니다. |
| `build/` | 패키징 설정과 CI 잠금 파일입니다. |
| `docs/` | 작업 계획(`service-upgrade-program.json`), 검증 기록(`implementation-ledger/`), 지원·라이선스 표입니다. |

## 6. 코드 규칙 (Coding rules)

- **텍스트 파일 인코딩**
  - 읽고 쓸 때 `encoding='utf-8'`을 명시합니다. 전역 UTF-8 모드에 기대지 않으며, `test_service_text_encoding.py`가 이를 검사합니다.
  - 사용자가 가져온 라벨 파일은 UTF-8(BOM 허용)을 먼저 시도하고, 아니면 PC의 기본 인코딩으로 읽습니다.
- **경로**: 공백과 한글이 들어간 경로가 Windows와 macOS 모두에서 동작해야 하며, 테스트에도 그런 경로를 넣습니다.
- **테스트 데이터**: 테스트는 임시 데이터 폴더(`VISION_AI_STUDIO_USER_DATA_DIR`)를 씁니다. 사용자의 홈 저장소를 건드리지 않습니다.
- **프로세스**: 코드가 직접 시작한 프로세스만 그 핸들로 멈춥니다. 번호(PID)로 다른 프로세스에 신호를 보내지 않으며, 테스트에서 지어낸 PID를 쓰지 않습니다.
- **공개 파일**: 코드, 테스트, 예제, 로그, 커밋에 실제 검사 이미지, 고객 데이터, 개인정보, 비밀번호·토큰·키를 넣지 않습니다. 예제는 합성 데이터와 환경변수를 씁니다.
- **화면 문구**: 사용자에게 보이는 문구는 한국어로 쓰고, 오류는 다음에 할 일을 알려 줍니다.

## 7. 담당 영역 (Ownership)

- 저장소 관리자(maintainers)가 모든 PR을 검토하고 병합합니다.
- 영역별 확인 기준:
  - 학습·작업 원장: 취소, 재연결, 예약 자원의 근거가 분리되어 있는가.
  - 데이터셋: 원본을 바꾸지 않는가, 버전이 고정되어 있는가.
  - 플로우: 백엔드와 화면의 연결 규칙이 같은가.
  - 검사 서비스·배포: 실행 위치와 장치가 명시되어 있는가.
  - Electron·설치: Windows와 POSIX 동작이 같은가.
  - CI·릴리스: 잠금 파일과 근거 기록이 있는가.
- 병합 전에 구현한 사람이 아닌 검토자가 실패 재현과 영향 범위를 확인합니다. 영역별 담당 지정 파일(CODEOWNERS)은 아직 없으며, 관리자가 검토자를 정합니다.

## 8. 지원 범위 (Support matrix)

| 항목 | 지원 | 현재 근거 |
| --- | --- | --- |
| Windows 11 x64 | 주 대상 | 설치하지 않은 소스 트리의 CPU 검사가 GitHub Windows Server 2025 runner에서 실행됩니다. 실제 Windows 11 장비와 서명된 설치본은 대기 상태입니다. |
| macOS arm64 | 개발 환경 | 개발 중 로컬 실행. 공증된 배포본은 대기 상태입니다. |
| Linux x64 | CI | Ubuntu runner에서 CPU 검사와 화면 e2e를 실행합니다. |
| Python / Node.js / Electron | 3.13 / 24 / 44 | Python 의존성은 `build/ci/*.lock`, Electron과 화면 의존성은 `package-lock.json`으로 고정하고, Python과 Node.js 버전은 CI 워크플로가 정합니다. |
| 장치 | CPU 항상 지원. CUDA·MPS는 선택. | CI는 CPU만 검사합니다. GPU 실행은 장비별로 따로 확인합니다. |

- 모델군은 10개입니다: classification, detection, segmentation, anomaly, patch_classification, rotation, ocr, rotated_detection, enhancement, defect_gan.
- 각 모델군의 실제 설정 항목은 `python -m backend.training_cli capabilities`가 보여 줍니다.
- 모델과 가중치 라이선스는 [docs/model-license-matrix.md](docs/model-license-matrix.md)에 있습니다.
- 배포 대상별 빌드·서명 조건은 [docs/release-platform-matrix.md](docs/release-platform-matrix.md)에 있습니다.

## 9. 변경·지원 중단·릴리스 정책 (Change, deprecation and release governance)

### 호환성

- 저장 형식(프로젝트, 플로우, 작업 원장, API 응답)을 바꾸는 변경은 이전에 저장된 데이터를 계속 읽을 수 있어야 합니다. 작업 원장과 REST API에는 아직 형식 버전 표기가 없으므로, 버전을 도입하는 변경은 따로 제안합니다.
- 저장된 값의 의미를 바꾸는 변경에는 두 가지가 필요합니다.
  - 원본 백업과 검증 기록을 남기는 명시적 변환.
  - `docs/implementation-ledger/`의 기록.

### 지원 중단

다음 정책을 따릅니다.

- 기능이나 형식을 없애기 전에 릴리스 노트로 알립니다.
- 적어도 한 번의 minor 릴리스 동안 경고와 함께 유지합니다.
- 기존 데이터는 변환할 때까지 읽을 수 있어야 합니다.

### 릴리스

- 아직 정식 릴리스는 없습니다. 첫 릴리스부터 버전은 SemVer를 따르고, 채널은 stable과 beta로 합니다.
- 릴리스 후보는 Linux와 Windows CI 통과, 라이선스 목록, 검증 기록을 갖춰야 합니다.
- 서명·공증·실제 설치 검증이 없는 산출물은 그 상태를 그대로 표시합니다.

## 10. 보안 문제 (Security)

취약점의 내용은 공개 이슈에 쓰지 말고 [SECURITY.md](SECURITY.md)의 절차를 따르세요. GitHub의 비공개 신고를 쓸 수 없으면 이슈의 **비공개 연락 요청** 양식을 씁니다.

## 11. 행동 강령 (Code of conduct)

모든 참여는 [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)를 따릅니다. 위반은 이슈의 **비공개 연락 요청** 양식으로 알립니다(내용은 쓰지 않습니다).
