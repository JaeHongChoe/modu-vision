# localhost 웹 기능 통합 검증 — 2026-10-27

상태: 개발 통합 검증 중. 구현·검사·실제 API·브라우저 UI·설치형 수락을 구분한다.
이 문서는 최종 통과 선언이나 병합·배포 승인 기록이 아니다.

## 범위와 보존

현재 macOS 앱과 같은 엔진·네이티브 서비스를 localhost 브라우저에서 사용한다.
`npm run web`이 전용 renderer, Python HTTPS 서버, 창 없는 Electron helper를 소유한다.
브라우저에 `window.api`를 만들지 않는다. 일반 공유 서버의 권한은 확대하지 않는다.

기존 `/Applications/Vision AI Studio.app`, 바탕화면 아이콘, 사용자 웹 포트 8443,
사용자 계정·자료·프로젝트·GPU/서비스를 변경하지 않았다. 실제 API QA는 격리된
포트 59443과 별도 계정·자료 폴더에서 수행했다. 원본을 삭제하지 않는다.

## 직접 확인한 실제 실행

아래 40장 API QA는 최종 수정 전 작업 소스에서 실행했다. 최종 빌드의 서버 재시작·
저장 결과 재독과 일반 브라우저·네이티브 수락을 대신하지 않는다. 학습을 반복해
최종 소스 증거로 바꾸지 않고, 보존한 작업·파일을 재독해 확인한다.

- 실제 컴파일된 Electron 파일 진입점에서 protocol 1 ready, 실제 Electron PID,
  세션 폐기 왕복, stdin EOF 후 exit 0을 확인했다. BrowserWindow 생성은 0개이고,
  이 lifecycle 검사로 도우미 계정 저장소를 만들지 않았다.
- 실제 HTTPS 실행기의 인증서를 해당 검사 프로세스만 신뢰하도록 지정해 정상 TLS
  검증을 거쳤다. 인증서 검사를 끄거나 OS 신뢰 저장소를 변경하지 않았다.
- 초기 API QA에서 서버 PID 36801과 helper PID 36889를 별도로 읽었다. 원격 서버 health에
  localhost PID를 붙이지 않으며, helper 상실과 Python 서버 health를 구분한다.
- 합성 PNG 40장을 실제 ZIP reserve/chunk/complete 및 검증·채택으로 반입했다.
  추출된 파일 바이트와 원본 SHA가 같았다. 클래스 수정 저장과 원래 값 복원,
  24/8/8 분할 저장이 성공했다.
- 기존 공식 ResNet18 캐시를 사용해 CPU 1 epoch 학습을 정확히 한 번 실행했다.
  `job_1793063912_f5914f`가 완료됐으며 44,804,619-byte checkpoint와 metadata,
  job receipt의 SHA를 기록했다. 새 모델 다운로드나 GPU 학습은 없었다.
- 저장 모델 평가에서 시험 분할 8장의 예측과 selection overlap 없음이 확인됐다.
  평가의 실행 장치를 별도로 증명하지 않아 CPU 평가라고 하지 않는다.
- HTML 7,612 bytes와 JSON 36,265 bytes를 응답으로 받아 실제 서버 보고서 파일과
  byte/SHA가 같은지 대조했다. 이는 브라우저 다운로드 버튼 수락과 별도 API 증거다.
- 모델을 쓰는 흐름 `b6b1ee528723437daedc094b0bbb48a8`을 저장했다. 검사 실행
  `326e5222-9037-4a20-b39e-2e997c49cce9`는 CPU에서 한 번 완료됐다.
- 새 쿠키 로그인·새 탭·등록 선택으로 같은 프로젝트, 모델, evaluation ID, 흐름,
  검사 결과를 다시 읽었다. 원본·모델·분할 SHA와 학습 작업 1개가 유지됐다.
- 제품 소스 `e90f5ef2`로 검증용 서버만 재시작했다. 새 서버 PID 95425와 helper
  PID 95454의 준비 상태를 확인했고, 같은 저장 자료를 다시 열어 원본·모델·분할 SHA와
  평가·흐름·검사 ID가 유지됨을 대조했다. 추가 학습 POST와 검사 실행은 모두 0회다.
  최종 소스 1,725개와 컴파일 산출물 58개의 해시 목록을 격리 QA 기록으로 남겼다.
