# Localhost 웹과 데스크톱 기능 일치 설계

상태: **구조 선택 완료, 상세 설계 승인 대기. 제품 구현 전.**

기준 소스: `7450808a2af4509a55d781e432cc56733e55a52c`.
기준 제품은 이 Mac에 설치한 최신 macOS arm64 앱이다. 기존 Windows/Linux 전용
조건과 미검증 상태를 웹 지원으로 확대하지 않는다.

## 1. 사용자 요청과 완료 기준

사용자는 로컬에서 실행하는 웹을 앱과 같은 기능으로 사용하려고 한다.
주소는 `https://localhost:8443`, 로그인은 기존 로컬 계정 방식을 유지한다.
2026-10-27 사용자는 **브라우저 화면 + 창 없는 로컬 보조 프로그램** 구성을 선택했다.

폴더 버튼이나 도우미 안내만 바꾸는 작업이 아니다. 프로젝트, 파일 가져오기,
라벨·마스크 교환, 저장·복원, 학습·평가, 보고서, 원격 서버, 도우미, 실행 상태,
배포 도구까지 아래 기능표를 전부 대조한다. 기존 앱에서 OS·서명·모델·역할 등의
조건 때문에 거절하는 기능은 웹에서도 같은 조건과 설명으로 거절한다.
웹에서만 빠진 기능을 숨기거나, 시험용 desktop bridge를 주입해 통과로 세지 않는다.

기능 일치는 다음 네 종류의 결과로 기록한다.

- 양쪽에서 실제로 수행한 기능과 저장 결과가 같다.
- 동일한 전제 조건에서 양쪽이 같은 이유로 거절한다.
- 외부 계정·물리 장치·서명된 배포물 등 사용자 참여가 필요한 확인이 남아 있다.
- 실제 제품 차이가 남아 있다. 이 항목이 있으면 기능 일치 완료라고 하지 않는다.

이번 구현의 자동 검증은 합성 자료와 격리 계정·로컬 테스트 공급자를 사용한다.
실제 ChatGPT 로그인·플랜 동의·유료 요청, 실데이터 외부 전송, 실제 OS 서비스 등록,
운영 배포·물리 장치 동작은 자동으로 수행하지 않는다. 기존 앱의 ChatGPT 연결은
텍스트 전용이므로 웹도 같은 범위를 구현한다. 이미지 기능 자체를 새로 확대하지 않는다.

## 2. 검토한 구성

1. **브라우저 + 창 없는 Electron 보조 프로세스 + 기존 Python 엔진 — 사용자 선택.**
   기존 폴더 창, OS 보호 저장소, 도우미 서비스, 파일 열기와 배포 검증 코드를
   재사용한다. 웹 실행 때 보조 프로세스가 함께 실행되지만 앱 작업 창은 뜨지 않는다.
2. Electron을 없앤 웹 전용 구성. 브라우저 파일 선택·업로드와 OS별 보호 저장소,
   native 파일 창·배포 도구를 별도 구현해야 한다. 현재 앱과 같은 기능을 맞추는 데
   중복 구현과 플랫폼별 검증이 크게 늘어난다. 선택하지 않았다.
3. 웹에서 기존 앱의 `window.api`를 흉내 내거나 개인 desktop API token을 대행하는
   구성은 제외했다. 현재 renderer가 desktop으로 오인하고 웹의 쿠키·프로젝트 권한을
   우회할 수 있다. 기존 앱 창을 같이 켜서 기능을 맡기는 방식도 사용 목적과 다르다.

## 3. 실행 구조와 수명

```text
일반 브라우저
  → 현재 localhost HTTPS API / WSS (로컬 계정·CSRF·요청 문맥)
    → 기존 Python 엔진 (기존 학습·평가·작업·권한 정책)
    → localhost 전용 host 작업 controller
      → 비공개 부모-자식 IPC
        → 창 없는 Electron main helper
           native dialog / 안전한 파일 열기 / OS 보호 저장소 / 기존 도우미·배포 서비스
```

