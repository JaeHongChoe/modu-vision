# Modu-vision 작업표

확인일: 2026-10-06 · 기반 HEAD: `f9be229af937601c8f4a58bb6df6bfcc43f6f6d5` · Task135 구현 및 실제 검증 포함

**82개 중 소프트웨어 구현 확인 62개, 구현 미완료 20개.** S3-06 실제 프롬프트·few-label 학습과 S4-12 실제 AutoDL·원격 exact resume를 추가 확인했다. Windows 실제 설치·사용 QA(S7-03)는 사용자 면제이므로 진행 대상은 19개다. 최종 수용 승인은 0개다.

## 단계별 집계

| 단계 | 전체 | 구현 확인 | 미완료 |
|---|---:|---:|---:|
| S0 | 9 | 9 | 0 |
| S1 | 10 | 8 | 2 |
| S2 | 10 | 10 | 0 |
| S3 | 10 | 10 | 0 |
| S4 | 14 | 12 | 2 |
| S5 | 10 | 7 | 3 |
| S6 | 11 | 6 | 5 |
| S7 | 8 | 0 | 8 |

## 미완료 작업 20개

| ID | 작업 | 남은 조건 |
|---|---|---|
| S1-08 | 기존 JSON과 DB migration | migration dry-run→backup→count/hash/revision 비교→원자적 cutover→복구를 검증한다. 기존 원본·labels·split·모델·flow·결과·승인을 보존한다. / SQLite를 NAS에서 여러 호스트가 직접 열지 않는다. 최초 team 모드는 단일 API/scheduler, PostgreSQL adapter는 다중 API 요구와 부하 검증 후 지원한다. / migration 전에 writer 중지·job drain/저장·backup revision 고정·cutover journal을 수행한다. cutover 후 정상 쓰기가 발생하면 오래된 backup의 무조건 restore를 금지하고 검증된 역변환 또는 forward recovery를 선택한다. 기존 지원 backbone/decoder checkpoint의 offline 재구성·평가·flow/export compatibility를 fixture로 유지한다. 신규 adapter가 기존 config/좌표/score 의미를 바꾸면 명시적 version migration을 요구한다. / 앱/서버 전역 local_jobs·remote_jobs·resource leases·compute profiles·accounts/memberships도 dry-run migration한다. 실제 살아 있는 owned worker와 불확실 lease는 receipt/identity/fence로 adopt하고 중복 실행/고아 예약을 만들지 않는다. 복사본에서 타 설치의 token/lease를 새 실행 권한으로 활성화하지 않는다. |
| S1-09 | Windows 프로세스와 실행 entrypoint | Windows spawn/frozen entrypoint/DataLoader/경로 공백·한글/권한 제한에서 실행·종료·재시작이 동작한다. / 자식 process tree만 종료하며 다른 프로세스에 재사용된 PID는 종료하지 않는다. GPU pack 없는 CPU 설치에서도 실행된다. |
| S4-13 | 동일 cohort 모델과 전체 flow 평가 | 동일 테스트 cohort에서 모델 A/B·이미지 차이·class/제품/Lot 오류·threshold sweep·재평가 history를 제공한다. CLS/SEG/DET/OCR/OBB/회전/개선 metric을 task에 맞게 사용한다. / 전체 flow의 OK/NG/REVIEW·미검/과검·node/ROI 근거·truth coverage와 unavailable metric을 기록한다. calibration split과 test split 역할을 지킨다. / 서로 다른 task 모델은 동일 이미지의 공통 판정/오류 기준으로 비교한다. task 전용 metric의 직접 비교가 불가능하면 unavailable과 이유를 표시한다. Best/Important flag·부모 모델별 재평가·checkpoint/data/config/metric/prediction JSON export를 유지한다. threshold sweep/tuning의 기본 입력은 val/calibration이다. heldout test에서는 고정된 threshold/rules만 평가한다. exploratory test tuning을 하면 deployment-quality 근거에서 제외하고 새 untouched heldout을 요구한다. |
| S4-14 | 모델 및 전체 flow 승인 | 모델·다중 모델·ROI·전처리·분기·merge·decision rules가 함께 포함된 전체 flow를 승인 대상으로 지정한다. / 활성화/rollback·package parity·device acceptance·현재 승인 eligibility를 연결한다. graph나 threshold 변경은 새 평가/승인 요구로 표시하고 실행 패키지 구성과 checksum을 검증한다. |
| S5-01 | 독립 검사 서비스 | Studio 종료 후 Windows service/Linux daemon에서 검사한다. 서비스 시작·정지·재부팅·해당 사용자권한·GPU warmup·readiness를 구분한다. / 같은 input id의 중복 요청 결과 게시를 제어하고 전원 중단 후 inbox/results/outbox를 복구한다. 시간 초과·미실행은 OK가 아니다. / Studio non-admin 설치와 SCM 서비스 등록을 분리한다. SCM은 명시적 elevation·전용 service account·ProgramData ACL·project/network credentials를 검사한다. 사용자 로그인이 없는 reboot/Session0에서 GPU/camera/network 접근을 실제 검증한다. 등록 거절 시 개인 Studio는 유지한다. |
| S5-08 | data drift와 개선 반복 | 제품/Lot별 입력 분포·REVIEW/NG 비율·human feedback 변화와 기준 모델 버전을 표시한다. / 정답 없는 NG비율 변화를 실제 품질 하락으로 단정하지 않는다. intake→검수→retrain→동일cohort 비교→사람승인→배포로 연결한다. / 정책 기반 자동 재학습·재배포는 opt-in이다. 기본은 candidate 생성 후 사람 승인이다. 자동 promotion은 사전 승인된 policy revision·fresh truth/heldout 기준·패키지/장치/현재 적격성·audit와 실패 rollback을 모두 만족해야 하며 검증 전 지원 완료로 표시하지 않는다. |
| S5-10 | 서비스 보안과 운영 설정 | HTTPS·worker/fleet 인증·세션 만료·project isolation·업로드/archive 경로 traversal·checkpoint trust·IPC allowlist·external URL allowlist를 검증한다. / 비밀정보는 OS credential store/서버 secret 설정으로 관리한다. 승인/적용/권한변경 감사기록과 정책 version을 보존하며 developer-mode 사용을 명확히 표시한다. |
| S6-02 | Windows CPU 설치 프로그램 | Python/Node 미설치 Windows 11 x64에서 non-admin 사용자 설치·첫실행·실제 CPU 추론·재실행·제거를 검증한다. / 한글/공백 경로와 locked file를 처리하고 사용자 프로젝트는 제거 시 보존한다. Windows10 지원은 EOL/보안정책 및 별도 검증 후 선택 지원으로 표시한다. / non-admin Studio와 elevated SCM registration은 서로 다른 설치 옵션이다. 서비스등록 refusal·rollback·uninstall은 소유한 서비스만 처리하며 개인 데이터와 Studio를 보존한다. |
| S6-03 | 선택형 GPU와 runtime pack | CPU 기본 설치와 NVIDIA/optionalOCR/DICOM/OpenVINO pack의 필요용량·호환driver·출처·checksum을 구분한다. / Linux worker와 Windows localCUDA 조합을 각각 preflight·native 실행으로 검증한다. unavailable optional runtime은 기능을 조용히 가짜 실행하지 않는다. / package library에서 선택 package의 ONNX/OpenVINO·정량화/embedded optimization을 job으로 실행하고 actual supported hardware·measured precision/cohort drift·변환/추론 receipt를 기록한다. CPU/GPU/iGPU/NPU/Jetson/MIG 조합은 실제 증거별 지원표이며 즉시 모든 장치를 지원한다고 선언하지 않는다. |
| S6-04 | 오프라인 설치와 업데이트 | 인터넷 차단 환경에서 checksum/서명 확인→설치→known-image 실행을 검증한다. app/API/worker/runtime/schema compatibility matrix를 적용한다. / 다운로드/설치/DBmigration 각 지점 중단 후 이전 설치와 데이터로 복구한다. 실행 중 학습·검사와 충돌하는 업데이트는 안전한 실행 창을 안내한다. / update의 pinned trust authority/publisher/key·manifest signature·pack inventory·key rotation/revocation·offline trust provision·downgrade policy를 고정한다. 변조 manifest/다른 publisher/누락추가 pack/폐기 key/의도하지 않은 구버전을 거절한다. 승인된 복구 rollback은 별도 기록한다. cutover 후 새 쓰기가 있으면 검증된 inverse 또는 forward recovery만 수행한다. |
| S6-05 | 공개 CI와 source 재현성 | PR에서 PythonCPU/renderer/type/build/contract 회귀를 실행하고 Windows native smoke를 별도로 운영한다. source/lock/runtime inventory를 release artifact에 묶는다. / 외부 PR에는 secret/GPU 접속을 제공하지 않는다. remoteGPU는 승인된 내부 runner, signing은 protected release workflow에서만 수행한다. bit-for-bit 재현은 측정 전 주장하지 않는다. / Windows native와 Linux CPU/native worker CI를 명시적으로 포함한다. Electron/Python/Node/dependency 지원 및 보안 버전 검토와 lock upgrade를 초반 작업에 포함하고 특정 최신 버전은 official release 확인 후 결정한다. |
| S6-06 | 서명과 릴리스 채널 | Windows Authenticode·checksum·release notes·지원표·source link를 동일 artifact에 연결한다. stable/beta와 unsigned development artifact를 구분한다. / 서명 후 byte 변화는 기존 receipt를 무효로 하며 실제 installer/실행 inventory를 다시 검증한다. signing identity가 없으면 unsigned 상태를 공개하고 signed gate는 pending이다. |
| S7-01 | 기존 기능 전체 coverage 계약 | F001–F123/U001–U033의 모든 ID를 새 계획 owner와 연결한다. 기존 integration 표시를 실사용 accepted로 승격하지 않는다. / 버튼/메뉴/shortcut/action 목록에 성공·empty·invalid·error·cancel·reopen·handoff 근거를 기록한다. 미실행 및 skip 이유는 pending/not_required 근거로 표시한다. |
| S7-02 | 10모델군 실제 작업 시나리오 | 각 모델의 적합한 사람이 검토한 label fixture와 실제 input으로 준비→학습→평가→flow/채택→package를 수행한다. 제공 데이터는 원본 보존 복사본/manifest로 사용한다. / 모든 task를 똑같은 NG 폴더로 억지 검증하지 않는다. 정상 truth/OCRtext/OBBangle/enhancementpair/GANreview 부족은 explicit prerequisite로 남긴다. / 한 chain에서 서로 다른 다섯 checkpoint와 반복/shared model을 연결한 검사도 준비된 checkpoint가 있을 때 실제 실행한다. ROI→SEG+Patch→aggregate·회전→OCR 등의 대표 multi-model recipe를 package까지 검증한다. |
| S7-03 | Windows 실제 설치와 사용 QA | 실제 Windows11x64 VM/장비에서 NSIS 설치·CPUknown-image·전체UI버튼·DPI·한글경로·업데이트·제거·재설치를 검증한다. / NVIDIA가 있는 target의 localGPU와 Windows→Linuxremote 실행은 별도 receipt로 남긴다. macOS 결과로 Windows gate를 대신하지 않는다. / 공개 후보 이전에 처음 사용하는 사람이 설명 없이 demo→자기 데이터→학습→평가→flow→검사/오류검수를 수행하는 usability pilot을 기록한다. blocker와 잘못된 OK/버전 안내는 공개 전에 수정하고 같은 경로를 재검증한다. |
| S7-04 | 팀 동시 작업과 fault injection | 두 사용자 project/label 충돌·role 철회·samejob submit·API/worker crash·네트워크중단·PID재사용·diskfull·OOM·취소 race를 검증한다. / 승인→export→중앙apply/rollback 사이 정답·권한·버전 변경과 partial transfer/update를 검증한다. 타 작업과 예약은 보존한다. |
| S7-05 | 데이터 규모와 연속 운전 | 10k/100k 이미지 metadata 검색 fixture와 큰 원본 image를 별도로 검증한다. 목표 장비의 RAM/disk/p95목록반응/queue 용량을 실제 측정한다. / 72시간 연속 검사와 backlog·중복전송·reconnect·diskquota를 검증한다. 이 값은 계획 목표이며 실제지원한계/택트성능은 측정 후 공개한다. |
| S7-06 | 공정 품질 승인과 장비 검증 | 정상/불량·제품/Lot별 검토 cohort와 사용자가 정한 미검/과검 정책으로 품질을 별도 승인한다. 공정마다 다른 기준을 숨기지 않는다. / 카메라/PLC/MES 실제장비 없는 경우 simulator contract까지만 완료이다. 산업별 성능 승인과 기능 출시 상태를 각각 공개한다. |
| S7-07 | 공개 후보와 릴리스 판정 | critical defects=0, 공개된 supported 조합은 실제 target기능 증거를 가지며 stale 승인·판정 불일치·데이터유실·무단접근 회귀를 통과한다. / 라이선스·Windows native install·upgrade/restore·문서·지원표·source/SBOM·서명 상태와 known issues를 공개한다. 외부 조건 pending을 완료로 바꾸지 않는다. |
| S7-08 | 파일럿 feedback과 지속 유지 | 설명 없이 사용자가 데이터→학습→평가→flow→검사/오류검수를 수행하는 파일럿을 기록하고 막힌 동선과 운영 장애를 다음 release에 반영한다. / 지원/보안/회귀 triage·patch release·schema/protocol deprecation·backup/restore 연습 주기를 운영한다. 서비스 SLA는 실제 측정과 운영 책임이 정해진 뒤 약속한다. / 첫 usability pilot은 public release 전에 수행하며 결과와 critical blocker 해결을 S7-07의 입력으로 제공한다. 이후 유지보수 feedback은 반복 release 운영으로 이어간다. |

