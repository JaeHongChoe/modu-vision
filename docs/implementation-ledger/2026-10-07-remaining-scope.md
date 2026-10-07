# 남은 작업과 사용자 면제 범위

2026-10-07. 사용자의 직접 지시 `원도우 테스트는 안해도 되니깐 남은 잡들 진행해죠`를 `user-scope-decisions.json`에 기록했다. 요청된 작업에서 Windows 실기 전용 부모 S6-02·S7-03 두 개를 면제한다. 원래 `windows_native=pending`과 제품 수락 기준은 그대로 보존하며 면제를 pass로 집계하지 않는다. 다른 부모에 포함된 품질·장비·서명 조건까지 면제하지 않는다.

현재 파일을 읽은 보고서의 값은 원래 부모 **82개, 구현 검증 67개, 구현 pending 15개, 종합 수락 0개**이다. 사용자 요청 범위에서는 이 pending 15개 중 **면제 2개·계속 필요한 13개**다. 기존 기능 ID 156개도 모두 유지한다. 15개는 미작성 기능 수가 아니며, 아래 소프트웨어 후속 작업과 외부 수락 조건을 함께 가진 부모 집계다.

`python3 scripts/remaining_work_report.py`는 원장과 행동 registry의 실제 파일을 읽고 입력 SHA-256과 현재 수를 출력한다. 저장된 부모 수가 실제 행과 다르거나 bool로 기록됐으면 실패한다. 면제의 직접 지시·대상·상태가 확대되거나 pass로 바뀐 기록도 거절한다. 보고서는 원래 program이나 수락 gate를 쓰지 않는다. 이후 수는 이 명령으로 다시 읽으며 이 문서의 snapshot을 최신 실행 증거로 사용하지 않는다.

현재 curated 행동은 **199개·검증 545개·pending 848개·not_required 0개**다. 이 시나리오 수는 부모나 전체 기능 수락 수에 더하지 않는다. 실제 slice 기록은 18개이며 원장의 별도 요약은 병합 slice 16개·추가 개선 영역 2개다. 이 기록들은 부모와 겹치는 구현 주장으로, 독립 수락이나 서로 더할 수 있는 진척 수가 아니다.

S6-03의 inert runtime pack API/GUI는 source `5284df6e979d4d4ceadc0a8256b5a90176adbbe6`에 연결된 근거가 이미 있다. 깨끗한 browser/native 검사 5개에 pack 설치·pin·integrity readback과 진단·지원 동선이 포함되며 공개 receipt는 `2026-10-07-runtime-pack-studio-closure-5284df6.json`이다. 이 동선을 미작성 API로 취급하지 않는다. S6-04의 portable 앱/DB cutover·검토·복구도 구현되어 있다. 별도로 남은 durable updated-app ownership/supervisor/native caller·기준 이미지 handoff 및 OS installer adapter를 추적한다.

| 계속 필요한 부모 | 소프트웨어 후속 작업 | 외부 수락 조건 |
|---|---|---|
| S1-08 | 이력 binding·지원하지 않는 worker의 이전 경계 검토 | 설치된 native target 이관·독립 수락 |
| S5-01 | 독립 서비스 lifecycle 근거 연결 | 전용 target 계정·재부팅·장비 권한·GPU/camera 운영 |
| S6-03 | 검증된 inert pack binding 보존·추가 선언 provider/target qualification | 다른 장치·대표 품질·native license/publisher |
| S6-04 | 기존 cutover/복구 보존·durable launch ownership/supervisor/native caller·기준 이미지 handoff·OS installer adapter | 실 publisher 서명·native positive 설치·installed target 수락 |
| S6-05 | 새 게시 source와 complete hosted CI 연결 | 실제 게시와 해당 runner 완료 |
| S6-06 | checksum/source/unsigned/channel 상태 표시 | publisher·서명 키·release 운영 정책 |
| S7-01 | 남은 행동 시나리오·누락 메뉴/shortcut 실행 | 전체 기능 target 수락 |
| S7-02 | 모델 lifecycle·저장된 multi-model package 근거 | 사람이 검토한 대표 task별 truth·품질 판정 |
| S7-04 | 로컬 장애 matrix·별도 팀 계정 충돌/재시작 | 물리 target 장애·실제 서명 native update |
| S7-05 | GUI metadata paging/검색 측정 | 같은 72시간 운전의 terminal receipt·target 자원/택트 |
| S7-06 | simulator·stale truth/권한 gate 보존 | 실제 camera/PLC/MES·제품/Lot 정답·공정 품질 승인 |
| S7-07 | exact source/artifact/coverage/복구 검토 | 서명·native 사용 조건·supported target·독립 최종 리뷰·pilot |
| S7-08 | 실제 관찰을 기록할 pilot/지원/backup 절차 | 처음 쓰는 참여자 pilot·운영 책임·측정된 SLA |

이 추가 보고서는 현재 구현을 독립적으로 승인하지 않는다. 물리 장애·대표 품질·실 publisher·pilot 참여자·72시간 경과의 근거는 생성하지 않는다. 관련 회귀 검사는 원장 복사본에서 집계 오류, 면제 확대/위조, pass 재표기, source 변조와 입력 bytes 보존을 확인한다.