- `npm run web` 사용법과 기존 데이터 폴더를 유지한다. 기존 Node wrapper는 Python을
  선택·실행하고, Python 웹 launcher가 renderer/helper 빌드와 helper 자식의 수명을
  소유한다. 직접 `python -m backend.web`으로 실행해도 같은 준비·권한 경로를 쓴다.
  `--help`는 프로세스·계정·데이터를 만들지 않는다. 필요한 패키지가 없으면 설치하지
  않고 설명과 함께 중단하며, 기능이 빠진 서버를 정상 준비로 표시하지 않는다.
- helper는 별도 entrypoint와 별도 userData를 사용한다. 기존 데스크톱의 single-instance
  잠금이나 userData를 가져오지 않는다. 현재 열린 앱과 웹 서버를 종료·재사용하지 않는다.
- helper의 `ready`는 실제 IPC 왕복과 필요한 OS 기능 검사 뒤에만 기록한다.
  서버는 renderer/API/helper 준비를 확인한 뒤 기존 READY 레코드를 출력한다.
- 기본 종료와 Ctrl+C는 자신이 띄운 프로세스만 정리한다. 진행 중 학습은 기존
  detach·원장·lease 정책을 따른다. helper 종료와 Python 종료를 학습 종료로 표시하지 않는다.
- helper가 죽으면 host 기능은 즉시 실패 상태가 되고, 기존 학습 상태는 별도로 보인다.
  연결이 끊긴 요청이나 OS 작업을 자동 재전송하지 않는다. 재연결 뒤 저장된 작업
  영수증을 읽어 실행 여부를 판별한다. 계정 로그인·전송·복원·업데이트는 자동 반복하지 않는다.
- IPC는 launcher가 소유한 상속 pipe/stdio를 기본으로 한다. stdout에는 제한된 RPC
  프레임만 쓰고 로그는 stderr로 분리한다. 공용 TCP 포트, 브라우저용 helper token,
  URL query의 capability, 전역 IPC socket을 만들지 않는다.
- 현재 Electron 버전을 유지한다. native dialog와 암호화는 기존 실제 어댑터를 사용한다.
  helper는 Python 엔진을 import하거나 실행 중 전역 저장 환경을 바꾸지 않는다.

## 4. 웹 API·권한 경계

- 기존 HTTPS Host/Origin, WSS Origin, HttpOnly/Secure/SameSite 쿠키, CSRF를 유지한다.
  일반 shared/LAN 서버에는 새 host controller를 등록하지 않는다. loopback이라는 사실만으로
  인증을 생략하지 않는다. backend startup에서 주입한 로컬 실행 문맥이 있어야 활성화한다.
- renderer에는 별도 browser host adapter를 추가한다. `window.api`는 만들지 않으며
  `host.kind()`·API transport는 브라우저로 유지한다. capability 목록은 공개 기능·제한만
  반환하고 세션·비밀·절대 저장 경로를 포함하지 않는다.
- 로그인 전 공개 capability는 최소 정보만 제공한다. 로그인 뒤 actor/역할에 맞는 기능을
  협상한다. 로그아웃·만료·계정/프로젝트 전환 때 이전 capability와 진행 요청을 폐기한다.
- host API는 기능별 endpoint와 입력 스키마의 allowlist이다. renderer가 IPC method 이름,
  shell 명령, 모듈 경로, 임의 HTTP proxy URL을 전달하는 범용 invoke API를 만들지 않는다.
- 웹 관리자와 이 컴퓨터의 소유 권한은 동일하지 않다. 최초 host 기능 사용 때 native
  확인창에서 현재 localhost 주소와 웹 계정을 확인해 OS 소유자의 허용을 받는다.
  허용 기록은 해당 웹 설치·actor에 묶으며 다른 계정/서버로 이전하지 않는다.
- 그 허용은 임의 디스크 읽기나 자동 OS 작업 실행을 뜻하지 않는다. 파일 선택은 실제
  chooser, 복원·설치·서비스 등록·외부 서버 로그인은 해당 기능의 명시적 사용자 동작이
  있어야 한다. 권한 철회·계정 비활성화 뒤 기존 허용을 사용할 수 없다.
