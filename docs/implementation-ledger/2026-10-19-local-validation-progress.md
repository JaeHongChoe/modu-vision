# 로컬 검증 진행 — 2026-10-19

없는 장비나 사람이 직접 확인해야 하는 단계만 보류한다. 가능한 코드·앱·설치·복구 검증은 계속한다.

| 이번에 실행한 범위 | 실제 결과 | 남는 경계 |
|---|---|---|
| 설치 앱 로그인 → 프로젝트 → 갤러리 → 저장 플로우 열기 → 편집 → 저장 | 7단계 관측과 정상 main/backend/controller·관리 저장소 종료 통과 | 전체 helper tree, 실제 모델 품질·서명·장비 승인으로 확대하지 않음 |
| portable 상태 재열기, 폴더 선택 응답 인계, finish/forward 응답 인계 | 깨끗한 커밋 `1d208d2`에서 browser 4/4, retry 0 | 실제 renderer/main manager; CLI·서명은 controlled 응답. 실제 설치 복구 거래는 아님 |
| 손상된 평가 기록으로 검토 큐 열기 | 실제 backend 422, UI 오류 표시, 큐·원본 보존과 테스트 report 원복 통과 | 한 개 invalid 셀; 모델·취소·native 실행 아님 |
| HTTP503 → 같은 포트 단절·재연결, ACK 유실 후 중복 수신 방지·재열기 | 현재 소스 기존 테스트 2/2, 자기 socket/thread 종료 확인 | 로컬 synthetic 결과 전달. 외부 MES·모델 품질·72시간 실험 아님 |
| desktop 의존성 고지 수집·바이트 재검증 | 10 구성요소, 11 파일, 누락 0, Electron/Chromium 고지 포함 | native 라이브러리 개별 조건·공개 서명 승인은 별도 |

U025의 pending 네 셀과 U015 저장 큐 열기의 invalid 한 셀을 실제 실행 근거로 verified에 반영했다. 전체 1,449개 동작 시나리오는 **verified 1,013 / pending 436**이다. 이는 전체 기능이나 상위 작업의 최종 승인 수가 아니다.

FlowchartStudio의 현재 inventory 파일 해시를 startup 수정본에 맞췄다. 기존 13개 action의 49 verified / 42 pending과 과거 실행 커밋·receipt는 그대로 보존했다. 과거 49개를 현재 버전에서 다시 실행한 것으로 표시하지 않는다.

기존 82개 상위 작업의 implementation verified 70 / pending 12와 Windows 두 항목의 사용자 면제는 그대로다. 남은 상위 10개는 로컬 후속 검증과 장비·서명·사람의 승인 조건이 섞인 묶음이다. 현재 코드가 올라간 hosted CI는 결과가 도착하기 전까지 pending으로 유지한다.

실행 근거는 `docs/verification/receipts/2026-10-19-*`에 보존한다. 이전 실패와 중단 기록도 삭제하거나 성공으로 덮어쓰지 않았다.
