# Geti 전체 플랫폼 기반 도입 검증

## 현재 판정

개발 브랜치에서 고정된 전체 Geti backend·학습 library를 기존 모두비전의 계정·프로젝트·전처리·모델·검사 흐름과 연결했다. 합성 자료로 실제 인증 gateway의 CPU 학습 전주기와 앱·브라우저의 준비 화면을 검증했다. 설치본 반영·최종 head CI와 전체 현장 품질 승인은 별도 단계다. 초기 기반 도입 이력과 아래 통합 근거를 구분한다.

## 11월 17일 제품 통합 근거

- [연결 사이클 영수증](2026-11-17-geti-integrated-cycle.json): 기존 cookie 로그인·CSRF, 역할·타 프로젝트 거절, 24장 고정 분할16/4/4, 저장한 resize 규칙, 단 한 번의 CPU 1epoch 학습을 확인했다. 모델·데이터 revision과 세 형식의 평가·내보내기 SHA를 기록했다.
- 같은 완료 모델의 OpenVINO·ONNX로 실제 예측했다. 새 검사 사진은 학습 구성원에 추가하지 않았고 원본 좌표로 결과를 반환했다. 기존 모델 목록→검증→흐름 저장→실행→저장 검사 실행까지 실제 Geti 추론으로 통과했다. 재시작과 같은 작업 ID 조회가 새 학습을 만들지 않았다.
- [화면 검증](geti-platform-ui.md): 실제 브라우저·Electron 2경로에서 64장 복사·전처리 SHA·원본 보존·조회503 복구·프로젝트 전환 뒤 늦은 응답 거절·양 테마·720px·키보드·접근성을 확인했다. 화면 시험을 학습 버튼 이후 결과 UI·설치 앱·사용자8443 검증으로 확대하지 않는다.
- [실제 HTTPS 결과 화면 영수증](2026-11-17-geti-browser-model-results.json): 같은 완료 모델을 재사용해 저장한 resize 80×64 표시, 고정 사진 24장과 새 검사 사진 1장의 선택, OpenVINO·ONNX 예측, 실제 ZIP 저장·내용·SHA와 비로그인 401을 확인했다. 새 학습 없이 기존 작업·고정 묶음·원본 25장을 보존했고 소유 프로세스와 포트가 종료됐다.
- 실제 화면에서 Library revision 부재로 사진 선택이 409로 거절되고, 새 브라우저가 저장한 모델 전처리를 없다고 표시하는 문제를 재현해 수정했다. Library 보호를 낮추지 않고 별도 프로젝트 사진 조회를 연결했으며, 새 검사 사진을 학습에 넣지 않는다.
- 독립 검토에서 내보내기 동시 발행, 추론 중 권한 철회, 다른 준비본의 오인, 복사본 변경 후 라벨 검토 표시 재사용을 수정했다. 마지막 결과 화면 검토의 상대 폴더명 도우미 노출과 잘못된 사진 페이지 재시도도 수정하고 재검토했다. 새 사진 조회 16개와 화면·도우미 55개 검사가 통과했다. 실제 GPT 질문은 실행하지 않았다.
- [이전 23f macOS ARM 패키지 영수증](2026-11-17-geti-packaged-runtime.json): frozen parent `6f548b48…`와 패키지 내부 Python 3.14를 실제로 사용해 합성64장·38/12/14·밝기1.1 준비와 두 번의 실행을 확인했다. 프로젝트·전처리·복사본과 사진 ID/해시가 유지됐고, 정지 상태의 목록 조회는 child를 시작하지 않았다. 두 종료 후 전체 Geti 리소스 트리 변경 0, strict 서명 검증 성공과 소유 프로세스/포트 종료를 확인했다. 이 영수증은 분리된 사용자 자료를 쓴 패키지 시험이며 기존 사용자 설치본 교체와 구분한다.
- macOS ARM CPU를 검증했다. Geti Windows 프로세스 트리 종료, 모든 모델 구조의 실제 학습, GPU·현장 정확도·실제 초보자 완주는 확인하지 않았다. Python 수준 egress 제한은 OS sandbox가 아니다. Geti 백그라운드를 기존 자동 관리 업데이트의 완전 종료 보증에 포함시키지 않았으며 보호를 낮추지 않았다.