- 소유자 네이티브 허용 전 폴더 선택은 실제 403이었다. 허용 상태나 path grant를
  검사 코드가 주입하지 않았다. 실제 사용자 브라우저·네이티브 승인 수락은 별도다.
- 실제 일반 브라우저는 검증용 인증서의 `NET::ERR_CERT_AUTHORITY_INVALID` 경고에서
  멈췄다. 사용자에게 직접 확인을 요청했으며, 인증서 검사를 끄거나 경고를 대신
  통과하지 않았다. 이후 네이티브 승인·chooser·화면 수락은 사용자 확인 대기다.

이 합성 자료의 실행 성공은 현장 정답·모델 품질·사람 수락을 뜻하지 않는다.

## 기능 대조표

| 대조 항목 | 구현 경로 | 현재 근거 / 남은 확인 |
| --- | --- | --- |
| 네이티브 폴더·파일 선택 | 고정 HTTP→private RPC→기존 Electron dialog | native adapter·권한·취소 회귀, 실제 사용자 chooser 수락 별도 |
| 프로젝트 생성·열기·최근 | 계정별 등록과 위치 검증 | 실제 managed 생성/재접속, 외부 경로·역할 거절 및 등록 회귀 |
| 프로젝트 백업 | durable backup와 ZIP/hash/영수증 | 실제 파일 단위 회귀; 네이티브 위치 선택 수락 별도 |
| 프로젝트 복원 | 새 위치·같은 manifest ID·새 registration/workspace | 실제 archive/namespace/restart 회귀, 원본 보존 |
| 데이터 폴더·ZIP | immutable project staging 또는 chunk upload | 실제 40장 ZIP·SHA·명시 채택·source CAS; 선택 원본 교체 경합 B1 독립 재검토 통과 |
| 링크 자료 | 명시 대상 계획/네이티브 확인 후 사본 | 구현·대상 검토·메타데이터 한도 및 공통 writer B2 독립 재검토 통과, 실제 native 수락 별도 |
| 작업 센터 백업 복원 | 검증된 backup job→새 네이티브 위치 | 구현·독립 재검토 완료; 원본 membership·게시 전 inventory·legacy 정규화 유지, 실제 native 위치 선택 수락 별도 |
| LabelMe/COCO/YOLO | 프로젝트 사본→기존 preview/apply | picker/반입 계약 회귀; 실제 사용자 UI 수락 별도 |
| 픽셀 마스크 | 프로젝트 사본→기존 mask preview/apply/export | 입력 grant·크기/class 계약 회귀; 실제 사용자 UI 수락 별도 |
| 검사 이미지 | chooser 또는 기존 library/typed path | 프로젝트 입력 사본과 선택 권한 회귀 |
| SAM2/DINO | 모델 폴더 사본·기존 검증 | 의존성/hash/권한 조건 유지; 실제 추가 모델 실행 안 함 |
| 사전학습 가중치 | checkpoint 사본·기존 hash 검사 | 기존 공식 캐시로 실제 학습; 추가 다운로드 안 함 |
| 결과 폴더·문서 열기 | 현재 프로젝트 경로·기존 native whitelist | 실행 파일/bundle/비밀/다른 프로젝트 거절 회귀 |
| 보고서 전달 | Blob download·opaque sandbox preview | 실제 받은 HTML/JSON bytes 확인, UI 버튼 수락 별도 |
| 호환성·migration | 현재 선택 프로젝트·기존 계획/hash/apply | 다른 프로젝트 거절과 기존 migration 조건 유지 |
| runtime pack | 사본·inventory·서명/호환성 검사 | 기존 모델·OS·배포자 조건 유지; 실제 설치 안 함 |
| 업데이트 | helper의 실제 설치 identity·기존 검증 | 소스 실행은 설치형 배포물이 아님, 공개 릴리스 수락 별도 |
| portable 전환·복구 | 명시 선택 중지 설치·기존 canary/recovery | 현재 설치/자료·stale plan 거절, 실제 사용자 전환 안 함 |
| ChatGPT 도우미 | actor별 보호 저장소·tab별 선택/취소 | 공급자 경계 모의 회귀; 실제 로그인·유료/이미지 요청 안 함 |
| 다른 공유 서버 | 소유 connection/epoch HTTP·WSS 중계 | 실제 AccountStore+가짜 외부 transport 경계 회귀; 실제 원격 계정 안 함 |
| 엔진 상태·재접속 | 실제 서버/helper PID·별도 liveness | 실제 준비/EOF, 인증된 PID와 fresh login readback |
| SSH compute·예약 | 기존 HTTP 엔진·작업 소유/권한 | 기존 회귀, 실제 SSH/GPU 실행 안 함 |
| OS 서비스 관리 | 기존 admin+owner 및 local native 허용 | 승인 전 side effect 거절 회귀, 실제 등록 안 함 |
| 외부 링크 | 기존 브라우저 새 탭 / desktop shell | 기존 허용 scheme·opener 격리 회귀 |
| 계정 로그인 | HTTPS cookie·Origin·CSRF | 실제 로컬 합성 계정, no browser bearer, revoke 회귀 |
| 학습·평가·흐름·검사 | 동일 기존 HTTP 엔진 | 위 실제 합성 학습 1회·평가·흐름·검사·저장 증거 |