- native dialog가 열린 동안에도 세션이 만료될 수 있다. 완료 직후와 실제 소비 직전에
  세션·프로젝트·멤버십·역할·expected revision을 다시 검사한다.

### 4.1 경로 선택과 소비

서버의 경로 허용 기록은 actor, 설치/workspace, 프로젝트(선택 전 작업은 대상 namespace),
작업 종류, 읽기/쓰기 목적, 만료, 단회 nonce와 정규화한 파일 identity에 묶는다.
사용자가 chooser에서 고른 경로만 기록하며 raw 경로 문자열은 권한의 근거가 아니다.
같은 파일의 교체·링크 변경·취소·재사용을 검사한다. 사전보기와 적용이 분리된 작업은
선택 권한을 소비한 뒤 별도 hash-bound 작업 계획/영수증으로 이어간다.

인증된 renderer가 화면에 경로를 표시할 수는 있지만, 공유 계정이나 브라우저 저장소에
그 경로와 범용 권한을 배포하지 않는다. 가능하면 opaque 선택 ID와 표시 이름을 쓴다.
다른 계정·프로젝트·탭에서 선택 ID를 쓰면 거절한다. 임의 `*_path`를 전역 허용하지 않는다.
파일 열기는 기존 `classifyOpenTarget`의 확장자·bundle·alias·UNC/프로토콜 거절을 재사용한다.
자체 TLS 개인 키·계정 DB·helper credential/lease/인증 기록은 chooser나 경로 grant로도
내보내지 않는다. 일반 파일 읽기/다운로드 API를 만들지 않고 기능별 형식·내용 검증을
통과한 자료와 해당 actor가 만든 결과만 다룬다. 오류·진단에 비밀 값이 포함되지 않아야 한다.

기본 반입은 선택한 자료를 project-owned staging으로 검증·복사하는 방식이다.
원본을 옮기거나 고치지 않는다. 원본 폴더를 직접 source로 연결할 때는 현재 프로젝트의
다른 멤버도 읽을 수 있음을 표시하고 별도로 확인한다. `source_dataset_dir` 등록을
계정 개인 권한으로 오해하지 않는다. 링크를 따라오는 기능은 선택한 local 작업에만
허용하고 외부 target을 검토·허용해야 한다. generic shared/team의 기존 링크 거절은 유지한다.

## 5. 기능별 구현과 확인 결과

