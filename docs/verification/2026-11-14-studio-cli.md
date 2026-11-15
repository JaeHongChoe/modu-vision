# 공통 CLI·GPT 기능 탐색 검증 — 2026-11-14

기준은 `main`의 `080666112135e5fdb4bf69cf291c97dd809a2f38`에서 분리한 `feat/studio-cli-20261114`입니다. 라벨 편집 PR26과 독립적이며 설치된 앱·localhost 웹·사용자 프로젝트·계정·가이드를 교체하지 않았습니다.

## 구현과 확인

- 기본 FastAPI의 등록된 HTTP 계약 **529개 / 57개 그룹**을 발견했습니다. 기능 목록 자체는 인증만 확인하며 프로젝트를 선택하거나 생성하지 않습니다. 설치 후 추가되는 네이티브 라우터도 실제 등록 목록에서 읽는 테스트를 통과했습니다. 529는 기본 서버의 스냅샷이며 모든 설치의 고정 숫자가 아닙니다.
- 목록·상세 schema·JSON 실행·명시적 터미널 로그인·프로젝트 범위·진단·raw/multipart 전송·바이너리 저장을 구현했습니다. 기존 독립 학습 CLI는 유지했습니다.
- GPT 도구는 목록/상세/허용된 관측 3개를 추가했습니다. 최대 구성 **35개 도구 / schema 9,671자**로 기존 40개/24,000자 한도 안입니다. 실행 매핑은 기존 작업 계획과 실행기를 사용합니다.
- 실제 임시 백엔드를 소유한 loopback HTTP 서버로 실행하고 별도 CLI 프로세스에서 프로젝트 생성, 합성 PNG 가져오기, 빈 라벨 저장, PNG 다운로드, artifact begin→raw 청크→complete, 작업 이력 조회를 확인했습니다. 원본 PNG 해시가 같았고 다운로드 바이트도 같았습니다. 모델을 학습·평가하거나 실제 검사를 실행한 테스트는 아닙니다.

## 실행한 검사

환경은 macOS arm64, Node **26.4.0**, Python **3.13.5**입니다. 저장소의 CI 대상 Node24/Linux 결과와 구분합니다. 기존 Python 환경과 동일 lock의 독립 APFS dependency clone을 사용했으며 새 의존성은 추가하지 않았습니다.

| 검사 | 관측 결과 |
| --- | --- |
| CLI peer/subprocess + 실제 격리 API 연동 최종 검사 | `python -m pytest backend/tests/test_studio_cli.py backend/tests/test_studio_cli_integration.py -q` → **43 passed / 0 failed** |
| catalog·CLI·실제 연동·desktop auth·assistant tasks·기존 authenticated-unselected/shared denial 회귀 | 8개 파일 → **200 passed / 0 failed**. 이후의 작은 CLI protocol redaction 보완은 위 최종 43개 검사로 다시 확인 |
| 전체 renderer/main Node 검사 | `npm run test:renderer` → **3,108개 중 3,107 passed / 1 skipped / 0 failed** |
| TypeScript | `npm run typecheck` → 성공 |
| production renderer/main 빌드 | `npm run build` → 성공. 기존 chunk 크기·혼합 dynamic import 안내는 유지 |
| 생성된 API 타입 | `python scripts/generate_api_types.py --check` → current |
| 서비스 계획·액션 근거 무결성 | `scripts/check_service_plan.py`, `scripts/check_action_evidence.py` → 성공. 과거 실행 증빙을 새 기능 수락으로 승격하지 않음 |
| Git 공백 오류 | `git diff --check` → 성공 |

검사 수는 서로 중복됩니다. 합산한 별도 제품 수락 건수가 아닙니다. 전체 backend/GPU/모든 OS·모든 모델의 실행을 했다는 의미도 아닙니다.

## 실패를 먼저 재현하고 보완한 항목

실제 RED→GREEN 로그를 작업공간의 로컬 검증 기록에 보존했습니다.

