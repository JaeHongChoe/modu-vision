# PR16 공유 서버 소스 핀 갱신 검증

PR16의 공유 프로젝트 이미지 권한 검사와 raw 이미지의 private 캐시 정책으로
`backend/api/routes_dataset.py`가 바뀌었습니다. 기준 커밋은
`1000775a91fbbb1b76faf2529a3709808a93aff1`입니다. 현재 브라우저 시험 8개의
데이터 라우트 SHA만 아래와 같이 갱신했습니다.

- 이전: `c70d2649ece6307f8cea87c334f7353a71ede385cbefc86290c56d4d990a2440`
- 현재: `92df70e8ad696b3d1a81eab5c290132c0802768ffaaeaa778eb0aad76eeabca3`

8개 파일의 소스 핀 170개를 대조해, 변경 전에는 위 라우트 핀 8개만 불일치했고
변경 후에는 모두 일치함을 확인했습니다. 시험 파일의 나머지 바이트는 그대로여서
요청·관찰·단언·시간 제한·파일 보존 검사를 유지합니다.

`gui-test-source-history.json`의 기존 선언 147개 중 관련 17개에서
`current_test_source_sha256`과 변경 이유만 갱신했습니다. 나머지 130개 선언과
각 선언의 receipt, receipt SHA, 실행 소스·시험 이름, 기록된 시험 SHA,
보존 커밋은 같습니다. 기존 영수증 파일 953개의 SHA와 관련 조상 Git blob
17개의 SHA를 갱신 전후 대조했습니다. 보존 커밋은
`9a18c088eda03610546190b64c32a5bf129737af`와
`d45817a83d72793570f888a6fff4701fbddced7f`입니다.

핀만 바꾸고 과거 선언의 현재 시험 SHA를 그대로 둔 중간 상태에서 action 근거
게이트가 종료 코드 1과 정확한 과거 선언 필요 오류 61개로 거절했습니다.
17개 선언을 갱신한 후에는 통과했습니다. Service-plan 게이트는 이 중간 상태에서도
통과했으며, 갱신 후에도 통과했습니다. 이 선언들은 action 근거 게이트에서 검사합니다.

| 검증 | 결과 |
| --- | --- |
| 과거 시험 소스·GUI 영수증·collector·action 근거·S7-01 단위 검사 | 144 통과 |
| `scripts/check_service_plan.py` | 통과, accepted work packages 0 |
| `scripts/check_action_evidence.py` | 통과, 207 actions / verified 1,014 / pending 435 / accepted features 0 |
| `npm run typecheck:e2e` | 통과 |
| 기존 E2E harness 검사 | 64 통과 |
| 관련 브라우저 8개 파일의 기존 9개 시험 | 9 통과, skip·retry·flaky 0 |

브라우저 실행은 기존 의존성과 절대 경로의 지원 Python 환경을 사용했습니다.
`MV_E2E_RENDERER_DIR`를 제거하고 harness가 현재 소스로 별도 실행 디렉터리에
renderer를 새로 빌드했습니다. 기존 checkout의 `dist`는 사용하지 않았습니다.
실행 ID는 `pin-repair-20261026T140401Z`이며 preflight는 기준 커밋과 미커밋
핀 갱신 상태를 기록합니다. 종료 검사에서 잔여 소유 프로세스·외부 참조·조회 오류는
모두 없었습니다.

정확한 명령, RED/GREEN 출력, 변경 전 선언·영수증 해시와 보존 대조 결과는
로컬 `.superpowers/sdd/2026-10-26-local-web-integration/pin-repair-*`에 보관합니다.
브라우저 JSON report SHA는
`76e2a4d6eca365d2f94af7d9a020eb74f219249d30deacf3c5b1ac3f479a0be0`,
새 renderer index SHA는
`6acb8b594e6cf1debf414c983f10918bb190c927c2ebc23b63f0ad0b93ce2472`입니다.

이번 결과는 합성 입력의 통제된 로컬 브라우저 검증입니다. 과거 실행 영수증에 새
실행이나 현재·네이티브·사람·모델 품질 수락을 부여하지 않습니다. 고정 커밋의
hosted CI와 PR 병합은 별도로 확인합니다.