| 기능 | 웹에서 맞출 동작 | 핵심 확인 |
| --- | --- | --- |
| 데이터 폴더·개별 파일 | native chooser와 취소, ZIP/typed path 대안 유지 | 실제 chooser→자료 반입→목록·SHA 확인 |
| 프로젝트 생성·최근·열기 | 현재 계정 namespace에 생성/등록/선택, 기존 프로젝트 chooser | 잘못된 계정 거절, 탭별 현재 프로젝트 유지 |
| 프로젝트 템플릿 | 기존 task/preset/tag/retention allowlist 재사용 | 비밀·임의 경로를 template로 반입하지 않음 |
| 프로젝트·데이터 백업 | durable backup job 재사용, 상태·실패·다운로드/선택 폴더 저장 | 원본/source 내부 저장 거절, 실제 ZIP·manifest 확인 |
| 복원·보관함 | 아래 namespace 설계에 따라 새 위치에 복원, 기존 자료 보존 | 같은 ID·다른 위치 공존, 중단 복구, 손상 ZIP 거절 |
| LabelMe/COCO/YOLO | 선택 폴더 또는 업로드로 preview→기존 apply | class·좌표·현재 labelset 보존, 재접속 후 저장 |
| 픽셀 마스크 | 파일/폴더 반입 preview→apply와 ZIP export | 원본 크기·class·실제 픽셀 결과 대조 |
| SAM2/DINO·가중치 | 기존 검증을 거치는 폴더/checkpoint 선택·반입 | hash 검증과 OS/의존성 조건 동일 |
| 파생 이미지·로컬 결과 | 결과 폴더/문서 열기와 다운로드 | 허용된 결과만 열고 실행 파일·bundle 거절 |
| 학습·평가·흐름·검사 | 기존 HTTP 엔진과 역할/저장 경로 유지 | 합성 자료 1회 학습→평가→흐름 저장/재접속 |
| HTML/JSON 보고서 | 다운로드와 sandboxed report view 연결 | backend 파일 존재뿐 아니라 받은 바이트·보고서 열기 |
| 호환성·migration | 선택한 프로젝트 read-only 검사·기존 계획/복구 | 다른 프로젝트 자동 편집 금지, 계획과 적용 binding |
| runtime pack | chooser/반입→기존 inventory·compatibility 검사 | 서명/권한/호환성·부분 실패·저장 증거 |
| ChatGPT 도우미 | 아래 per-actor 서비스와 기존 대화·확인 UI | 연결/모델/취소/쓰기 preview/저장 로그 격리 |
| 원격 공유 서버 | localhost를 통해 기존 서버 로그인·프로젝트 선택·WSS 중계 | token 비노출, 서버 전환 시 이전 문맥·스트림 폐기 |
| SSH compute·예약 | 기존 backend 기능 그대로 노출·검증 | 권한·장치 조건·작업 소유·기존 자원 보존 |
| 업데이트/오프라인 전달 | helper 실행물 또는 명시 선택 설치의 기존 검증 서비스 | 실제 설치 identity·배포자·hash, 수동 전달 경계 |
| portable 전환·복구 | 명시 선택한 중지 설치와 기존 계획·canary·복구 재사용 | 현재 설치/데이터 선택 금지, stale plan·재적용 거절 |
| OS 서비스·배포 도구 | 기존 지원 OS·admin+owner에 local host 승인 추가 | 실제 설치 자동 실행 없이 권한·계획/회귀 확인 |
| 엔진 상태·재시작 | 서버와 helper의 실제 소유 PID/상태/이유를 구분 | health만으로 process 완료 주장 금지, 안전한 재연결 |
| 도움말·실행 문서 | 웹 미지원 문구·버튼·절차를 실제 capability와 일치 | 일반 브라우저에서 shim 없이 대조 |

웹이 백업을 만들어도 내려받지 않는 현상, report content를 버리는 현상, 브라우저에서
picker만 존재하는 라벨/마스크/호환성 UI를 각각 고친다. 기존 typed path 대안은 없애지 않는다.

### 5.1 프로젝트·복원 namespace

현재 AccountStore는 manifest project ID 하나를 경로 하나에만 묶지만 desktop 복원은
같은 project ID를 새 workspace 위치에 보존한다. 이 차이를 ID 재작성이나 권한 우회로
해결하지 않는다. 로컬 웹에 **등록 위치 identity**를 추가한다.

- 등록 위치는 opaque registration ID, account owner/member 권한, workspace ID,
  원래 manifest project ID, 실제 위치, revision을 갖는다. 기존 등록은 기존 ID·경로와
  selection을 그대로 읽는 호환 경로를 유지한다. account membership의 내부 대상 key와
  manifest project ID를 구분한다. 기존 계정·membership의 대상은 default registration으로
  보존하고 additive 위치 레코드를 연결한다. UI의 표시 project ID를 내부 membership key로
  무조건 교체하지 않는다. 과거 client의 project-ID-only 선택은 원래 default 위치를 뜻하며
  복원본을 자동 선택하지 않는다. 새 위치 선택은 registration ID를 명시해야 한다.
- 새 local 프로젝트 열기/복원은 이 명시된 등록 위치를 선택한다. 요청의
  `X-Vision-Context`는 서버가 반환한 actor/workspace/project identity와 일치해야 한다.
  로컬 실행에만 주입하는 project resolver가 등록 위치를 해석하고, 기존 permission policy를
  재사용해 매 요청 actor·membership·role을 확인한다. 브라우저가 준 workspace ID만으로
  허용하거나 일반 shared 서버의 전역 workspace 검사를 풀지 않는다.
- 복원은 새 빈 target의 소유를 확보하고 archive SHA, project/labelset/데이터·모델 참조,
  credential 정리, lock/lease와 기존 archive 검증을 통과한 뒤 위치를 등록한다.
  원래 project/model/labelset ID와 fingerprint를 임의 재발급하지 않는다.
