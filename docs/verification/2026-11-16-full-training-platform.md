# Geti 전체 플랫폼 기반 도입 검증

## 현재 판정

전체 공개 소스를 고정하고 원본 UI 빌드·타입 검사와 실제 격리 API 왕복을 확인한 **기반 도입 단계**다. 모두비전 제품에 통합되거나 설치본에 반영된 상태가 아니다. 실제 모델 학습·평가·내보내기와 기존 계정·프로젝트 연결은 아직 검증하지 않았다.

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

`1 epoch`는 저장한 설정값이다. 학습을 실행하거나 1 epoch가 통과했다는 뜻이 아니다. 카메라, 모델 job 제출, 별도 backend pretrained weight 다운로드, GPT 호출, 사용자 이미지 전송을 시험하지 않았다. npm 의존 패키지에는 SAM decoder·ORT WASM 자산이 포함된다. 전체 의존성의 외부 송신 관측은 아직 수행하지 않았다.

## 추가한 소스 CI

[Geti platform source checks](../../.github/workflows/ci-geti-platform.yml)는 관련 source/gitlink/guard 변경의 PR과 main push에만 추가되는 M0 전용 workflow다. Ubuntu 24.04·Node 24에서 정확 submodule을 checkout하고 위 18개 fixture 시험과 전체 source 검사를 실행한다. npm/Python 설치·upstream script·학습은 실행하지 않으며, 기존 fast/full/gate·보호 조건을 변경하지 않는다. 현재 로컬 검사 결과와 앞으로 실행될 원격 workflow 결과는 구분한다. 이 workflow 성공은 실제 모델 학습·제품 통합의 성공이 아니다.

## 보존한 실패와 관측

1. backend base 의존성만 설치한 최초 schema 생성은 `base_weights_service.py`의 `requests` import에서 실패했다. 원본 CPU dependency closure를 설치한 뒤 정확 source schema 생성이 성공했다. 임의 package 버전 교체로 덮지 않았다.
2. 최초 UI 타입 검사는 fresh clone에 생성 spec/types가 없어 실패했다. 정확 backend spec/types 생성 뒤에는 상위 모두비전의 Electron 타입이 Playwright의 선택적 import를 통해 섞여 3개 진단이 남았다. 실제 외부 빌드 경계에서 같은 원본·의존성·생성 타입으로 검사하자 통과했다. 설치·패키징에서도 이 분리를 유지해야 한다.
3. 최초 API harness는 잘못된 `/dataset` 요청의 404로 실패했다. 원본 계약인 `/dataset/items`로 harness를 고친 새 실행만 통과했다. 원래 실패 로그·DB·응답·합성 사진은 보존했다.
4. macOS에서 PyAV와 OpenCV가 같은 ObjC class를 포함한다는 경고가 나왔다. 영상/카메라 경로와 패키지 호환성은 미검증이다.
5. 두 종료 모두 **Geti scheduler 자체가 InferenceWorker와 StreamLoader를 각각 10초 기다린 뒤 terminate**했다. harness가 신호를 강제 확대한 것은 아니지만, 이를 모든 worker의 정상 완료나 깨끗한 종료라고 표현하지 않는다.
6. 원본 `/api/jobs`는 현재 server session의 메모리 큐다. 재시작 뒤 작업 이력을 보존하는 플랫폼 ledger로 간주하면 안 된다. 모두비전의 기존 작업 영수증과 durable binding이 필요하다.

## 다음 완료 조건

실제 CPU 학습→평가→모델 variant 내보내기, worker 수명주기 보완, 인증된 gateway와 child 직접 접근 차단, 기존 데이터의 명시적 왕복 변환, 10개 모델군 확장, 한국어 작업 UI, 정확 최종 head CI·OS 패키지·매뉴얼이 남았다. 원본 UI의 별도 실행 성공을 이 항목들의 대체 근거로 쓰지 않는다.

세부 설계는 [전체 플랫폼 설계](../superpowers/specs/2026-11-16-full-training-platform-design.md), 실행 순서는 [계획](../superpowers/plans/2026-11-16-full-training-platform.md)에 기록했다.

## 로컬 증거

사용자 자료와 분리된 `.superpowers/sdd/2026-11-16-full-training-platform/`에 원본 로그와 QA 자료를 보존했다. 경로는 검증 실행 당시 기록이며 설치용 데이터 경로가 아니다.

- `geti-ui-feasibility-receipt.json`: 원본 설치·빌드·타입 실패/성공 및 분리 빌드 증거.
- `geti-api-schema.log`, `geti-api-schema-cpu.log`: 최초 실패와 실제 schema 생성.
- `api-qa-20261116T232648-5fabc387/`: harness 경로 오타 실패.
- `api-qa-20261116T232833-6a631301/receipt.json`: 실제 API·재접속 95개 확인.
- 같은 QA root의 `observations.json`: worker terminate와 ObjC 경고, 각 원본 로그 SHA.
