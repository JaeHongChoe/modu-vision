# 라벨 보관·복구 API

`DELETE /api/annotations/{image_id}`는 Studio overlay JSON과 마스크를 먼저
보관한 뒤 활성 파일에서 제거한다. 이미지 원본과 이미지 옆의 원본 정답 파일은
수정하지 않는다. 응답의 `status: "deleted"`는 기존 계약을 유지하며, 실제 파일을
보관했을 때만 `recovery_id`가 추가된다. 파일이 없으면 `not_found`다.

프로젝트 요청은 `image_path`, 최신 `expected_revision`, 작업자 `actor`가 필요하다.
팀 편집을 켰으면 기존 `lease_token`도 필요하다. 공유 서버의 작업자는 요청값이
아닌 인증한 계정이다. 팀/라벨북 보호와 승인 취소는 저장 경로와 같다. 같은 폴더에
`a.png`, `a.jpg`가 함께 있어 overlay 이름이 겹치면 작업하지 않는다.

- `GET /api/annotations/{image_id}/recovery?image_path=...`: 보관 시각·작업자,
  파일 크기/SHA256, 복구 이력을 읽는다. 프로젝트·라벨 세트 범위를 따른다.
- `POST /api/annotations/{image_id}/restore/{recovery_id}`: JSON body에
  `image_path`, 최신 `expected_revision`, `actor`, 필요시 `lease_token`을 보낸다.
  활성 JSON이나 마스크가 하나라도 있으면 덮어쓰지 않고 409를 반환한다.
  원본 이미지 UUID/내용 SHA256이 바뀌었거나 사본 크기/해시가 다르면 복구하지 않는다.

성공한 프로젝트 작업은 장부에 `annotation_archived` / `annotation_restored`와
복구 번호·실제 작업자를 기록하고 수정 번호를 올린다. 복구 후에도 `needs_review`이며
이전 사람 검토·평가/승인이 자동으로 되살아나지 않는다. 사본은 복구 후에도 남는다.
아직 이 API를 도우미 도구로 열거나 화면에 삭제 버튼을 추가하지 않았다.

프로젝트 사본은 현재 라벨의 `by_dataset` 밖, 해당 라벨 저장 범위의
`.recovery/<overlay-key>/<recovery-id>/`에 둔다. 데이터 버전이 overlay를 교체해도
사본이 남으며 학습/데이터 지문/버전 스캐너의 숨김 폴더 제외를 그대로 따른다.
레거시 비프로젝트 API는 지정한 허용 라벨 폴더의 `.recovery/`를 쓰며 장부 대신
manifest에 작업자·복구 이력을 남긴다. 레거시에는 프로젝트 수정 번호/이미지
지문 보장이 없으므로 프로젝트 경로 사용을 우선한다.

JSON·마스크·복구 기록은 기존 파일 트랜잭션을 통해 발행한다. 처리 중 파일이나
장부 저장이 실패하면 같은 잠금 안에서 원래 바이트로 되돌린다. 사본이 완성되기
전에는 활성 파일을 지우지 않는다. 운영체제 강제 종료까지 포함한 다중 파일
crash-atomic 저장소는 아니며, 이 보장을 그렇게 확대하지 않는다. 사본 경로와
활성 파일의 심볼릭 링크는 거절한다.

집중 검사: `backend/tests/test_annotation_recovery.py` 및 기존
`test_annotation_save_transactions.py`, `test_annotation_batch_integrity.py`,
`test_annotation_path_security.py`, `test_team_data.py`. 실행 결과는 이번 작업의
통합 검증 기록과 해당 CI head를 함께 확인한다.