- 기존 위치는 그대로 남는다. 같은 manifest ID의 복원본도 별도 workspace/등록 위치로
  구별된다. 기존 탭의 captured context는 원래 위치를 계속 가리키며 복원본으로 이동하지 않는다.
- 브라우저 저장 key, job/artifact/ref 조회, 최근 목록, 선택 상태와 원격 transport identity에
  등록 위치/workspace binding을 포함한다. 프로젝트 ID만 같은 두 위치의 캐시·대화·라벨·
  모델 후보가 섞이지 않아야 한다. 이전 위치의 실행 중 job은 새 위치로 이전하지 않는다.
- 등록 DB와 filesystem publish는 하나의 journalled operation으로 다룬다. staging부터
  publish·등록·현재 selection까지 단계·expected identity를 보관한다. 중단 뒤 원본을
  지우지 않고 동일 요청을 재평가해 미완료·완료·수동 복구를 구분한다. 비어 있지 않거나
  소유가 바뀐 target은 거절한다. 실패 자료는 quarantine/보관 이동하며 영구 삭제하지 않는다.
- archive credential과 로그인 세션은 원래 프로젝트/사용자에서 자동 복제하지 않는다.
  새 위치를 열 때 필요한 계정 연결은 기존 제품 정책대로 다시 한다.

## 6. ChatGPT 도우미와 원격 연결

### 6.1 도우미

- renderer의 `AssistantSession`, 도구 allowlist, 사람의 write preview/확인, authority
  binding을 유지한다. helper는 기존 `AssistantService`, `SiwcConnection`, 암호화 저장소,
  redaction, 요청 한도·취소·replay 검증을 재사용한다.
- credential은 웹 전용 helper storage에서 actor별로 분리하고 OS 보호 저장소를 사용한다.
  desktop assistant 폴더나 다른 앱의 auth 파일을 읽거나 자동 이관하지 않는다.
  OS 보호가 없으면 평문 fallback 없이 해당 연결을 거절한다.
- 웹 설치/actor가 credentials를 소유하고, 계정 선택·활성 모델·진행 요청·취소·대화와
  확인 grant는 session/tab별로 분리한다. refresh rotation과 복구 잠금은 credential owner
  단위로 직렬화한다. 탭의 모델 선택·취소가 다른 탭의 상태나 결과 수신자를 바꾸지 않는다.
- 로그인/credential 설정 endpoint와 프로젝트 데이터를 보내는 endpoint를 나눈다.
  전자는 로그인 actor 소유만 확인하고, 후자는 현재 프로젝트 역할·authority를 추가 확인한다.
  `/api/assistant/*` 전체를 인증 예외로 만들지 않는다.
- ChatGPT OAuth callback은 기존 `127.0.0.1` 임시 listener와 state/nonce/PKCE·계정 검증을
  유지한다. 사용자가 직접 로그인·약관·플랜 권한을 확인한다. 토큰·인증 URL·callback
  proof·비밀번호를 browser response/storage/log에 반환하지 않는다.
- beginRun/endRun은 attribution이며 권한이 아니다. 브라우저의 실제 쓰기 요청 하나에
  현재 actor/session/tab/project·tool·run ID를 붙이고 서버에서 확인/소비한다. 다른 요청이나
  전환 뒤 요청에 이전 run ID를 재사용하지 않는다. forbidden 도구는 계속 거절한다.
- 로그아웃·만료·disabled·모델/계정/프로젝트 변경·helper 재시작 때 늦게 도착한 응답,
  OAuth callback, 이미지/쓰기 확인을 폐기한다. 진행 중 전송을 자동 재시도하지 않는다.

### 6.2 원격 shared server

