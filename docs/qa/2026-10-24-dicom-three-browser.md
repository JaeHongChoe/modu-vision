# DICOM 브라우저 3건 격리 검증

2026-10-24 01:13 KST, `c592b0456ed198a96e7a0e31b5354245646abf68` 기반의 작업 중인 트리에서 아래 세 건만 실행했다. **3 passed / 0 skipped / 0 flaky, 39.6초, exit 0.** 제품·e2e 코드 수정 없이 의존성 누락을 QA 전용 환경으로 보완한 결과다.

- `dicom-first-frame-thumbnail-owned-abort.spec.ts:172`: 12.7초
- `dicom-integrity-rle.spec.ts:56`: 9.1초
- `dicom-integrity-rle.spec.ts:65`: 8.3초

QA 환경은 `/private/tmp/modu-vision-dicom-qa-20261024-4bnzdsv9/venv/bin/python`이며 Python 3.13.5, NumPy 2.2.6, Node v26.4.0, Playwright 1.63.0을 사용했다. 기존 `.venv`와 같은 시스템 패키지를 읽되 [공식 PyPI pydicom 3.0.2](https://pypi.org/project/pydicom/3.0.2/)만 전용 venv에 설치했다. Wheel SHA256 `abf971a5440f84dbaf42c4b6758e30e62480902584f8b270b9a5d146e278a07b`는 Ubuntu·Windows CI lock과 일치한다. 첫 격리 실행 전후 기존 `.venv` 의존성 목록 해시는 동일했고, 그 시점에는 `pydicom`이 없었다. 모델은 내려받지 않았다.

위 문단은 첫 격리 실행 당시의 상태다. 이후 소유 Electron QA 종료를 확인한 뒤,
같은 wheel을 `--no-deps --require-hashes`로 프로젝트 `.venv`에도 설치했다.
설치 보고서의 추가 항목은 `pydicom==3.0.2` 하나이며 실제 import 경로가
프로젝트 `.venv/lib/python3.13/site-packages/pydicom`임을 확인했다.
base Anaconda와 다른 패키지는 변경하지 않았다. 설치 보고서·검증 기록과
첫 실행의 작은 증거 파일 14개는
`$PRIVATE_QA_ROOT`(실제 경로는 개인 receipt manifest에 기록)에
해시를 비교해 별도로 보존했다. 이 프로젝트 `.venv`를 `MV_E2E_PYTHON`으로
지정한 후속 실행도 **3 passed / 36.9초 / exit 0**이다(각 12.5초·7.6초·7.3초).
위 보존 폴더의 `dicom-project-runtime.log`, `dicom-project-runtime-exit.json`,
`dicom-project-e2e/`에 실제 런타임 결과가 있다.

합성 비환자 원본은 보존됐고, 잘못된 window·frame과 의도한 요청 중단 뒤 기존 표시가 유지됐다. 열린 FD 165개는 모두 닫혔고 모든 요청 관찰자·핸들러가 제거됐다. 각 소유 백엔드는 강제 종료 없이 끝났으며 포트 폐쇄·잔여 프로세스 0건을 확인했다. 다른 앱은 중단하지 않았다. Electron·Windows 설치·다른 압축 디코더·임상 품질·프레임 라벨링은 이번 세 건의 검증 범위가 아니다.

재현: `/private/tmp/modu-vision-dicom-qa-20261024-4bnzdsv9/run-dicom-only.sh`. 내부 명령은 `MV_E2E_PYTHON`을 위 인터프리터로 지정한 뒤 `node node_modules/@playwright/test/cli.js test --project=browser --workers=1 scripts/e2e/dicom-first-frame-thumbnail-owned-abort.spec.ts:172 scripts/e2e/dicom-integrity-rle.spec.ts:56 scripts/e2e/dicom-integrity-rle.spec.ts:65`다. 각 실행은 새 식별자를 사용한다.

증거 루트: `/private/tmp/modu-vision-dicom-qa-20261024-4bnzdsv9/`. `README.md`에 전체 환경·재현 설명, `playwright-three.log`에 실행 결과, `verification-summary.json`에 의존성 불변·원본 보존·정리 결과, `pip-install-report.json`에 고정 패키지 출처가 있다. 원본 보고서는 `e2e/dicom-three-20261023T161312Z-11247/report.json`, `preflight.json`, `teardown.json`, `results/**/harness-manifest.json`이다. 임시 경로이므로 장기 증거 보관 시 별도 보존이 필요하다.
