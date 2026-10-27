# localhost 웹 기능 통합 검증 — 2026-10-27

상태: 개발 통합 검증 중. 구현·검사·실제 API·브라우저 UI·설치형 수락을 구분한다.
이 문서는 최종 통과 선언이나 병합·배포 승인 기록이 아니다.

## 최신 체크포인트

`e2525829`의 전체 회귀 `40456188961`에서는 browser job `122029947405`가
짧은 창 라벨링 geometry 검사에서 실패했다. 검사 전용 SSR 로더가 새
`BrowserSessionGate`를 일반 빈 모의 함수로 바꾸면서 마크업을 0바이트로 만든 것이
원인이었다. canvas가 없어 `getBoundingClientRect`에서 실패했으며, 이어지는 전체
브라우저 검사는 실행되지 않았다. CPU source job은 당시 계속 실행 중이었고 취소하지 않았다.

geometry 로더가 실제 `BrowserSessionGate`와 `browserSession`을 읽게 수정했다.
기존 데스크톱 `window.api` fixture를 실제 host 판정으로 인식하며 제품 인증을
우회하지 않는다. 모든 크기·위치 단언, viewport, 전이 2초 상한과 오류 처리는 그대로다.
동일 geometry 검사의 RED→GREEN과 별도 인증 경계 8개 PASS를 확인했다.
1024×768 canvas 243px, 1280×800 246.5625px, 1440×900 221px,
2560×1440 370px, 집중 편집 267px였다. 제품·workflow 코드는 변경하지 않았다.
원격 원본 로그와 로컬 RED/GREEN은 `e252-geometry-failure/repair-evidence.json`에
해시로 연결한다. 재검토 보조 작업은 모델 지원 오류로 종료돼 완료한 독립 검토로
세지 않으며, 이번 작은 검사 구성 수정은 실제 소스·정확한 diff·인증 거절·만료 검사를
직접 검토했다. `e2525829`의 fast 11개·웹 보안 1개 성공은 별도 보존하며,
수정 후 새 head의 전체 회귀 통과를 대신하지 않는다.

제품 소스는 `0a9bbfc6`의 등록 위치 결합 수정까지, 검증 fixture는 `e88f1b3b`까지
반영했다. 아래 실제 화면 결과는 문서·검증 선언까지 반영한 `744283cf`에서 확인했다.
아래 시점별 기록의 대기 문구보다 이 요약이 최신 상태다.

- 전체 renderer/main: **2,499 PASS / 명시 opt-in SKIP 1 / FAIL 0**.
- 웹·보조 프로그램·마스크 복원 19개 backend 모듈: **352 PASS**.
- 기존 선택자를 유지하고 공급자 fixture 검사를 덧붙인 E2E 하네스: **67 PASS**.
  renderer/main/e2e 타입 검사와 build가 통과했다. 이번 문서 갱신을 위해 재실행하지 않았다.
- 복원본 로그인은 membership key를 계정 선택에 그대로 쓰면서, 서버가 검증한
  registration/workspace/actor/team/logical ID/경로 전체를 대조한다. 새 조합 18개와
  trace 정책 3개를 독립 실행해 21개가 통과했다. stale catalog/다른 같은-ID 위치/
  권한 거절/네이티브 준비 실패에서 ID-only legacy로 내려가지 않는다.
- 도우미 AP1은 해당 시험의 raw trace를 인증 전부터 끄고 실제 trace 옵션을 검사해
  해소했다. 의도적인 인증 전 실패에서도 ZIP trace와 helper ledger가 없었다.
  새 실제 브라우저 사례도 통과했으며, 원래 실패 trace는 SHA 그대로 private 권한에 보존했다.
- 이전 재시작의 서버 PID 26728/helper PID 26772에서 저장한 프로젝트·모델·평가·흐름·
  검사 ID와 원본/모델/split SHA를 대조한 기록, 소스 2,212개·빌드 58개 해시는 보존했다.
  이후 browser 도구가 enabled 설정에도 Work 비활성 응답을 반환한 차단도 과거 실패로 남긴다.