[새 frozen backend 완료 모델 재사용 영수증](2026-11-17-geti-frozen-model-reuse.json)은 소스 `85a388ab…`의 실행 파일 `8876b875…`에서 HTTPS cookie·CSRF 인증을 거쳐 기존 Geti 모델로 CPU 검사 **1회**와 동일한 저장 결과 재조회를 확인한 근거다. 새 학습·준비 없이 고정 묶음·기존 작업·원본 25장과 이전 영수증을 보존하고 관측한 소유 프로세스와 포트 종료를 확인했다. 이 추론 근거와 frozen 시작·재시작 acceptance는 별도로 기록했다. 앞선 `23f926b9` 패키지는 당시 소스의 이력이며, 같은 소스의 [새 서명 앱 두 번 실행 영수증](2026-11-17-geti-packaged-runtime-8876.json)도 별도로 통과했다. 새 frozen parent와 패키지 Python 3.14, 합성64장·고정 전처리·프로젝트/복사본 재접속을 확인했고, 두 종료 각각의 전체 Geti 리소스 변경 0·strict 서명·소유 프로세스/포트 종료를 확인했다. 사용자 설치 앱·8443 교체와 최종 원격 CI는 이 근거만으로 완료 처리하지 않는다.

## 소스와 경계

- 기반: `open-edge-platform/geti`, `app/v3.3.0`.
- commit: `ddcf394d550bf1b84ca262888260043af361f5e7`.
- tree: `0230bda3c9fce38e275a41403500dc8eec445616`.
- 전체 저장소: 2,987개 tracked file. `application/backend/` 589개, `application/ui/` 1,239개, `library/` 987개를 포함한다.
- `platforms/geti/upstream`은 전체 저장소의 gitlink다. 라벨 편집기나 학습 SDK 일부만 가져온 구성이 아니다.
- 원본 core는 Apache-2.0이다. 선택 모델·가중치·의존 패키지의 배포 조건은 별도이며, upstream UI의 최초 Ultralytics 동의 화면을 대신 수락하지 않았다.
- 현재 모두비전의 React 18/Python 3.13과 분리하여 원본 React 19/Python 3.14 환경에서 시험했다. 설치 앱·8443 웹·기존 자료·모델·계정·GPU·자동화를 변경하지 않았다.

## 실행한 확인

| 항목 | 결과와 실제 범위 |
| --- | --- |
| M0 source guard | Node 24.22.1, 소유한 작은 실제 Git fixture 18/18 통과. 잘못된 head/tree/origin·tracked 수정·source 경로 이탈·toolchain/license 불일치·Git filter 실행·replacement object 우회를 거절함 |
| 실제 전체 source 확인 | `node scripts/geti-platform.cjs check --json` 성공: 정확 commit/tree, tracked clean, 전체 2,987개와 backend589/UI1,239/library987, core LICENSE SHA256 확인. 상태는 `source_verified`이며 `runtimeVerified:false`, `productIntegrationVerified:false` |
| 전체 UI 설치·빌드 | Node 24.22.1/npm 11.19.1, `npm ci --ignore-scripts --no-audit --no-fund`, 원본 `npm run build` 성공. 의존 패키지 설치 hook은 실행하지 않음 |
| OpenAPI 타입 | 정확 원본 backend의 `app/cli.py gen-api`로 spec 생성 후 `npm run build:api` 성공 |
| 분리 UI 타입·빌드 | 모두비전 상위 `node_modules` 밖의 별도 복제본에서 원본 `npm run type-check`와 `npm run build` 성공. 원본·시험 코드 수정이나 진단 억제 없음 |
| backend 설치 | 격리 Python 3.14.10/uv 0.10.4, frozen lock의 CPU·MQTT 의존성을 설치. `ultralytics`, `ultralytics-thop`, `nvidia-ml-py`는 설치에서 제외. 전역 Python/기존 venv는 보존 |
| 실제 API | 66회 HTTP 요청·95개 단언 통과. mock backend가 아닌 고정 Geti application·SQLite·실제 worker 사용 |
| 데이터·설정 | 분류·검출·인스턴스 분할 프로젝트 생성, 합성 PNG 6장 업로드, 분류 라벨·학습/검증/시험 분할 저장, 사진 원본 바이트·썸네일 디코딩/해시 확인, 3개 작업 카탈로그 조회, 1 epoch 설정 저장·재조회 |
| 재접속 | 같은 격리 data로 재시작 후 프로젝트·클래스 ID·사진·라벨·분할·설정과 사진/썸네일 해시 보존 |
| 종료 | 두 server exit 0, 관측한 소유 child 모두 종료, 두 포트 닫힘. 아래 worker 강제 종료 관측과 구분 |

