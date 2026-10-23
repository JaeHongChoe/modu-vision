# 2026-10-24 후속 구현·검증

기준 트리는 PR #6이 병합된 `11f21993187e03713354f0a76a2a52a7540ec921`이다.
새 소스는 별도 `codex/remaining-work-20261024` 브랜치이며 기존 PR의 성공을
이번 변경의 CI 결과로 사용하지 않는다. 이 기록은 합성/격리 기능 QA이며
현장 정답·품질 승인, 처음 쓰는 사람 측정, 실제 키/유료 호출을 포함하지 않는다.

## 라벨 보관·복구

[API 계약과 제한](annotation-recovery.md). 새 복구 검사31개와 기존 경로·배치·
저장 트랜잭션·팀 편집 검사를 CI와 같은 FastAPI 환경에서 실행해 **82passed**.
프로젝트 .venv의 데이터 버전·클래스/원본 철자·vector/mask·후보 API 관련
**29passed**. 실제 HTTP 데이터 버전 복원 후 사본이 남는 것, OS 잠금 아래
저장/복구 두 순서, 중간 JSON/mask/manifest/장부 발행 실패의 바이트 롤백도 포함한다.

학습·지문 스캐너가 보관 사본을 입력으로 읽지 않도록 `by_dataset` 밖 숨김 저장소를
쓴다. 원본 교체·같은 stem의 확장자 충돌·새 활성 라벨·다른 수정 번호·심볼릭 링크는
거절한다. 복구 후에도 재검토이며 이전 승인을 되살리지 않는다. 강제 전원 종료의
다중 파일 crash atomicity까지 검증한 것은 아니다.

## 작은 창·팝오버

레이아웃/팝오버 집중 검사 **56passed**, Chromium geometry+popover **7passed**.
실제 Electron+격리 backend의 처음12개 UI/API 확인과 후속13개 확인도 통과했다.
실제 저장200, 클래스/공동 편집 잠금·경고, 집중 보기 복원, 휠·키·스크롤,
초안 격자/넓게·drawer 열고 닫기를 확인했다. 후보2개는 합성이고 채택하지 않았다.

최종 그림 영역은 1024×768 QA **246.75px**, 제품 최소1280×800 **246.70px**.
1024는 QA 전용 minimum override다. 2560×1440은 실제 디스플레이 제한으로
Electron renderer viewport emulation을 사용했다. native 창 크기로 주장하지 않는다.
416px drawer가 열린 작업영역864px에서 popup x12/w840/h185이며, 격자 채택 버튼은
스크롤로 도달한다. 닫으면 x68/w1200/h263으로 복원된다. page error0, 소유 앱/backend
exit0, 잔여 프로세스0. 최종 소스 해시와 PNG/JSON30개를 아래 증거 폴더에 복사해 검증했다.

## SAM2 점 입력

[실모델 실행과 실패/수정 경계](2026-10-24-sam2-quick-point-one-shot.md).
기본 꺼짐, 패널을 닫은 Canvas 점→요청1건, 설정 보존, 미리보기, 자동 채택 없음은
실제 화면/상태와 모의 공급자 HTTP를 결합한 public browser 검사로 확인했다.
실제 cached CPU SAM2 owned 검사는 수정 후 최종 **1passed/22.8초**였다.
성공 안내·화면 안 preview·실제 pending API/파일 일치, 생성 요청1건, 자동 채택0,
원본/기존 라벨/annotations 전체 바이트·revision/audit/annotation_hash 불변을 확인했다.
최초에는 안내 소실로 실패했고 수정 후에는 첫 팀 설정의 지연 초기화 전에 비교
기준을 잡아 실패했다. 후자는 실행 시각·team_data 제거 후 원래 SHA 재구성·이미지
장부 의미 비교로 분리하고, 초기화 대기 후 원래 불변 단언을 모두 유지해 최종 통과했다.
실패 두 실행은 보존했으며 최종 성공으로 바꿔 적지 않았다. 다른 모델 다운로드나
재학습은 하지 않았다. 모델 confidence를 정답 IoU로 간주하지 않는다.

## 키 저장·DICOM·사람 확인

[실제 safeStorage 재현기](../../scripts/qa/README.md)는 dummy만 네 프로세스에서
저장/재시작/제거/재시작했다. 실제 macOS OS 보호, 0600/0700, hasKey true/true/false/false,
네트워크 시도0. QA 고유 Keychain master는 남을 수 있으며 다른 앱 항목을 열거나
지우지 않았다. 집중 Node 검사8개 통과. CI에는 OS UI 실행 대신 격리 경계 단위 검사를 넣었다.

[DICOM3건](2026-10-24-dicom-three-browser.md)은 격리 환경3passed39.6초,
프로젝트 실제 .venv3passed36.9초. pydicom3.0.2 wheel 해시/실제 import 위치를
확인했다. 임상 품질·다른 압축 decoder·Windows/Electron DICOM 검증은 아니다.

[사람 실사용·현장 평가 표](first-use-and-field-validation.md)는 준비됐지만 실제
자료와 정답은 지정 대기다. ChatGPT 라이브 연결은 공식 약관 동의/직접 로그인
조건이 남아 구현하지 않았다. 기본1~6단계와 API키 도우미는 별개다.

## 통합·출판

최종 소스에서 renderer **1791/1791**, renderer/main·E2E 타입 검사,
production build, 생성 API 타입 일치, action/plan 근거 gate가 통과했다.
독립 검토에서 찾은 넓은 팝오버 clipping·설정 remount 초기화·모델 확인 후 빠른
모드 무효화와 실제 QA의 성공 안내 소실을 수정한 뒤 좁게 재검토했다.
새 중요 문제는 없었다. source CI 계약27개도 통과했고 기존 CPU/browser 선택과
head 검증을 그대로 유지했다. 정확한 원격 head의 CI 결과는 PR에 기록한다. 기존 PR6 전체 회귀39976600956·병합11f2199는
기준 트리 근거이며 이번 새 소스 통과로 세지 않는다. 새 전체 회귀는 소스 확정 후
정확한 head에 한 번 수행한다. 기존 CI를 취소하거나 보호 조건을 약화하지 않는다.

증거 보존 루트:
로컬 `$PRIVATE_QA_ROOT`(이번 실행의 개인 검증 폴더). 실제 절대 경로는 개인 receipt manifest에 남긴다.
작은 창30개 PNG/JSON과 key/DICOM 선택 JSON/로그는 복사 전후 SHA256을 비교했다.
프로필·키 파일·모델·원본 자료를 저장소에 넣지 않는다. 개별 기록에는 최초 임시
실행 경로도 남겨 서로 구분한다. 15분 ux 자동화는 중지 상태를 유지한다.