- 사용자가 앱을 종료·재시작한 뒤 browser 제어가 복구됐다. 당시 검증 서버도 종료돼
  실제 접속이 거절됐으며, 소유한 59443 실행기만 같은 데이터 폴더·기존 인증서로
  다시 실행했다(서버 PID 37496). 사용자 앱·8443·설정·권한은 변경하지 않았다.
- `744283cf`에서 정상 QA 계정 로그인으로 이전 실패 복원본의 membership를 다시 열었다.
  실제 native 파일 창으로 원래 백업을 선택해 `restore-parent-fixed/restored-project`에
  새로 복원했고, 데이터 통계 **라벨 완료 2장 / 미완료 0장**과 두 이미지의 각 브러시
  라벨 2개·마스크 표시를 확인했다. 새로고침 뒤 명시 로그인 gate와 정상 재로그인,
  원본과 새 복원본을 각각 다시 여는 동작도 통과했다.
- 별도 파일 대조 `qa/native-ui-mask-744-fixed-restore-readback.json`
  (SHA-256 `5946fd607bc5504b6b0c00222505581ba5af53776f981ae29e86667c8d4b95eb`)은
  마스크 경로가 새 범위 안의 실제 PNG로 연결되고 픽셀·구멍·클래스 ID 0/3/7·색상·
  원본 바이트·UUID·revision 2/content version 1·메타데이터 해시·이전 버전이 보존됐음을
  확인했다. 기존 실패 복원본 25개 파일, 원본 백업·프로젝트·기존 모델/split도 그대로다.
  브라우저 통계는 실제 UI 관측이며 이 파일 대조의 재구성 통계와 구분한다.
- 작업 센터의 새 백업 1개가 파일 24개를 검증하고 종료됐다
  (SHA-256 `b99f4e9d1704cc6d121cafabbd42871f68d153ee76e68239a718d4cbd5d3b9fc`).
  실제 native 상위 폴더 선택 후 `restore-parent-task-744/restored-project` 복원 완료와
  현재 프로젝트가 자동 전환되지 않는 상태를 확인했다. 작업
  `4326b5fa7b6e48bb840650a0392cd260`의 검증된 116,983-byte ZIP은 payload 24개와
  manifest 1개의 SHA/CRC가 맞았고, 새 복원본의 마스크 2장·브러시 4개, 원본·메타데이터·
  버전과 네 위치의 registration/workspace/permission 분리를 별도 대조했다.
  근거 `qa/native-ui-mask-744-taskcenter-restore-readback.json`의 SHA-256은
  `8038a3323c470effd33638c645ed152f2fc8c4d73c0743926387ccf8c875c237`이다.
  백업 원본의 `.retention/retention.sqlite3` 변경은 기록했으며 라벨·이미지·버전은 유지됐다.
  이는 durable 백업 작업에서 명시적으로 복원한 결과이며, 별도 durable `project_restore`
  작업을 실행했다는 뜻은 아니다.
- 실제 HTML 보고서의 `HTML 보고서 미리보기` iframe에서 보고서 제목·지표·혼동 행렬·
  이미지 항목 제목을 읽고 화면을 보존했다. DOM의 `sandbox=""`와
  `referrerpolicy="no-referrer"`도 확인했다. 화면 근거는
  `qa/actual-browser-744-html-preview.png`와 `.txt`다. 추가 학습·평가·검사 dispatch는 0회다.
- `744283cf`의 hosted fast `40454490320`은 **11/11 SUCCESS**, 웹 보안
  `40454490394`는 **1/1 SUCCESS**다. 정확한 head와 workflow path를 대조했다.
  해당 head의 full은 **미기동**이고, gate `40454490323`의 최초 실패는 full 선행 조건
  기록이다. 이전 head의 성공이나 실패를 현재 full 결과로 재사용하지 않는다.
- PR18은 draft이며 main 병합·배포·사용자 앱 재패키징은 하지 않았다. 위 결과는 합성 QA이며,
  전체 기능의 사람 수락·실제 공급자/원격 계정·OS 서비스·공개 릴리스·현장 품질 수락은 아니다.

