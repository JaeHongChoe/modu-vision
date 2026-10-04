# Windows에서 소스로 실행하기

Python 3.13, Node.js 24, Git이 설치된 Windows x64 PC에서 PowerShell을 엽니다.
이 안내는 개발 실행용입니다. 서명된 설치본과 실제 Windows 11 장비의 설치·SCM·장치 검증은 별도입니다.

## 설치와 앱 실행

```powershell
git clone https://github.com/JaeHongChoe/modu-vision.git
cd modu-vision
py -3.13 -m venv .venv
.venv\Scripts\python.exe -m pip install --require-hashes --only-binary=:all: -r build/ci/requirements-windows-py313-cpu.lock
npm.cmd ci
$env:VISION_AI_PYTHON = (Resolve-Path .venv\Scripts\python.exe).Path
npm.cmd run dev
```

가상환경을 활성화하지 않고 실행 파일을 직접 사용합니다. PowerShell 실행 정책을 변경할 필요가 없습니다.
CPU 잠금 환경은 CUDA 설치를 대신하지 않습니다. 원격 GPU는 서버 연결, 장치 사전 검사, 실제 실행 기록을 각각 확인하세요.

## 작은 CPU 학습 확인

새 PowerShell에서 저장소 폴더로 이동한 뒤 실행합니다. `cpu-demo-evidence`는 새 폴더를 지정하세요.

```powershell
.venv\Scripts\python.exe scripts/cpu_demo.py --workdir cpu-demo-evidence
```

합성 회전 이미지를 만들고 앱과 같은 학습 엔진으로 학습·평가합니다. 작업 기록은 데모 폴더 안에 저장합니다.
결과의 `angular_mae_deg`와 모델 파일 목록을 확인하세요. 실제 부품 품질 승인은 이 데모에 포함되지 않습니다.

## 변경한 코드 확인

```powershell
npm.cmd run typecheck
npm.cmd run test:renderer
$env:MV_E2E_PYTHON = (Resolve-Path .venv\Scripts\python.exe).Path
npm.cmd run test:e2e:electron
```

브라우저 검사는 `npm.cmd run test:e2e:browser`입니다. 설치된 Chrome을 사용할 경우
`$env:MV_E2E_BROWSER_CHANNEL = 'chrome'`을 먼저 지정합니다.
검사별 전제와 임시 데이터 격리는 [기여 안내](../CONTRIBUTING.md)를 확인하세요.

## 검사 패키지 사용

앱에서 완료된 모델과 사용할 데이터 버전을 선택하고, 플로우를 저장·검증합니다.
패키지의 검증된 실행 버전과 대상 장치 실행 근거를 확인한 후 운영 승인 절차를 진행하세요.
배치 CLI, REST, C++·C# 예제는 [연동 안내](api-integration.md)에 있습니다.

오류가 나면 [문제 해결 안내](troubleshooting.md)를 확인하세요.
빌드·서명·공증과 플랫폼별 검증 조건은 [배포 플랫폼 기준](release-platform-matrix.md)에 있습니다.