사용자 입력 서버 주소는 기존 HTTPS/명시적 loopback HTTP 터널 검증을 사용한다.
원격 세션 token은 서버 측 메모리에만 있고, 암호는 저장하지 않는다.
연결은 **인증된 로컬 세션 + tab + 선택한 remote origin/account + authority epoch**에
묶는다. 로컬 쿠키/세션은 controller 권한으로 유지하고 remote 로그인 때문에 교체하지 않는다.
원격 계정/프로젝트 상태와 로컬 계정/프로젝트 상태는 별도 store와 request envelope로 둔다.
proxy endpoint는 먼저 로컬 session/tab의 연결 소유를 검증한 뒤 그 연결의 원격 문맥만
upstream header로 만든다. remote actor/workspace/context를 로컬 `X-Vision-Context`로
전달하지 않고, 원격 권한으로 native host 기능을 허용하지 않는다. host 작업은 항상
로컬 계정 권한을 다시 확인하고, 원격 프로젝트 데이터 작업은 원격 서버가 추가 승인한다.
브라우저는 계속 현재 localhost origin에만 요청한다. 서버가 실제 선택된 원격 origin의
허용 `/api/`·`/ws/` 경로만 중계하며 redirect·임의 fetch·protocol 전환을 거절한다.
method/path별 필요한 context를 보존하고 임의 authorization/forwarded header를 받지 않는다.
로컬 로그인 cookie/CSRF는 원격 서버에 보내지 않고 원격의 Set-Cookie도 브라우저에 전달하지
않는다. 파일/이미지·보고서·다운로드·업로드와 스트리밍 URL도 같은 연결 ID 아래에서만
중계해 직접 cross-origin token 요청으로 폴백하지 않는다. 원격 응답의 절대 URL이나
redirect가 새로운 서버 접근 권한을 만들지 않는다. 크기·시간·동시 stream 상한을 둔다.
원격 웹소켓도 선택한 서버·프로젝트·권한 epoch에 묶고 종료/전환 시 닫는다.
로컬 로그아웃·만료·계정 비활성화 때 그 세션의 원격 연결과 스트림도 폐기한다.
다른 탭/세션의 원격 연결을 대신 로그아웃시키지 않는다. remote login/logout은 전용
broker 동작으로 처리하고 그 upstream token 응답을 generic proxy로 반환하지 않는다.
서버 주소에 대한 보안 경고나 OS 신뢰 설정은 자동으로 우회하지 않는다.

## 7. 배포·운영과 데이터 보존

업데이트 상태는 실제 helper의 실행 형식과 OS·아키텍처·서명 상태를 말한다. 소스 실행을
설치 앱으로 가장하지 않는다. 별도 설치를 관리하려면 사용자가 native chooser에서
그 설치를 고르고 identity/plan hash를 확인해야 한다. 기존 배포자·manifest·canary·
소유 launch·백업·쓰기 fence 검사를 그대로 재사용한다. 사용자에게 보인 계획과 다른
설치를 변경하지 않는다. 앱에서도 안 되는 ad-hoc/발행자 미확인 전달을 웹에서 승인하지 않는다.

원본 이미지·라벨·클래스·모델·지문·labelset·프로젝트·백업, 현재 앱·웹 서버·작업/GPU,
다른 checkout과 QA·가이드 자료를 보존한다. 개발/검증은 현재 격리 worktree와 별도
데이터 루트에서 한다. 기존 웹 계정 DB는 additive migration 전 백업하고 실패/중단에서
재개할 수 있어야 한다. 실제 사용자 계정 DB와 보호 저장소는 개발 테스트에 쓰지 않는다.

현재 `/Applications/Vision AI Studio.app`, 바탕화면 연결, `Modu Vision Web` 데이터는
개발 중 바꾸지 않는다. 구현 후 CI와 통합 확인을 마치고 정확한 실행/업데이트 안내를
제공한다. 이 문서 승인만으로 main 병합·운영 배포·서비스 등록·계정 실요청을 자동 수행하지 않는다.

## 8. 구현 순서와 검증

세부 파일 분리와 endpoint 이름은 구현 계획에서 정한다. 아래 순서는 의존성을 따른다.