## 범위와 보존

현재 macOS 앱과 같은 엔진·네이티브 서비스를 localhost 브라우저에서 사용한다.
`npm run web`이 전용 renderer, Python HTTPS 서버, 창 없는 Electron helper를 소유한다.
브라우저에 `window.api`를 만들지 않는다. 일반 공유 서버의 권한은 확대하지 않는다.

기존 `/Applications/Vision AI Studio.app`, 바탕화면 아이콘, 사용자 웹 포트 8443,
사용자 계정·자료·프로젝트·GPU/서비스를 변경하지 않았다. 실제 API QA는 격리된
포트 59443과 별도 계정·자료 폴더에서 수행했다. 원본을 삭제하지 않는다.

## 직접 확인한 실제 실행 — 이전 시점 기록

아래는 실행 당시의 기록이다. 이후 해소된 UI 대기는 위 최신 체크포인트와 구분한다.

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
  검사 코드가 주입하지 않았다.
- 실제 일반 브라우저는 검증용 인증서의 `NET::ERR_CERT_AUTHORITY_INVALID` 경고에서
  멈췄다. 이후 사용자가 인증서 경고와 네이티브 소유자 허용을 직접 확인했다.
  인증서 검사를 끄거나 승인 상태를 주입하지 않았다.
- 기존 검증용 서버의 실제 브라우저에서 분류 합성 이미지 40장을 네이티브 폴더 창으로
  선택했다. 프로젝트 사본의 OK 20장·NG 20장과 원본 이미지 40개의 바이트·SHA가
  같았다. 선택 취소 전후 사본·원본 보존, 클래스 저장과 새 로그인 뒤 재독을 확인했다.
- 같은 실제 브라우저에서 LabelMe·COCO·YOLO 각각의 native 폴더 선택, 미리보기·적용,
  ZIP 내보내기를 수행했다. 저장한 이미지 3장·라벨 4개와 내보낸 ZIP의 좌표,
  native class ID 3/7 및 색상 `#ef4444`/`#22d3ee`가 fixture와 같았다.
  ZIP CRC·파일 SHA·이전 버전 backup을 읽어 대조했고 원본·기존 학습 모델은 보존됐다.
  근거는 QA의 `native-ui-labelme-artifact-readback.md`,
  `native-ui-coco-artifact-readback.md`, `native-ui-yolo-artifact-readback.md`다.
- COCO의 기본 충돌 중지 동작도 실제 UI에서 확인했다. 거절 뒤 기존 LabelMe 라벨·
  metadata·이전 버전 SHA가 유지됐고 새 적용 버전은 생기지 않았다. 선택 동작으로
  만들어진 COCO 입력 사본은 적용 라벨과 구분했다. 원래 거절 기록은
  `qa/native-ui-coco-conflict-stop-readback.md`에 보존했다.
- 인증 수정 전 네이티브 창의 AX 조작 timeout과 당시 상태를
  `qa/auth-restart-before.json`에 보존했다. timeout을 선택·수락 성공으로 세지 않는다.
- 이전 검증용 서버 PID 95425와 helper PID 95454의 종료를 확인한 뒤 소유한
  59443 실행기만 `82e0c338`로 다시 빌드·실행했다. 이 커밋의 새 로그인 화면·
  새로고침·프로젝트 생성·native 선택 실사용 수락은 아직 진행 중이다.

위 실제 native UI 결과는 로그인 수정 전 소스의 증거다. `82e0c338`의 실제 화면
수락으로 소급하지 않는다. 마스크와 백업/복원 UI 수락도 아직 완료로 기록하지 않는다.

이 합성 자료의 실행 성공은 현장 정답·모델 품질·사람 수락을 뜻하지 않는다.

## 기능 대조표

