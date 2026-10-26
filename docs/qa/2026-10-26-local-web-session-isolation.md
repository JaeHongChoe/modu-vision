# Localhost renderer 빌드 자식의 공통 격리 계약

이전 통합 head `6fdaf55e`의 full run `40324872866`은 CPU 시험 5,338개 통과·17개 skip·1개 실패였습니다. 실패는 새 renderer builder Popen이 프로젝트 공통 `session_isolation()`을 사용하지 않은 계약 검사였습니다. 브라우저의 별도 소스 핀 실패 9건과 이 CPU 실패는 구분해 보존합니다.

`backend/local_web_runtime.py`의 `_run_renderer_builder` 안에서 표준 라이브러리만 사용하는 `backend.engine.process_isolation.session_isolation`을 가져와 Popen에 `**session_isolation()`을 전달합니다. 직접 지정하던 `start_new_session`만 공통 함수로 바꿨습니다. POSIX에서 옵션은 기존과 동일한 `{'start_new_session': True}`입니다. 그룹 PID 소유, spawn 구간 신호 차단, TERM/KILL·reap, 반복 신호 처리와 잠금 수명은 바꾸지 않았습니다. 도움말·저장 경로 설정 전에 engine을 가져오지도 않습니다. Windows TLS 키 ACL 제한과 미지원 상태는 유지합니다.

기존 CPU 계약 시험을 통합 작업트리에서 먼저 실행해 동일한 실패 1건과 exit 1을 확인했습니다. 수정 뒤 local HTTPS·계정·보안·수명주기 시험 175개와 공통 격리 계약 3개를 합쳐 **178개 통과, exit 0**을 확인했습니다. 실제 학습을 실행하는 S1-09 시험은 이 로컬 보완 검증에서 선택하지 않았습니다. 합성 builder의 자손·SIGTERM·잠금·무관 프로세스 보존 검사와 실제 격리 HTTPS 테스트는 기존대로 통과했습니다.

실제 합성 모델·평가의 원래 소스 `069d9b26`, 마지막 실제 브라우저 읽기 소스 `df55d815`와 저장 해시를 바꾸지 않았습니다. 이 두 줄 보완 때문에 모델을 다시 실행하지 않았고, 새로운 모델 품질·OS·현장·설치 패키지 승인을 주장하지 않습니다. 원본 RED/GREEN·선택 목록·종료 코드는 로컬 통합 검증 폴더의 `isolation-contract-*`에 보존합니다. 최종 head의 hosted full·fast·local-browser 검사와 병합은 별도로 확인합니다.