- metadata endpoint 부재, 실수로 project admission을 건너뛰는 유사 경로, lazy/native route 누락과 schema 참조.
- 실제 Request.stream() 청크 업로드의 OpenAPI 본문 누락, 실제 `path` converter 정보 누락.
- 현재 프로젝트 GET의 기본 프로젝트 생성 부작용. 관측 목록에서 제외하고 CLI 진단은 명시적 프로젝트만 사용.
- 한글·공백 그룹 이름, 긴 schema 참조, 출력 제한으로 사라진 schema 참조.
- GPT의 마지막 가림 처리에서 credential 이름의 **schema 형식**까지 지워진 문제. 타입·제약만 보존하며 예시·기본값·실제 credential은 보존하지 않음.
- 합성 HTTP 오류의 token/password 반사, FastAPI validation `loc`에 묶인 password `input`, 진단 부분 오류의 가림 누락.
- 올바른 JSON 모양이지만 HTTP Content-Length보다 짧은 응답, 응답 유실 후 중복 요청, redirect에 대한 요청/credential 전달.
- 기존 출력 파일, 잘못된 SHA256, 세션 경로 길이·저장 공간 오류, 로그인 성공 후 세션 파일 저장 실패의 구분.
- 매우 짧은 잘못된 password가 JSON protocol의 고정 schema/outcome 문자열을 가림 처리로 손상시키는 문제.

서로 분리한 독립 검토에서 catalog/GPT 3건과 CLI transport 4건을 확인했고 수정 후 재검토에서 해당 결함의 잔존은 발견하지 못했습니다. 검토와 테스트는 합성 계정값·임시 저장소만 사용했습니다.

## 유지한 경계

- 모든 실제 API는 기존 세션·프로젝트·역할·revision·lease·자원·작업 규칙을 적용합니다. HTTP 성공, 접수, 완료, 취소 확인, 저장 모델과 품질 승인·배포를 구분합니다.
- 응답 유실/타임아웃은 결과 미확인일 수 있습니다. 자동 재시도·full 재실행·CI 취소·권한 우회는 추가하지 않았습니다.
- 네이티브 폴더 선택·보호 저장소·계정 연결·업데이트는 기존 browser tab/cookie/CSRF/명시적 동의를 요구합니다. bearer CLI는 이를 가장하지 않습니다.
- 현재 화면의 미저장 상태와 마운트된 화면 전용 동작은 기존 앱 실행기의 범위입니다. 새 CLI만으로 모든 GUI가 독립적인 headless 기능이 됐다고 주장하지 않습니다.
- 실제 GPT 요청·이미지 외부 전송·새 로그인/키체인 접근·GPU/모델·서비스 등록·운영 배포·앱/웹 교체는 실행하지 않았습니다. 사람의 정답 확정·품질 수락과 현장 검증도 별도입니다.
- 원격 PR fast/security/full 결과는 해당 PR에서 확인합니다. 위 로컬 검사 기록은 exact-head 전체 회귀 성공이나 main 병합 증거가 아닙니다.

## 원격 CI 보존 회귀 정정

최초 head `a5835e12`의 fast `43575149239`는 기존 full CI 명령 중간에 새 테스트를 삽입해 CI 순서·digest 보존 검사 2개가 실패했습니다. 제품 기능 성공으로 덮지 않습니다. `.github/workflows/ci.yml`을 main08066611과 byte-for-byte 동일하게 복원하고 신규 CLI 검사는 별도 `ci-studio-cli.yml`에서 모든 PR/main push에 전체 실행하도록 분리했습니다. 기존 full 선택·해시 단언·related selection·보호 조건은 변경하지 않았습니다.

수정 전 기존 2개와 새 workflow 검사 4개의 실패를 재현했고, 수정 후 source CI 계약 **50/50**, 전체 CLI·catalog·격리 연동·새 workflow **61/61**을 확인했습니다. 기존 실행은 종료된 뒤 새 head를 게시했으며 취소·rerun·full dispatch는 하지 않았습니다. 새 원격 CI의 결과는 별도입니다.