| 대조 항목 | 구현 경로 | 현재 근거 / 남은 확인 |
| --- | --- | --- |
| 네이티브 폴더·파일 선택 | 고정 HTTP→private RPC→기존 Electron dialog | 사용자 native 허용, 실제 40장 폴더 선택·취소; `744283cf` 재로그인 후 백업 파일·두 복원 상위 폴더 native 선택 확인 |
| 프로젝트 생성·열기·최근 | 계정별 등록과 위치 검증 | 실제 managed 생성/재접속; `744283cf`에서 같은-ID 원본·새 복원본 별도 재열기와 복원본 재로그인 확인; 외부 경로·역할 거절 회귀 |
| 프로젝트 백업 | durable backup와 ZIP/hash/영수증 | 실제 native 백업의 21개 파일·ZIP CRC/SHA 보존; `744283cf`에서 동일 백업을 native 파일 창으로 선택해 새 복원에 사용 |
| 프로젝트 복원 | 새 위치·같은 manifest ID·새 registration/workspace | `744283cf` actual native 새 위치 복원·재로그인·원본/복원본 별도 재열기, 픽셀/메타데이터/이전 버전 및 기존 실패 복원본 보존 대조 PASS |
| 데이터 폴더·ZIP | immutable project staging 또는 chunk upload | 실제 40장 ZIP·SHA·명시 채택·source CAS; 선택 원본 교체 경합 B1 독립 재검토 통과 |
| 링크 자료 | 명시 대상 계획/네이티브 확인 후 사본 | 구현·대상 검토·메타데이터 한도 및 공통 writer B2 독립 재검토 통과, 실제 native 수락 별도 |
| 작업 센터 백업 복원 | 검증된 backup job→새 네이티브 위치 | `744283cf` actual UI 백업 24개 파일 검증·종료, native 새 위치 복원·현재 프로젝트 유지; ZIP/마스크/메타데이터/버전·네 위치 권한 분리 대조 PASS, 보존기한 DB 변경 기록 |
| LabelMe/COCO/YOLO | 프로젝트 사본→기존 preview/apply | 실제 UI 선택·적용·ZIP 내보내기, 3장/4라벨 좌표·ID·palette·SHA 대조; COCO 충돌 중지 보존 |
| 픽셀 마스크 | 프로젝트 사본→기존 mask preview/apply/export | 실제 2장/4브러시 import/export 픽셀·구멍·palette 대조; `744283cf` 새 복원과 두 이미지 실제 표시·라벨 완료 2/미완료 0·저장 파일 대조 PASS |
| 검사 이미지 | chooser 또는 기존 library/typed path | 프로젝트 입력 사본과 선택 권한 회귀 |
| SAM2/DINO | 모델 폴더 사본·기존 검증 | 의존성/hash/권한 조건 유지; 실제 추가 모델 실행 안 함 |
| 사전학습 가중치 | checkpoint 사본·기존 hash 검사 | 기존 공식 캐시로 실제 학습; 추가 다운로드 안 함 |
| 결과 폴더·문서 열기 | 현재 프로젝트 경로·기존 native whitelist | 실행 파일/bundle/비밀/다른 프로젝트 거절 회귀 |
| 보고서 전달 | Blob download·opaque sandbox preview | 실제 UI HTML/JSON 다운로드 bytes/SHA 대조; `744283cf` HTML iframe 내용·실제 화면 및 빈 sandbox/no-referrer 확인 |
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
| 계정 로그인 | HTTPS cookie·Origin·CSRF·명시 재로그인 gate | 실제 로컬 합성 계정; reload gate 130개·독립 16개 및 full native 위치 결합 18개; `744283cf` 새 복원본 reload→명시 gate→정상 재로그인 PASS |
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
  메타데이터 한도를 수정·재검토했다. 아래 B2의 별도 재현·수정·재검토와 구분한다.
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
- `d0add2e3`의 GitHub 빠른 검사 11개 중 10개가 통과했고, CPU 2번 job은
  406개 통과·CI 목록 보존 시험 1개 실패였다. 새 웹/host 시험 18개가 추가돼
  기존 명령 해시와 달라진 경우다. 추가 18개가 정확한 순서로 한 번씩 있는지 확인한
  뒤 그 블록만 제외해 기존 해시·검사 순서·시간 제한을 그대로 대조하도록 보완했다.
  원래 해시 상수와 workflow의 검사·제한을 바꾸지 않았고, 보완 후 관련 28개가
  통과했다. 독립 변형 검사 60개도 통과했다. 새 18개 각각의 누락·중복·순서 변경,
  기존 검사 삭제·순서 변경, 실패 무시·시간 제한·browser worker 변경을 모두 거절했다.
  원래 원격 실패와 로컬 RED 로그는 보존했다.
