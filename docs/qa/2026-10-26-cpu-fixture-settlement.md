# 소유 CPU 작업 종료·전환 시험의 결정적 경계

이 변경은 시험 fixture의 순서와 작업량만 조정합니다. 제품의 종료·복구·학습·
소유권·lease 검사는 바꾸지 않았고, 기존 실패한 원격 실행 기록도 보존합니다.

## 손상된 종료 프레임

이전 PR11 전체 회귀40189873930은 changed_nonce 경우에 실제 종료 가드가
거절한 뒤 일반 종료 요청을 관찰하는 파일을3초 안에 받지 못했습니다. 기존
fixture의 허용된 실제 예외와 관찰 순서가 경합했습니다.

원래 가드의 post-send prepared 경계만 기존 프로파일 방식으로 기다립니다.
다른 코드·객체·스레드·phase, 응답 전 prepared 호출은 선택하지 않습니다.
실제 refusal·ordinary-stop 관찰 뒤 원래 가드를 실행하며 예외 스택을 유지합니다.
독립 검토에서 발견한 parent의 관찰 파일 순서 경합도 같은3초 절대 시한 안에서
기다리도록 수정했습니다.3초/4초 한도와 거절·registry·원본 바이트 단언은 그대로입니다.

준비 경계의 결정적 RED6개와 검토 수정 RED2개를 기록했습니다. 원래 두 손상 프레임,
정상/unsupported 실제 소스 종료, 기존 supervisor/dispatcher/cleanup 대조를 통과했습니다.
검토 수정 후 helper/관련17개와 원래 두 실제 경우도 통과했습니다. 겹치는 실행을
합산한 고유 시험 수나 원격 성공으로 해석하지 않습니다.

## 실제 학습 worker의 live cutover

이전 PR12 전체 회귀40191815419는 thread.join(60) 뒤 worker 관찰 스레드가
살아 있었습니다. 오류가 없다는 로그만으로 느린 학습과 정체를 구분할 수 없고,
이전 원격 실패의 원인은 아직 확정하지 않습니다.

새 fixture는 실제 첫 optimizer 단계와 원래 status callback의 정상 반환 뒤에
한 번만 대기합니다. 임시 sitecustomize는 원래 -m CLI·handshake·trainer·callback을
교체하지 않습니다. callback이 기록 뒤 예외를 던진 경우에는 대기 경계가 성공하지
않습니다. parent가 실제 PID/생성 시각·명령 해시·job/spec·준비 응답·시도·fence를
확인한 뒤 동일 worker를 가진 채 전환하고 해제합니다.

생성 이미지8장,32px,3에폭의 실제 CPU 학습으로 필요한 전후 진행을 확인합니다.
준비20초 시한은 시작 전에 정해 child까지 그대로 전달하고, 종료 join60과 원래
단일 시도·중복 실행 거절·원본/이전 제어 바이트·실제 exit·복구 단언을 유지합니다.
실패하면 새 요청/연장 없이 기존 status·진행·worker log·관찰 스택을 제한된 크기로
추가합니다. 정리 중 오류가 최초 실패를 덮지 않습니다.

최종 검토에서 helper 단위 시험의 짧은 실제 시계가 정상 I/O 지연을 실패로
만들 수 있음을 확인했습니다. 단위 경계는 논리 시계로 순서를 제어하도록 고쳤고
실제 callback/I/O와 통합20초/60초 한도는 유지했습니다. 결정적 RED2건 뒤
helper15개 및 실제 cutover/재연결2개가 통과했으며, 좁은 재검토도 승인됐습니다.

- `python -m pytest -q backend/tests/test_live_control_migration.py backend/tests/test_local_training_process.py --durations=10` → **51 passed**.
- `python -m pytest -q backend/tests/test_global_migration.py backend/tests/test_live_control_migration.py -k 'not actual_cpu' --durations=5` → **68 passed, 1 deselected**.
- 최종 실제 cutover 호출10.36초, trainer 기록2.572초. 동일 worker가 epoch1/step1에서
  전환을 지나 epoch3/global-step6으로 완료되고 worker_exit_confirmed가 참인 것을 확인했습니다.

기존 환경의 python_multipart 경고는 남아 있습니다. 위 실행은 생성 자료의 소스
실행 검증이며 모델 품질·현장 승인·원격 CI·병합·배포 완료를 뜻하지 않습니다.
독립 검토와 최종 공개 head의 hosted CI 결과는 해당 PR에 별도로 기록합니다.
