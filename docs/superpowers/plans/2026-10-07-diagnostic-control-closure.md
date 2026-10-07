# 진단 자료 화면의 미검증 경로 실행

기존15개 미완료 부모 중 S7-01의 U025를 진행한다. 제품 기능과 새 권한은 추가하지 않는다.

1. 격리한 actual 프로젝트를 만든다. 앱은 시작할 때 Default Project를 자동 생성하므로 초기 화면을 프로젝트 없는 상태라고 가정하지 않는다. 최초071695f의 해당 가정 실패는 보존하고 empty-project 차원을 승격하지 않는다.
2. 설치 조회의 명시적 transport503을 유지한 뒤 창 닫기·직접 재열기로 실제 backend metadata를 다시 읽는다.
3. 실제 첫 packages bundle을 저장해 download와 persisted JSON을 비교한다. 미제출 section 변경·취소·재열기에 원래 bundle이 유지돼야 한다.
4. 빈 section은 저장이 막힌다. 실제 diagnostics POST를 지연한 명시적503에서는 controls가 잠기며 기존 bundle과 source bytes가 유지돼야 한다.
5. fixture를 제거한 뒤 같은 selected section을 직접 재시도한다. 실제 redacted download와 persisted JSON·원본 hash를 대조하고 재열기가 추가 download/export를 하지 않는지 확인한다.
6. Browser와 Electron에서 exact committed spec을 각각 실행한다. Screenshot·request·disk hash·owned teardown을 보존하고 실제 수행한 차원만 원장에 추가한다. Native Download counter는 main session의 실제 will-download event다.

통신503은 controlled fixture다. 인간 품질 승인·외부 업로드·추가 학습·Windows 실기·실 publisher·전체 부모 수락은 이 검사에 포함하지 않는다. 이전 receipts의 spec을 수정하지 않는다.