- `1ac335f6`의 CPU 5번 job은 파일 교체 시험 3개에서 공용 `time.sleep` 패치가
  다른 스레드의 대기까지 집계해 실패했다. 실제 재시도 횟수는 모두 맞았다.
  실제 package build와 별도 스레드로 세 실패를 재현한 뒤, 시험의 clock 바인딩만
  해당 모듈로 한정했다. 제품 코드와 기존 재시도·대기·실패·정리 단언은 유지했다.
  파일 전체 18개, 동시성 재현 포함 6개와 독립 검토 13개가 통과했고,
  잘못된 재시도 한도·지연 누락·비-Windows 재시도 변형은 모두 실패로 잡았다.
- 실제 새로고침 뒤 쿠키는 남지만 메모리의 CSRF proof가 없어 프로젝트 GET만
  성공하고 생성은 거절되며 native 찾기가 비활성화되는 문제를 확인했다.
  `82e0c338`은 기존 재로그인 정책대로 로그인 전 작업 화면·데이터 요청을 막고,
  정상 로그인·프로젝트 확인 후 화면을 연다. 비밀번호·CSRF·세션 토큰을 저장하거나
  backend 인증을 완화하지 않았다.
- 그 수정의 독립 검토에서 원격 도우미 401이 로컬 principal까지 지우는 RA1을
  재현했다. 원격 오류 뒤 로컬 인증을 한 번 확인하고, 확정된 로컬 401만 로그인을
  해제하도록 수정했다. 원래 RED probe는 보존했고 수정 뒤 같은 probe와 독립
  인증 검사 16개가 통과했다. 로컬 200·network/5xx, 새 계정·다른 remote로 바뀐
  늦은 응답을 구분하며 도우미 요청·쓰기를 재전송하지 않는다.

## 통합 검사 이력 — 시점별 결과

증거 로그는 이 작업의 `.superpowers/sdd/2026-10-27-local-web-desktop-parity/logs/`에 보관한다.

| 검사 | 현재 결과 | 근거와 범위 |
| --- | --- | --- |
| `82e0c338` 전체 renderer/main | 2,458 PASS, 1 명시 opt-in SKIP, 실패 0 | `reload-auth-final-renderer.log`; 총 2,459개, 이전 2,438 PASS 기록도 보존 |
| 실제 컴파일 Electron 진입점 | 이전 소스에서 별도 1/1 PASS | `final-actual-electron.log`; ready·실제 PID·세션 폐기·EOF 종료·BrowserWindow 0개, 최신 UI 수락으로 소급하지 않음 |
| `82e0c338` renderer/main/E2E 타입 검사·빌드 | PASS | `reload-auth-final*`와 현재 타입 검사 기록; 로컬 검사이며 hosted CI 결과 아님 |
| reload 인증 수정·독립 재검토 | focused 130 PASS·독립 16 PASS | 실제 모듈 경계의 retained cookie·CSRF·만료·계정/remote 교체·RA1 회귀, 실제 UI와 별도 |
| E2E 시험 도구 | 64/64 PASS | `final-e2e-harness.log`; 실제 사용자 브라우저 수락을 뜻하지 않음 |
| 최신 local-web/host 통합 | 340/340 PASS | `final-host-current.log`, 62.96초; B1/B2 보완 후 18개 명시 시험 파일 |
| service plan 검사 | PASS | 현재 정적 계약 검사 |
| action evidence 검사 | PASS | 아래의 source 선언 갱신 범위만 반영 |
| 현재 커밋의 hosted fast/full | `82e0c338` 전체 회귀 미기동 | `664b2239` fast 11/11·local-browser 1/1 성공은 이전 head 결과; `1ac335f6` full `40443762321`도 보존하며 현재 소스 통과로 쓰지 않음 |