## 독립 검토와 실패 보존

- OAuth callback/refresh가 다른 탭의 signout 뒤 credential을 되살리는 경합,
  filesystem root 보호 판정, 포화된 RPC에서 취소·폐기가 막히는 네 건을 재현·수정했다.
  scoped 재검토의 native 34개가 통과했다.
- 복원본 권한이 원본 row 조회에 쓰이는 namespace 누출과, inode 거절 전에 legacy
  manifest를 정규화하는 문제를 재현·수정했다. 독립 probe와 관련 14개가 통과했다.
- EOF 후 살아 있는 것처럼 남는 helper readiness를 실제 child로 재현·수정했다.
- 원격 health 404, 느린 socket 정리 후 오래된 login 부활, 거절된 WSS가 남의 슬롯을
  반환하는 문제를 재현·수정하고 scoped 재검토를 기록했다.
- 원격 프로젝트에서 로컬 경로 선택이 남는 문제, 도우미가 이전 로컬 문맥을 쓰는
  문제, 원격 세션 만료 뒤 연결 해제가 로컬 로그아웃으로 바뀌는 문제를 수정·재검토했다.
  원격 프로젝트 작업과 로컬 보호 저장소·네이티브 권한을 분리한다.
- 작업 센터 복원의 등록 실패 후 재개, 원본 membership 재검사, 게시된 파일 inventory,
  legacy 정규화 시점과 복원 모델 조회를 수정·재검토했다. 같은 프로젝트 ID의 원본과
  복원본을 별도 등록 위치·권한·저장 결과로 유지한다.
- 링크 사본의 확인 전 파일 내용 읽기, 영수증 hardlink 덮어쓰기, 빈 폴더로 우회하는
  메타데이터 한도를 수정·재검토했다. 이 검토 완료가 아래 B2를 해소하지는 않는다.
- 기존 CLI 준비 mock 네 건은 새 helper build/serve 경계에 맞췄다. 새 구조의 실제
  Electron loader 문제는 별도로 재현하고 수정했으며 fixture 수정으로 숨기지 않았다.
- 첫 평가 API probe는 legacy standard endpoint에 `device=cpu`를 넣어 별도 common
  cohort 계약을 호출해 422였다. 표준 브라우저 getResults 계약으로 이어 갔고 추가
  학습이나 제품 코드 변경은 없었다. 원래 실패 로그와 요청을 보존했다.
- 전체 renderer 중간 실행의 custom-loader mock·theme 실패를 보존한다. 마지막
  custom-loader 네 파일은 실제 `LinkedSourceClient` 의존성을 읽도록 수정했고
  단언을 유지한 184개 검사가 통과했다. 이어 실행한 전체 결과는 아래와 같다.
- bare repository pytest의 private QA fixture·bundled torch collection 오류를
  전체 backend 통과로 바꾸지 않는다. CI가 실제 실행하는 명시 목록을 별도로 검증한다.

## 현재 통합 검사 결과

