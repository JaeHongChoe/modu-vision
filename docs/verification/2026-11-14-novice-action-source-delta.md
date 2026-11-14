# 초보자 UI 변경의 현재 소스 연결 감사

`81b61d4a`의 fast run 43587110660 / job 132423881993은 `check_action_evidence.py`에서 현재 소스 바이트 3개가 등록 해시와 다르다고 보고했다. 해당 파일들의 첫 사용 동선 변경과 UI 정리 내용을 검토하고 `docs/service-action-evidence.json`의 `sources` 항목 세 개만 갱신했다.

| 소스 | 영향받는 기존 선언 | 현재 SHA256 |
| --- | --- | --- |
| ProjectWorkspaceDialog.tsx | U030/project-archive, U030/fresh-project-restore | `e78ec6763473076aee46e19ec48d1e0b2e161be483183994c41670cbf9bd494a` |
| TrainingController.tsx | F025/dino-classification-native-start, F027/dino-segmentation-native-start | `3fe82096e5505c0922cd12a774524acb7acc846676c9e47fadc21cb9edd34605` |
| WizardHeader.tsx | F025/dino-classification-native-evaluation | `7ea4b336da2f1737b7aac01b560430bcc391388609f5e751b79ba1f8c790203f` |

프로젝트 관리의 백업/복원 핸들러는 유지되며, `noValidate`를 더한 폼에 기존 required/pattern/type 검증은 없었다. 오류 스크롤은 표시만 바꾼다. 학습 controller는 앞선 초보자 구현에서 준비 안내와 복구 위치를 연결했고 모델 시작·권한 검증을 변경하지 않았다. Header는 기존 `setStep` 호출을 유지하며 화면 구조와 표현을 바꿨다.

이 갱신은 native DINO 학습·평가나 백업/복원 전체를 새로 실행했다는 증거가 아니다. 두 native-start 선언의 7개 차원은 전부 pending으로 유지한다. 평가 success/reopen과 백업/복원에 남은 verified는 원래 `0eadf8c9…`, `0f3be05…`, `5e96e5e…`의 역사적 실행 근거다. 원래 `source_sha`, 테스트, 영수증·그림 해시, 상태와 미검증 사유를 그대로 보존했다.

모든 `records`의 정렬·압축 JSON SHA256은 전후 동일한 `bd12281aa7acaf83ef682432d52ae5a39dbd8c3520e7afe644ad4048ca4bc5fe`다. 검사기는 207 actions, 1,011 verified scenarios, 438 pending scenarios, **accepted_features=0**을 유지한다. 원본 CI 로그와 정확 head의 불일치 목록은 `.superpowers/sdd/2026-11-14-novice-ui-polish/ci-81b61d4a-*`에 보존한다.
