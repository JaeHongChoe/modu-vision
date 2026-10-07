# 남은 작업과 사용자 면제 범위

2026-10-07. 사용자의 직접 지시 `원도우 테스트는 안해도 되니깐 남은 잡들 진행해죠`를 `user-scope-decisions.json`에 기록했다. 요청된 작업에서 Windows 실기 전용 부모 S6-02·S7-03 두 개를 면제한다. 원래 `windows_native=pending`과 제품 수락 기준은 그대로 보존하며 면제를 pass로 집계하지 않는다. 다른 부모에 포함된 품질·장비·서명 조건까지 면제하지 않는다.

현재 파일을 읽은 보고서의 값은 원래 부모 **82개, 구현 검증 69개, 구현 pending 13개, 종합 수락 0개**이다. 사용자 요청 범위에서는 이 pending 13개 중 **면제 2개·계속 필요한 11개**다. 기존 기능 ID 156개도 모두 유지한다. 13개는 미작성 기능 수가 아니며, 아래 소프트웨어 후속 작업과 외부 수락 조건을 함께 가진 부모 집계다.

`python3 scripts/remaining_work_report.py`는 원장과 행동 registry의 실제 파일을 읽고 입력 SHA-256과 현재 수를 출력한다. 저장된 부모 수가 실제 행과 다르거나 bool로 기록됐으면 실패한다. 면제의 직접 지시·대상·상태가 확대되거나 pass로 바뀐 기록도 거절한다. 보고서는 원래 program이나 수락 gate를 쓰지 않는다. 이후 수는 이 명령으로 다시 읽으며 이 문서의 snapshot을 최신 실행 증거로 사용하지 않는다.

2026-10-08 소스fe92877까지의 추가 실행 뒤 curated 행동은 **207개·검증 572개·pending 877개·not_required 0개**다. 이전369b8e1에서는 새6개 action에42차원을 추가하고 실제19개를 검증했다. 이후1a9b830의 launch action2개에14차원을 추가하고 실제5개를 검증해 나머지9개를 pending으로 유지한다. fe92877에서 metadata picker의 오류·취소·전달3차원을 실제 browser와 native로 추가 확인했다. 이 수는 부모나 전체 기능 수락 수에 더하지 않는다. 실제 slice 기록은18개이며 원장의 별도 요약은 병합 slice16개·추가 개선 영역2개다. 부모와 겹치는 구현 기록으로 독립 수락이나 서로 더할 수 있는 진척 수가 아니다.

S6-03의 inert runtime pack API/GUI는 source `5284df6e979d4d4ceadc0a8256b5a90176adbbe6`에 연결된 근거가 이미 있다. 깨끗한 browser/native 검사 5개에 pack 설치·pin·integrity readback과 진단·지원 동선이 포함되며 공개 receipt는 `2026-10-07-runtime-pack-studio-closure-5284df6.json`이다. 이 동선을 미작성 API로 취급하지 않는다. S6-04의 portable 앱/DB cutover·검토·복구도 구현되어 있다. 영속 controller·native caller·main/backend 인증 handshake는1a9b830에서 검증했다. 실제 기준 이미지 실행·pointer 변경 전 candidate 검사·전체 process-tree reconciliation·native packaged positive startup·OS installer adapter를 이어간다.

| 계속 필요한 부모 | 소프트웨어 후속 작업 | 외부 수락 조건 |
|---|---|---|
| S5-01 | 독립 서비스 lifecycle 근거 연결 | 전용 target 계정·재부팅·장비 권한·GPU/camera 운영 |
| S6-03 | 검증된 inert pack binding 보존·추가 선언 provider/target qualification | 다른 장치·대표 품질·native license/publisher |
| S6-04 | 기존 cutover/영속 인증 controller 보존·실제 CPU 기준 이미지·활성화 전 canary·전체 process-tree·OS installer adapter | 실 publisher 서명·native positive 설치·installed target 수락 |
| S6-05 | 새 게시 source와 complete hosted CI 연결 | 실제 게시와 해당 runner 완료 |
| S6-06 | checksum/source/unsigned/channel 상태 표시 | publisher·서명 키·release 운영 정책 |
| S7-01 | 남은 행동 시나리오·누락 메뉴/shortcut 실행 | 전체 기능 target 수락 |
| S7-02 | 모델 lifecycle·저장된 multi-model package 근거 | 사람이 검토한 대표 task별 truth·품질 판정 |
| S7-05 | 소스fe92877의10만 metadata/실제 이미지3장 GUI paging·검색·선택 복원·오류·취소·전달 근거 보존 | 실제 사진 규모·같은72시간 운전 terminal receipt·target 자원/택트 |
| S7-06 | simulator·stale truth/권한 gate 보존 | 실제 camera/PLC/MES·제품/Lot 정답·공정 품질 승인 |
| S7-07 | exact source/artifact/coverage/복구 검토 | 서명·native 사용 조건·supported target·독립 최종 리뷰·pilot |
| S7-08 | 실제 관찰을 기록할 pilot/지원/backup 절차 | 처음 쓰는 참여자 pilot·운영 책임·측정된 SLA |

이 추가 보고서는 현재 구현을 독립적으로 승인하지 않는다. 물리 장애·대표 품질·실 publisher·pilot 참여자·72시간 경과의 근거는 생성하지 않는다. 관련 회귀 검사는 원장 복사본에서 집계 오류, 면제 확대/위조, pass 재표기, source 변조와 입력 bytes 보존을 확인한다.

2026-10-08 추가 판정: S1-08과 S7-04는 독립 검토와 최신 소스의 실제 회귀를 거쳐 **구현만 verified**로 옮겼다. migration26개, 장애 matrix61개와 focused44개가 통과했다. 다른8개 gate·실 사용자 home·지원하지 않는 worker·물리 target·실 publisher update는 pending을 유지한다. 공개 receipt는 `2026-10-08-migration-implementation-1a9b830.json`과 `2026-10-08-software-fault-matrix-213e9b7.json`이다. 원래 전체 수락이나 면제의 의미를 바꾸지 않는다.