증거 로그는 이 작업의 `.superpowers/sdd/2026-10-27-local-web-desktop-parity/logs/`에 보관한다.

| 검사 | 현재 결과 | 근거와 범위 |
| --- | --- | --- |
| 전체 renderer/main | 2,438 PASS, 1 명시 opt-in SKIP, 실패 0 | `integrated-renderer-main-2.log`; 총 2,439개 |
| 실제 컴파일 Electron 진입점 | 별도 1/1 PASS | `final-actual-electron.log`; ready·실제 PID·세션 폐기·EOF 종료·BrowserWindow 0개, 실제 계정/chooser 수락 아님 |
| renderer/main/E2E 타입 검사·빌드 | PASS | 로컬 통합 검사이며 hosted CI 결과 아님 |
| E2E 시험 도구 | 64/64 PASS | `final-e2e-harness.log`; 실제 사용자 브라우저 수락을 뜻하지 않음 |
| 최신 local-web/host 통합 | 340/340 PASS | `final-host-current.log`, 62.96초; B1/B2 보완 후 18개 명시 시험 파일 |
| service plan 검사 | PASS | 현재 정적 계약 검사 |
| action evidence 검사 | PASS | 아래의 source 선언 갱신 범위만 반영 |
| CI 명시 CPU 회귀 목록 | 진행 중 | 최종 성공으로 기록하지 않음; B1/B2 이전 시작 목록이며 최신 보완은 위 340개로 별도 확인 |

`docs/service-action-evidence.json`은 현재 TSX 소스 10개의 `sources` 해시 선언만
갱신했다. 기존 action 기록·상태·과거 영수증은 바꾸지 않았다. 과거 verified 1,014개와
pending 435개는 그대로이며, 이번 갱신으로 새로 accepted 처리한 기능은 0개다.
[선언 변경 영수증](receipts/2026-10-27-local-web-parity-source-declarations.json)은
소스 선언과 과거 수락 증거를 구분하는 기록이며, 새 UI 수락 증거가 아니다.

최종 renderer/native 구성 검토에서는 새 중대 결함을 찾지 못했다. 백엔드 최종 검토는
동시 외부 파일 교체에서 다음 두 결함을 독립 재현하고 수정·재검토했다.

- **B1:** 선택 grant의 원본 inode를 복사 전에 고정하지 않아, 선택 직후 바뀐 폴더의
  내용이 최종 거절 전에 사본으로 읽혔다. 현재는 내용 작업 전에 grant를 소비하고,
  작업자가 별도 소유한 descriptor와 선택 inode·모든 하위 경로를 대조해 읽는다.
- **B2:** 사본 출력의 권한을 고정할 때 경로가 교체되면, writer가 만들지 않은 폴더를
  `chmod`했다. 현재는 writer가 만든 inode와 정확한 하위 목록·열린 descriptor를
  대조하고, 바뀌거나 모르는 경로를 거절한 뒤 소유한 폴더의 권한만 바꾼다.
  일반 반입과 링크 사본이 공유하는 writer에 해당한다.

두 건 모두 동시 외부 filesystem writer가 필요한 재현이다. 브라우저만으로 가능한
권한 우회나 실제 사용자 자료 변경을 관측했다고 주장하지 않는다. 원본 실패 probe와
로그를 보존했다. 독립 재검토에서 원래 재현과 전용 회귀 15개가 통과했고,
요청 취소 뒤에도 worker가 자신의 descriptor로 복사를 마치고 닫는 별도 검사
1개도 통과했다. 이 범위에서 남은 조치 가능한 결함은 확인하지 못했다.

## 최종 검사와 수락 경계

남은 확인은 전체 CPU 회귀 완료, 일반 브라우저의 인증서·네이티브 승인·chooser 수락,
feature PR과 정확한 head의
hosted CI 기록이다. 링크/작업 센터 복원의 앞선 개별 검토는 완료됐지만 이 남은
통합·수락 확인을 대신하지 않는다.
Linux 런타임은 미검증이고 Windows 실행기는 기존 TLS private-key ACL 미지원으로
명시 거절한다. 모델·실장비·모든 OS·공개 서명/공증·실제 계정/플랜 수락을 확대 주장하지 않는다.