위 66회 API 시험의 `1 epoch`는 저장한 설정값이며 학습 실행 근거는 아니다. 실제 학습은 아래 독립 시험에서 확인했다. 카메라·GPT 호출·사용자 이미지 전송은 수행하지 않았다. npm 의존 패키지에는 SAM decoder·ORT WASM 자산이 포함된다. 전체 의존성의 외부 송신 관측은 아직 수행하지 않았다.

## 실제 CPU 학습 사이클

같은 원본 소스의 별도 QA 프로젝트에서 합성 24장(학습16·검증4·시험4, OK/NG 균형)을 사용했다. EfficientNet-B0을 CPU·batch2·worker0·64px·1 epoch로 **단 한 번** 제출했다. OMP/MKL/OpenBLAS/VECLIB/NUMEXPR thread 환경값은 2로 설정했으며, 전체 프로세스나 OpenVINO threadpool의 실제 상한을 측정한 것은 아니다. 공개 publisher의 MIT LICENSE와 weight 출처를 보존하고, 19,824,784바이트 weight를 명시적으로 한 번 받아 upstream SHA256 `79f2b405a15399a0772337bd7f58052cb88299a65ed838a43c1ea8d3e0253f70`을 대조한 뒤 소유 QA cache에서 읽었다. 플랫폼 core의 Apache-2.0과 pretrained publisher MIT를 구분한다.

- job `429ec27c-eb9c-4c5b-a54e-87209694bd2d`: `DONE`, error 없음, `2026-11-16T14:50:01.967556Z` 완료.
- 실제 학습 → PyTorch checkpoint → OpenVINO·ONNX export → 세 variant 각각 시험 subset 평가 → 모델 저장 완료.
- 실제 OpenVINO CPU 예측 1회 성공. 재시작 후 모델·세 variant ID와 데이터 revision 보존.
- **169회 HTTP·57개 확인 통과**, 산출물 83개의 파일 목록·SHA256 저장.
- 학습 worker 종료를 서버 종료 전에 확인했다. 두 server exit0, 관측한 소유 child와 두 포트 모두 종료했다. 작업 재시도·중복 제출·취소는 없었다.

| 실제 산출물 | 바이트 | SHA256 |
| --- | ---: | --- |
| PyTorch `model.pt` | 32,452,355 | `13add4de3ceee9245eaf8e097d1dd3e21b8ae6243e06c80e60fb0f3ca0a655be` |
| ONNX `model.onnx` | 9,041,852 | `86b2a314c76e9c79c90514ba1050454944be73eff129840c3431c95cb1856237` |
| OpenVINO `model.bin` | 7,978,236 | `9798780e3a443a4d90c22194e95ccaf9b2e44019b432c6d1f20e3ec929df8f88` |
| OpenVINO `model.xml` | 350,492 | `932f1ec2f9580573e2fb7d45bfbf0cfa54beca2b52b20dba353eea5e6a9a4815` |