`docs/service-action-evidence.json`은 현재 TSX 소스 10개의 `sources` 해시 선언만
갱신했다. 기존 action 기록·상태·과거 영수증은 바꾸지 않았다. 과거 verified 1,014개와
pending 435개는 그대로이며, 이번 갱신으로 새로 accepted 처리한 기능은 0개다.
[선언 변경 영수증](receipts/2026-10-27-local-web-parity-source-declarations.json)은
소스 선언과 과거 수락 증거를 구분하는 기록이며, 새 UI 수락 증거가 아니다.
이후 `82e0c338` 인증 수정에서는 선언된 소스 55개의 SHA가 모두 유지됐다.
따라서 registry·위 선언 영수증을 다시 쓰지 않았다. 이번 실제 UI QA와 과거 action
수락 상태 역시 자동으로 합치거나 새 accepted 상태로 바꾸지 않는다.

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

남은 확인은 마스크·백업/복원 실제 UI, `82e0c338`의 최초 로그인·새로고침 재로그인·
저장 프로젝트 재독·managed 생성·native 찾기 실사용 수락과 정확한 최종 head의
hosted CI다. 사용자 인증서·native 허용과 앞선 native 자료/라벨 교환 결과는 위처럼
별도로 확인했다. 링크/작업 센터 복원의 개별 검토나 이전 head CI를 이 남은
통합·수락 확인으로 확대하지 않는다.
Linux 런타임은 미검증이고 Windows 실행기는 기존 TLS private-key ACL 미지원으로
명시 거절한다. 모델·실장비·모든 OS·공개 서명/공증·실제 계정/플랜 수락을 확대 주장하지 않는다.