1. 기능/조건/저장 결과/거절 결과 표를 실행 가능한 parity matrix로 고정한다.
2. launcher/helper 수명, 비공개 IPC, browser adapter와 account host 승인·선택 grant를 만든다.
3. 등록 위치/namespace와 journalled open/import/backup/restore를 구현하고 데이터 보존을 검증한다.
4. 라벨·마스크·모델 파일·보고서·migration/runtime pack UI와 실제 경로를 연결한다.
5. actor/tab별 도우미, 원격 shared 연결과 run attribution을 연결한다.
6. 배포·portable·OS 서비스 도구와 엔진 상태/재연결을 기존 조건에 맞춰 연결한다.
7. 실제 localhost 제품 실행기로 기능표 전체를 대조하고 문서·회귀·독립 리뷰를 완료한다.

필수 검증은 다음과 같다.

- 단위/경계: IPC frame·payload·timeout, stale/cancelled grant, 바뀐 inode/link,
  허용되지 않은 method/path, 다른 actor/tab/workspace/project, 만료/로그아웃·역할 철회 거절.
- 저장/복원: 중복 ID·다른 위치, 두 탭 동시 사용, 손상/중단 archive, 등록 실패 뒤 재개,
  원본과 이전 선택 보존, credential 비복제, 실제 저장 SHA·labelset·계정 membership 확인.
- 도우미: 기존 protocol/store/service/tools 회귀 + 가짜 공급자를 통한 일반 브라우저
  setup→모델 선택→대화→쓰기 preview 취소/확인→기록→새 로그인. token·capability
  비노출과 tab 간 취소·model·replay 격리를 확인한다. 실제 계정 결과와 구분한다.
- native: 실제 helper·chooser/취소·안전한 파일/폴더 열기·정상 종료/재시작을 확인한다.
  `window.api` shim을 쓰지 않는다. CUA로 일반 사용자 화면을 확인하고 CLI/API 저장 증거와 대조한다.
- 통합: 격리 합성 데이터의 업로드/폴더→라벨·마스크 저장→작은 CPU 학습 1회→평가/보고서→
  흐름/검사 저장→브라우저 새로고침·서버 재실행·다시 로그인→같은 결과 재독.
  기존 사용자 작업/GPU를 기동·중단하지 않는다. 모델 성능·현장 수락과 구분한다.
- 플랫폼: 우선 이 Mac의 Edge/Chromium에서 실제 제품 경로를 확인하고 가능한 Safari/WebKit
  결과를 별도 기록한다. Windows/Linux는 기존 TLS·OS 지원 조건을 그대로 표시한다.
  확인하지 않은 OS/브라우저를 통과로 쓰지 않는다.
- 회귀: 변경 영역의 renderer/main/backend/E2E·source pin 검사를 통과하고 독립 리뷰를 받는다.
  정확한 최종 head의 fast/full/gate 결과를 구분한다. 완료 CI를 반복 기동하거나
  실패를 재실행만으로 덮지 않는다.

## 9. 근거와 구현자 판단

기능 차이 근거는 `.superpowers/sdd/2026-10-27-web-desktop-parity/gap-audit.json`의
26개 항목과 현재 소스다. 백업 경로 충돌 등은 코드 읽기의 정적 추론이며, 구현 전에
재현 시험으로 고정한다. 과거 합성 웹 QA나 desktop shim E2E를 이번 기능 일치 증거로
사용하지 않는다.

주요 근거: `src/renderer/services/hostAdapter.ts`, `browserSession.ts`,
`src/main/ipc.ts`, `src/main/assistant/{ipc,service,siwcConnection,keyStore,runHeader}.ts`,
`backend/{web,local_web_app,local_web_state}.py`,
`backend/api/{shared_authorization,routes_project,routes_dataset_imports,routes_report}.py`,
`backend/engine/{project_archive,shared_accounts}.py`, `backend/contracts/context.py`.
native dialog는 Electron 공식 API의 optional window 구성을 사용한다:
https://www.electronjs.org/docs/latest/api/dialog .

이 문서에서 세부 IPC serialization, DB additive migration SQL, module 경로와 endpoint 이름,
화면의 작은 배치는 구현자가 기존 코드에 맞춰 결정한다. 새 공개 호스팅/LAN 노출,
인증 제거, 계정 공유, 모델/이미지 지원 확대는 이 설계에 포함되지 않는다.
