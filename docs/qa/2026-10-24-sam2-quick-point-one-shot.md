# SAM2 새 점 입력 경로 검증

2026-10-24 KST, `11f21993187e03713354f0a76a2a52a7540ec921` 기반 작업 트리에서
새 `foundation-quick-point-owned.spec.ts` 브라우저 검사만 실행했다.
**최종 1 passed / 0 skipped / 0 flaky, 22.8초, exit 0.**
각 실행은 명시적으로 승인한 기존 SAM2 cache로 CPU 추론 한 번만 수행했다.
아래 두 실패 기록도 원래 상태로 보존하며 최종 통과와 합산하지 않는다.

| 실행 | 결과 | 조치 |
| --- | --- | --- |
| `sam2-owned-point/2026-10-23T16-49-57-454Z-30618` | 실제 pending 생성 성공, 성공 안내 문구 단언 실패 | 패널 자동 열림의 목록 재조회가 안내를 지우는 UI 버그 수정 |
| `sam2-owned-point-after-fix/2026-10-23T16-57-35-799Z-32376` | 안내·실제 preview·pending API/파일 검사 통과, 전체 annotations 바이트 단언 실패 | 최초 팀 설정 GET보다 앞선 baseline 순서 보완 |
| `sam2-owned-point-final/2026-10-23T17-00-44-605Z-36497` | 모든 명시 단언 통과 | 추가 실행 없음 |

두 번째 실행은 image row 전체·revision `1`·라벨 해시·감사 이력이 그대로였다.
최초 `GET /api/team-data`(16:58:06.341Z)가 기본 팀 설정을 초기화했고 생성 POST는
그 뒤인 16:58:06.973Z였다. 새 `team_data`만 제거해 엔진과 같은 방식으로 직렬화한 SHA256이
이전 workflow 파일 SHA256 `1a46dd5d3e386f4c5b11ee473cc83e588da779654008de9cd5beb0663609afd8`와
정확히 같았다. 사후 annotation API 응답도 사전 응답과 같았다.
최종 검사는 실제 팀 설정 GET과 이미지 metadata 준비 뒤 전체 annotations 바이트 baseline을 잡는다.
부모 폴더·ledger를 비교 대상에서 제외하지 않았으며 revision·audit·annotation_hash 불변도 추가했다.

새 테스트는 `@owned-model` 태그와 `MV_E2E_SAM2_MODEL_DIR` 환경 게이트를 사용한다.
HTTP 공급자 모의 응답 없이 격리된 실제 프로젝트·백엔드·Canvas·store·panel을 사용했다.
데스크톱 호스트 브리지만 기존 브라우저 shim이다. Electron·Windows 검증은 아니다.

- 입력: 새 256×256 합성 이미지, 밝은 배경에 어두운 사각형, 별도의 기존 LabelMe 라벨 1개.
- 설정: CPU, 빈 텍스트, positive 점 원본 좌표 `(128,128)`, threshold `0`, max 후보 `1`, polygon.
- 기본 꺼짐을 확인하고 opt-in을 켠 뒤 패널을 닫아 실제 Canvas 점으로 생성했다.
- 최종 실제 제안 `suggestion_79e942a12f404625a944dba6`은 pending 후보 1개이며
  polygon은 `(65,64), (64,192), (192,192), (192,64)`다. provenance는 `sam2`, `cpu`, 지정 cache다.
- 성공 안내 유지, 기존 preview 이미지의 실제 너비와 viewport 표시, polygon 렌더,
  pending API·목록·실제 파일 일치를 확인했다.
- 원본·기존 LabelMe·전체 annotations 파일 바이트와 annotation API 응답이 모두 같다.
  ledger 전체와 image row의 revision·audit·annotation_hash도 같다.
- 생성 POST 정확히 1개, `accepted_candidate_ids=[]`, 자동 채택·라벨 저장·학습 요청 0개다.
- page error·console error·차단된 loopback 요청·잔여 프로세스는 0개다.
  소유 백엔드는 정상 종료했고 포트가 닫혔다. 실제 모델 품질 승인은 아니다.

성공 안내 수정은 자동 열림 한 번에 한해 생성한 proposal ID와 프로젝트·이미지·라벨 세트
context가 일치할 때 안내를 유지한다. 닫거나 context를 바꾸면 소모하며 이전 안내를 재생하지 않는다.
실제 Panel+Controls/controller 검사에 RED 재현을 남겼고, 수정 뒤 focused 32/32와 renderer·E2E
TypeScript 검사가 통과했다. 기존 notice·원본·라벨 보존 단언은 유지했다.

모델은 기존 `facebook/sam2.1-hiera-tiny` revision
`de431c4043854a71d8101e17995dfe596bf101a5`를 사용했다.
HF·Transformers·Datasets offline을 켰고 엔진도 `local_files_only=True`로 로드했다.
가중치 SHA256은 `48c14467e5cf9e51870511feb72c89688e82dd74523142c0538b663e193ac2a7`이며
config·preprocessor·가중치 해시가 실행 전후 같다. 다운로드·외부 이미지 전송·다른 모델·학습은 없다.
Python 3.13.5, transformers 4.57.6, torch 2.9.1, torchvision 0.24.1, safetensors 0.7.0,
Pillow 12.3.0을 사용했다. 새 의존성은 설치하지 않았다.

재현 환경의 `$PROJECT_ROOT`는 해당 checkout, `$PROJECT_PYTHON`은 기존 backend Python,
`$MODEL_CACHE_DIR`은 명시적으로 허용한 기존 snapshot, `$PRIVATE_QA_ROOT`는 비공개 결과 폴더다.
실제 절대 경로는 private receipt에만 보존한다. 다음 명령은 새 추론을 실행하므로 추가 실행은 별도 범위다.

```sh
cd "$PROJECT_ROOT"
PYTHONDONTWRITEBYTECODE=1 \
MV_E2E_PYTHON="$PROJECT_PYTHON" \
MV_E2E_ARTIFACT_DIR="$PRIVATE_QA_ROOT/sam2-owned-point-final" \
MV_E2E_SAM2_MODEL_DIR="$MODEL_CACHE_DIR" \
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_DATASETS_OFFLINE=1 \
OMP_NUM_THREADS=2 MKL_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2 \
node node_modules/@playwright/test/cli.js test --project=browser --workers=1 \
scripts/e2e/foundation-quick-point-owned.spec.ts
```

최종 spec SHA256은 `82660a3f17d34144a210bcd1024c513ad4c9d476d09b0c06124c2a298075b016`,
`ModelAssistPanel.tsx`는 `ac2c864bba86dbce773220fa2bb88c3f3236a32861838c84fb5ecbf71adfc8c8`다.
private summary에 controller·controls·store·Canvas·backend의 source fingerprint도 있다.

증거는 `$PRIVATE_QA_ROOT/sam2-owned-point-final-summary.json`과
`sam2-owned-point-final.log`, 최종 실행 폴더의 `report.json`, `teardown.json`,
`results/**/harness-manifest.json`, 실제 preview 화면이다.
첫 실패는 `sam2-owned-point-audit.json`, 두 번째 실패는 `sam2-owned-point-after-fix-audit.json` 및
각 원래 log·report·trace·실패 화면을 보존했다. RED·focused·renderer/E2E 타입 로그도 비공개로 보존했다.