## 소프트웨어 구현 확인 62개

| ID | 작업 |
|---|---|
| S0-01 | 공간 증강의 이미지와 라벨 동시 변환 |
| S0-02 | 이상 점수와 임계값의 단위 통일 |
| S0-03 | 변경된 정답과 오래된 승인 차단 |
| S0-04 | 중앙 배포와 rollback의 공통 적격성 검사 |
| S0-05 | 플로우 실행과 화면 상태 결함 수정 |
| S0-06 | 복구와 보안 후보의 재현 및 판정 |
| S0-07 | 기존 모델과 승인 결과의 영향 조사 |
| S0-08 | 핵심 결함 회귀와 기준 기록 |
| S1-01 | 프로젝트와 실행 문맥 공통 계약 |
| S1-02 | 영속 작업 원장과 멱등 제출 |
| S1-03 | 스케줄러와 작업 소유권 |
| S1-04 | 취소와 장애 상태 통합 |
| S1-05 | 범용 worker 등록과 지원표 |
| S1-06 | 저장소와 artifact 게시 |
| S1-07 | 계정과 프로젝트 권한 |
| S1-10 | 이벤트와 클라이언트 host adapter |
| S2-01 | 첫 실행과 GPU 없는 예제 |
| S2-02 | 공통 디자인 시스템 |
| S2-03 | 6단계 진행과 준비도 안내 |
| S2-04 | 5단계 편집 화면 재구성 |
| S2-05 | typed DAG와 검사 규칙 편집 |
| S2-06 | 공통 원본과 판정 viewer |
| S2-07 | 대용량 이미지 선택과 검색 |
| S2-08 | 디버거와 A/B 및 버전 이력 |
| S2-09 | 학습과 작업 센터 공통 화면 |
| S2-10 | 운영자 화면과 개인 설정 |
| S3-01 | 데이터 index와 import |
| S3-02 | dataset snapshot과 출처 |
| S3-03 | 태그와 명시적 정답 |
| S3-04 | 팀 라벨 편집과 충돌 처리 |
| S3-05 | 라벨 도구 기능과 format 왕복 |
| S3-06 | 모델과 프롬프트 라벨 제안 |
| S3-07 | 제품과 Lot 분할 및 준비도 |
| S3-08 | 비파괴 편집과 재학습 영향 |
| S3-09 | 검사 수집과 active learning |
| S3-10 | 프로젝트 수명과 데이터 이동 |
| S4-01 | DINOv3 Classification recipe |
| S4-02 | DINOv3 Segmentation recipe |
| S4-03 | DINOv3 Patch Classification recipe |
| S4-04 | YOLO 일반 검출 recipe |
| S4-05 | 이상탐지 두 목적과 calibration |
| S4-06 | OCR 검출과 인식 recipe |
| S4-07 | 회전 검출 production adapter |
| S4-08 | 회전과 정렬 recipe |
| S4-09 | 이미지 개선 실제 pair recipe |
| S4-10 | GAN 생성과 사람 검토 |
| S4-11 | 모든 작업의 실행 위치 통합 |
| S4-12 | AutoDL과 checkpoint 재개 |
| S5-02 | 폴더와 HTTP 입력 |
| S5-03 | 카메라와 video adapter |
| S5-04 | PLC와 MES 범용 연동 |
| S5-05 | 제품 recipe와 traceability |
| S5-06 | 장비 fleet와 단계 배포 |
| S5-07 | 관측과 운영 알림 |
| S5-09 | 백업과 복구 및 보존기한 |
| S6-01 | 오픈소스와 모델 라이선스 정책 |
| S6-07 | 외부 기여와 프로젝트 운영 |
| S6-08 | SDK와 자동화 API |
| S6-09 | 공개 위생과 지원 자료 |
| S6-10 | 브라우저 및 확장 개발 경계 |
| S0-09 | 초기 UI와 Electron 검증 환경 |
| S6-11 | 공개 배포 라이선스 결정 |

## 이번 실행의 확인 결과

- 실제 원격 CUDA: 중단·재개와 연속 학습의 가중치/optimizer/scheduler/AMP/RNG/epoch/step이 모두 일치했다. 원본과 타 GPU 예약은 보존했다.
- 실제 앱: AutoDL 측정·재사용·재학습·중단, 원격 재개 선택, 실제 SAM2/텍스트/이미지 예시·거절·재열기·배치 취소·few-label 학습/refine을 확인했다.
- 원격 재개 회귀 140개, 화면 회귀 827개와 타입 검사가 통과했다. 공개 CI와 현재 코드의 frozen known-image 검증은 별도로 진행 중이다.
- 72시간 고정 소스 연속 운전은 실행 중이며 완료로 세지 않는다.
- 인간 품질 승인·실장비·운영 계정·서명·지원 책임은 소프트웨어 회귀 통과와 별개다. Windows 실제 QA 면제를 해당 플랫폼 실행 성공으로 표시하지 않는다.

출처는 registry와 source freeze·명령·JUnit·GUI trace·SHA 근거다. 이전 검증을 모두 최신 코드로 재실행한 것으로 주장하지 않는다.