이 문서는 해당 커밋 시점의 기록이다. 후속 정확-head CI 상태는
[PR #18](https://github.com/JaeHongChoe/modu-vision/pull/18)에서 별도로 확인한다.


## 추가 통합 확인과 보존한 실패

- 로그인 수정 후 `82e0c338` 실제 브라우저에서 managed 프로젝트를 생성했다.
  CSRF 오류 없이 새 manifest가 저장됐으며, 기본 프로젝트와 다른 작업 자료는 남겼다.
- 픽셀 마스크 2장·brush 라벨 4개를 실제 네이티브 폴더 창으로 반입해 저장하고,
  원본 포함 ZIP을 내려받았다. ID 0/3/7·색·클래스별 픽셀·내부 구멍과 원본 SHA를
  대조했다. 21개 payload 파일이 든 78,851-byte 백업의 모든 항목 SHA와 ZIP CRC도 확인했다.
- 첫 실제 복원은 파일 바이트와 별도 등록은 보존했으나 `mask_file`에 이전 dataset scope가
  남았다. 라벨 목록이 보이는 것만으로 mask raster 수락이라고 하지 않는다. 완료 수가
  0/2였던 실제 실패 복원본은 25개 파일을 그대로 보존했다. `7e214133`의 수정은 게시 전
  editable overlay와 관리된 Studio 버전 사본의 정확한 소유 경로만 옮긴다. 원본·mask
  픽셀·모델·immutable package와 승인 정책은 유지한다. 관련 71개 및 마지막 35개,
  독립 9개가 통과했으며 새 위치에서 실제 재복원 확인은 아직 진행 중이다.
- `7e214133`로 검증용 서버만 재시작해 서버 PID 19908/helper PID 19964를 읽었다.
  기존 학습·평가·흐름·검사 ID와 원본·모델·분할 SHA는 유지됐고 추가 실행은 없었다.
  그 뒤의 실제 재로그인에서 복원본의 membership key와 logical project ID를
  혼동하는 오류를 발견했다. 원래 거절 화면과 정상 서버 context를 보존했으며 수정 중이다.
- `82e0c338`의 실제 평가 화면에서 HTML 24,315 bytes와 JSON 36,265 bytes를
  다운로드해 서버 파일과 byte/SHA가 같음을 확인했다. HTML은 이전 API probe와
  출력 옵션이 다르므로 이전 7,612-byte artifact와 같은 파일이라고 하지 않는다.
  추가 학습·평가·검사 실행은 없다. HTML 미리보기 수락과도 구분한다.
- `1ac335f6` 전체 회귀의 browser job은 335개 통과·2개 skip·14개 실패였다.
  8개 spec의 9개 사례는 바뀐 소스 지문에서 멈췄다. 현재 실행용 SHA 20개만 갱신하고
  기존 단언·시간 제한·원본 보존·요청 기록 검사는 유지했다. 새 실행 9개가 통과했다.
  과거 실행 영수증이나 실패 결과를 새 해시로 바꾸지 않았다.
- 다른 두 실패는 일반 HTTPS 공유 서버에 native `/session` POST를 반복하던 문제였다.
  읽기 전용 capabilities 확인 후 지원되는 경우에만 tab을 생성하고, origin·로그인 세대별
  실패 탐색을 제한했다. 실제 모듈 RED와 경합을 수정한 129개 및 독립 50개가 통과했다.
  도우미 원격 401을 로컬 만료로 오인하지 않는 기존 검사는 유지한다.
- 나머지 세 사례의 옛 안내 문구·초기 프로젝트/source 준비 조건을 보완했다.
  단계 이동 두 사례는 현재 소스로 통과했지만, 이를 입증되지 않은 제품 경합 원인 해결로
  확대하지 않는다. 새 로그인 절차의 비학습 브라우저 사례 7개가 통과했고 exact wire,
  비밀값 비저장, 계정·프로젝트 권한 및 원본 보존 검사는 그대로다.
- 현재 backend 19개 모듈은 352개 통과했다. Node 통합 중간 실행은 2,471개 통과·
  1개 실패·명시 opt-in skip 1개였다. 실패는 linked-source가 tab 준비보다 먼저 준비 여부를
  검사한 실제 모듈 조합 문제다. 준비 전 scope/generation 고정과 준비 후 전체 권한 검사를
  유지한 수정의 17개 및 독립 17개가 통과했으며, 마지막 통합 재실행은 별도로 기록한다.
- 실제 브라우저에서 native ChatGPT 설정 화면이 열렸다. 실제 계정의 약관·전송 동의는
  선택하지 않았고 로그인·외부 요청도 하지 않았다. 별도 fixture의 실제 HTTPS renderer→
  controller→private stdio→LocalHostService/SIWC/store 경로에서는 공급자 네트워크와
  native I/O만 모의했다. 읽기·미리보기·거절 뒤 쓰기 0회, 확인 후 분할 1회·run/context/log,
  취소·계정/탭 격리와 원본 20개 보존을 확인했다. 모의 공급자의 성공은 실제 공급자·OS·
  원격 브라우저 수락이 아니다. 실패 trace에 합성 cookie/CSRF가 남은 AP1을 확인해 원본을
  private 권한으로 보존했으며, CI 전에 이 사례의 안전한 trace 정책을 보완 중이다.

현재 미완료: 새 위치 실제 mask 복원·재로그인 화면 수락과 최종 head의 hosted fast/full/gate.
등록키 결합·AP1 수정 및 마지막 로컬 통합 검사는 위 최신 체크포인트에서 완료했다. 완료한 학습·모델 실행을 반복하거나
사용자 앱·8443·자동화·main을 변경하지 않는다.


기록 무결성 검사에서, 현재 실행용 source pin이 바뀐 8개 GUI spec에 대한 과거 선언이
예전 파일 SHA를 가리키는 오류도 보존했다. 기존 이력 147행 중 17행의
`current_test_source_sha256`과 사유만 갱신했다. 원래 receipt 147개의 바이트,
execution source/recorded test SHA/retained ancestor spec과 action registry는 그대로다.
독립 기계 대조를 통과했고, 무결성 gate는 1,014개 historical verified/435개 pending/
accepted 0을 유지했다. 현재 실행의 통과를 과거 실행이나 사람 수락으로 재귀속하지 않았다.