모델 ID는 `04bce283-4e67-4f1e-88b1-18cfc8394e4b`, dataset revision은 `35dd0e81-78f7-427b-8da0-a4ed97d0377b`다. 이 합성 실행의 세 형식 시험 정확도는 모두 0.5다. **플랫폼 실행 가능성 시험이며 실제 검사 성능·제품 수락의 근거가 아니다.** 다른 모델군·양자화·MPS·영상·모든 OS를 확인한 것으로 확대하지 않는다.

공개 가능한 합성 sample ID·split·원본 해시·실제 job/model/variant·산출물 해시·평가·미검증 범위는 [축약 실행 영수증](2026-11-16-geti-cpu-cycle.json)에 보존했다. 실제 모델 binary나 사용자 자료를 저장소에 올리지 않았다.

## 별도 Geti CI 계약 검사

[Geti platform source checks](../../.github/workflows/ci-geti-platform.yml)는 관련 source·부모 gateway·패키지 변경의 PR과 main push에 별도로 추가된다. `source` 작업은 Ubuntu 24.04·Node 24에서 고정 submodule을 checkout하고 source guard·패키지 inventory 계약과 전체 source 검사를 실행한다. 이 작업은 npm/Python 설치·upstream script·학습을 실행하지 않는다.

`platform-cpu` 작업은 Ubuntu 24.04·Python 3.13의 해시 잠금 CPU 환경을 설치하고 gateway·권한·사진 목록·데이터·모델·흐름 경계·작업·runtime의 검사 파일 **12개**를 명시적으로 열거한다. 원래 [ci.yml](../../.github/workflows/ci.yml)은 기준 브랜치와 바이트가 같도록 복원했으며, 기존 fast/full/gate와 보호 조건은 바꾸지 않는다. 별도 계약 검사 설정을 추가한 사실과 실제 원격 CI 성공은 구분한다. 두 작업만으로 Python 3.14 런타임의 실제 학습, 패키지 실행이나 자동 관리 업데이트를 승인하지 않는다.

## 보존한 실패와 관측

1. backend base 의존성만 설치한 최초 schema 생성은 `base_weights_service.py`의 `requests` import에서 실패했다. 원본 CPU dependency closure를 설치한 뒤 정확 source schema 생성이 성공했다. 임의 package 버전 교체로 덮지 않았다.
2. 최초 UI 타입 검사는 fresh clone에 생성 spec/types가 없어 실패했다. 정확 backend spec/types 생성 뒤에는 상위 모두비전의 Electron 타입이 Playwright의 선택적 import를 통해 섞여 3개 진단이 남았다. 실제 외부 빌드 경계에서 같은 원본·의존성·생성 타입으로 검사하자 통과했다. 설치·패키징에서도 이 분리를 유지해야 한다.
3. 최초 API harness는 잘못된 `/dataset` 요청의 404로 실패했다. 원본 계약인 `/dataset/items`로 harness를 고친 새 실행만 통과했다. 원래 실패 로그·DB·응답·합성 사진은 보존했다.
4. macOS에서 PyAV와 OpenCV가 같은 ObjC class를 포함한다는 경고가 나왔다. 영상/카메라 경로와 패키지 호환성은 미검증이다.
5. 최초 CRUD 시험의 두 종료와 실제 학습 시험의 재시작 직후 종료에서는 **Geti scheduler 자체가 InferenceWorker와 StreamLoader를 각각 10초 기다린 뒤 terminate**했다. 실제 학습을 수행한 첫 server의 worker는 이 강제 종료 없이 끝났다. harness가 신호를 강제 확대한 것은 아니지만, 전체 수명주기가 깨끗하다고 표현하지 않는다.
6. 원본 `/api/jobs`는 현재 server session의 메모리 큐다. 재시작 뒤 작업 이력을 보존하는 플랫폼 ledger로 간주하면 안 된다. 모두비전의 기존 작업 영수증과 durable binding이 필요하다.

## macOS 관리 데몬 복구의 별도 검증 범위

추가 소유권·재시작 검사 15건의 원래 실행은 **14건 통과·1건 실패**로 보존했다. 실패한 관리 데몬의 강제 종료 복구 시험에서는 실제 CPU 모델 결과가 먼저 저장됐고, 이후 종료 관측기가 그 추론 작업자의 PID를 `unknown_members`로 분류해 소유권을 확정하지 않았다. 별도 격리 재시도 1건은 통과했지만 원래 실패를 재현하지 못했으므로 원인이나 수정 효과를 입증하지 않는다. 이를 원래 묶음의 15/15 성공으로 합산하지 않는다.

이 실패의 범위는 관리 데몬의 복구 소유권 확인이다. Geti core·실제 추론·작업대 UI는 위의 별도 실행 영수증으로 범위를 판단한다. **자동 관리 업데이트는 여전히 미검증이며, 소유권·종료가 불확실하면 거절하는 보호를 유지한다.** 소유권 관측기나 제한 시간을 낮추지 않았고, 프로세스 그룹 전환 가설을 확인된 원인으로 쓰지 않는다. 원래 실패와 격리 재시도의 로그·추론 결과 소유권은 로컬 `qa/native-portable-followup-proof.json`, `qa/daemon-original-failure-persisted-provenance.json`에 보존했다.

## 릴리스 확인 조건

릴리스할 때는 정확한 최종 head CI와 위 OS 패키지 근거를 확인하고, 실제 설치·기존 프로젝트 재접속과 매뉴얼 반영을 별도 인계 기록에 남긴다. 원본 플랫폼의 실행 성공과 분리된 패키지 시험만으로 기존 사용자 자료의 교체·전체 현장 수락을 선언하지 않는다.

위의 `23f926b9` 시점 패키지와 결과 화면 영수증은 당시 소스의 보존된 근거다. 이후 runtime·흐름 경계·CI 변경의 새 head를 검증한 것으로 소급하지 않으며, 새 패키지·최종 CI·설치 반영은 각각 별도로 확인한다.

첫 서명 패키지에서는 준비 후 재시작 시 Python 캐시가 앱 리소스를 바꿔 무결성 검사에 실패했다. 버전 확인과 실제 실행을 모두 `-B`로 고정하고 패키지의 개발 캐시를 제외했다. 실패한 패키지·해시 차이·서명 진단을 보존했으며, 최종 패키지의 첫 실행과 재실행 각각에서 전체 Geti 파일 트리와 서명을 다시 대조해 통과했다. Frozen diagnostics의 새 파일 최초 실행은 120초 제한으로 실패했고, 같은 바이트의 제한 내 진단과 acceptance-only 재검사가 통과했다. 최초 지연의 OS 검사 가설은 확인된 원인으로 쓰지 않는다.

세부 설계는 [전체 플랫폼 설계](../superpowers/specs/2026-11-16-full-training-platform-design.md), 실행 순서는 [계획](../superpowers/plans/2026-11-16-full-training-platform.md)에 기록했다.

## 로컬 증거

사용자 자료와 분리된 `.superpowers/sdd/2026-11-16-full-training-platform/`에 원본 로그와 QA 자료를 보존했다. 경로는 검증 실행 당시 기록이며 설치용 데이터 경로가 아니다.

- `geti-ui-feasibility-receipt.json`: 원본 설치·빌드·타입 실패/성공 및 분리 빌드 증거.
- `geti-api-schema.log`, `geti-api-schema-cpu.log`: 최초 실패와 실제 schema 생성.
- `api-qa-20261116T232648-5fabc387/`: harness 경로 오타 실패.
- `api-qa-20261116T232833-6a631301/receipt.json`: 실제 API·재접속 95개 확인.
- 같은 QA root의 `observations.json`: worker terminate와 ObjC 경고, 각 원본 로그 SHA.
- `training-runs/api-qa-20261116T234619-92016670/receipt.json`: 실제 CPU 학습 전주기. 영수증 SHA256 `c553b01ca3e2d4089545aa3b5ac97fa9d7b3e3b511be9a3ec9770ee8bdb39154`; 같은 폴더의 `observations.json`에 종료·경고를 보존했다.
