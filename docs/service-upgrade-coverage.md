# 기존 기능과 서비스 고도화 대응표

기존 기능을 새 구조로 옮길 때 빠지는 항목을 검토하는 표다. 기존 registry 상태는 구현 주장 및 당시 근거이며, 이번 Windows 서비스 acceptance가 완료되었다는 의미는 아니다. 156개 ID 전부에 구현 담당 task와 별도 acceptance가 있다.

## 범위와 상태

- F001–F123: 123개, U001–U033: 33개, 합계 156개. 누락 0개.
- 신규 계획: 8개 phase/82개 작업 묶음. in_progress 25개, planned 39개, verification_pending 18개. accepted 0개.
- 부모 구현 근거 소스 `e376db11`: verified 24개/pending 58개. 최신 추가 코드 `5b2955749dd9a5dc6a621dd24a2b28441050f9ed`에서 E03/E04/E05/E07을 반영해 별도 게시 진척은 확장 구현 slice 16개와 데이터·라벨링 개선 영역 2개다. 이 slice 집계는 부모 작업과 겹치므로 더하지 않는다.
- 최신 구현 slice와 게시 근거는 [실행 기록](implementation-ledger/SERVICE-UPGRADE.md#gold-label-review-20261004)에 기록한다.
- source registry: `docs/feature-program.json`, `docs/product-upgrade-program.json`.
- source baseline: `03e8f6d142e6f8e7fa6bd80cb82a4a9ccf8d65e6`.
- source registry의 acceptance와 최신 실행 ledger가 충돌하면 개별 최신 evidence를 다시 확인한다.
- 아래 mapping은 기존 제목·기능·제약을 보존하라는 실행 계약이다. task의 공통 acceptance만 확인하고 기존 제목의 동작을 생략하면 완료가 아니다.
- 최신 SDK 근거: F092–F096의 구현·실패·hosted native Windows 15개 근거 칸을 exact8917d8a 실행 결과에 연결했다. 다른 근거 칸과 accepted 0개는 유지한다. [Linux SDK](verification/receipts/sdk-linux-8917.json), [Windows SDK](verification/receipts/sdk-windows-8917.json), [실행 기록](verification/2026-10-04-remaining-integration-evidence.md).
- 각 legacy 항목은 GUI/persist/reopen/failure/handoff 및 target 조합의 evidence를 새 ledger에 연결한다.

## 모든 기존 항목

| 기존 ID | 기능 | 담당 task | 새 검증 |
| --- | --- | --- | --- |
| F001 | 자연어 조건으로 대상 영역 찾기 | S3-06, S3-05, S7-01 | pending |
| F002 | 이미지 예시로 대상 찾기 | S3-06, S3-05, S7-01 | pending |
| F003 | 이미지 프롬프트의 제외 영역 지정 | S3-06, S3-05, S7-01 | pending |
| F004 | 이미지와 텍스트 조건을 결합한 프롬프트 | S3-06, S3-05, S7-01 | pending |
| F005 | 긴 텍스트 프롬프트 입력 | S3-06, S3-05, S7-01 | pending |
| F006 | 프롬프트에서 다각형 라벨 생성 | S3-06, S3-05, S7-01 | pending |
| F007 | 클릭으로 객체 영역 선택 | S3-06, S3-05, S7-01 | pending |
| F008 | 드래그한 박스 안에서 객체 선택 | S3-06, S3-05, S7-01 | pending |
| F009 | 박스 라벨을 객체 다각형으로 변환 | S3-06, S3-05, S7-01 | pending |
| F010 | 소량 수동 라벨로 자동 추천기를 학습·갱신 | S3-06, S3-05, S7-01 | pending |
| F011 | 완료 모델로 여러 이미지의 라벨 제안 | S3-06, S3-05, S7-01 | pending |
| F012 | 키워드로 여러 이미지 일괄 라벨링 | S3-06, S3-05, S7-01 | pending |
| F013 | 라벨 후보를 객체 크기로 필터링 | S3-06, S3-05, S7-01 | pending |
| F014 | AI 라벨링 CPU·GPU 선택 | S3-06, S3-05, S7-01 | pending |
| F015 | 여러 이름 있는 라벨 세트 | S3-03, S3-04 | pending |
| F016 | 이미지별 색상 태그 | S3-03, S3-04 | pending |
| F017 | 라벨 세트에 사용자 플래그 | S3-03, S3-04 | pending |
| F018 | 모델에 Best·Important 플래그 | S4-13 | pending |
| F019 | 라벨링 완료·미완료 수와 비율 | S3-03, S3-07, S2-07 | pending |
| F020 | 상태 통계를 눌러 이미지 필터링 | S3-03, S3-07, S2-07 | pending |
| F021 | 클래스별 데이터 분포 | S3-03, S3-07, S2-07 | pending |
| F022 | Train·Test·미사용·미분할 분포 | S3-07 | pending |
| F023 | 모델 결과를 라벨 세트별로 선택 | S3-05, S4-13 | pending |
| F024 | 여러 작업자의 데이터 정리 | S3-04, S1-07 | pending |
| F025 | 이미지 Classification | S4-01, S7-02 | pending |
| F026 | 고해상도 Patch Classification | S4-03, S7-02 | pending |
| F027 | Segmentation의 여러 결함 클래스 | S4-02, S7-02 | pending |
| F028 | 일반 Object Detection | S4-04, S7-02 | pending |
| F029 | Oriented Object Detection | S4-07, S7-02 | pending |
| F030 | OCR 문자 인식 | S4-06, S7-02 | pending |
| F031 | 학습형 정방향 Rotation 모델 | S4-08, S7-02 | pending |
| F032 | 정상 데이터 기반 Anomaly Classification | S4-05, S7-02 | pending |
| F033 | Anomaly Segmentation 영역 검사 | S4-05, S7-02 | pending |
| F034 | GAN Defect Generator | S4-10, S7-02 | pending |
| F035 | 학습형 Image Enhancement | S4-09, S7-02 | pending |
| F036 | 빠른 초기 모델 학습 | S4-12, S4-11, S2-09 | pending |
| F037 | 모델 구조 자동 탐색 | S4-12, S4-11, S2-09 | pending |
| F038 | 초매개변수 자동 탐색 | S4-12, S4-11, S2-09 | pending |
| F039 | 사전학습 모델을 활용한 Transfer Learning | S4-12, S4-11, S2-09 | pending |
| F040 | 기존 모델에서 추가 데이터 재학습 | S4-12, S4-11, S2-09 | pending |
| F041 | 최적화된 설정을 재사용하는 Fast Retraining | S4-12, S4-11, S2-09 | pending |
| F042 | 추론 속도 목표를 반영한 학습 최적화 | S4-12, S4-11, S2-09 | pending |
| F043 | Embedded 장치에 맞춘 모델 최적화 | S6-03, S6-08, S7-03 | pending |
| F044 | 양자화 모델 생성 | S6-03, S6-08, S7-03 | pending |
| F045 | GUI 없이 폴더 입력으로 학습 REST API | S6-08, S4-12 | pending |
| F046 | 하나의 CLI로 quick·AutoDL 학습 | S6-08, S4-12 | pending |
| F047 | 이미지와 분석 결과를 같은 화면에서 확인 | S4-13, S2-06 | pending |
| F048 | 정확도·정밀도·재현율·F1 | S4-13, S2-06 | pending |
| F049 | 클래스별 지표 표시 | S4-13, S2-06 | pending |
| F050 | Confusion Matrix 셀로 이미지 필터 | S4-13, S2-06 | pending |
| F051 | 분할 클래스별 미검·과검 이미지 찾기 | S4-13, S2-06, S4-02 | pending |
| F052 | 검출 클래스별 누락·추가 박스 이미지 찾기 | S4-13, S2-06, S4-04 | pending |
| F053 | OCR 문자별 누락·추가 결과 찾기 | S4-13, S2-06, S4-06 | pending |
| F054 | 결과 확률 분포 분석 | S4-13, S2-06 | pending |
| F055 | 결함 크기 분포 분석 | S4-13, S2-06, S4-02 | pending |
| F056 | ROC 곡선·임계값 검토 | S4-13, S2-06 | pending |
| F057 | 같은 종류 모델 두 개 비교 | S4-13, S2-06 | pending |
| F058 | 서로 다른 종류 모델 비교 | S4-13, S2-06 | pending |
| F059 | 재평가 이력 보관 | S4-13, S2-06 | pending |
| F060 | 재평가 결과를 부모 모델 아래 묶어 표시 | S4-13, S2-06 | pending |
| F061 | 모델 목록의 임계값 설정 정보 | S4-13, S2-06 | pending |
| F062 | 회전 검출 mAP·IoU 평가 | S4-13, S2-06, S4-07 | pending |
| F063 | 회전 박스 IoU·각도 오류 분포 | S4-13, S2-06, S4-07 | pending |
| F064 | 여러 모델을 잇는 플로우 편집 | S2-05, S2-04, S2-08 | pending |
| F065 | 모델 뒤에 모델을 잇는 다단계 검사 | S2-05, S2-04, S2-08 | pending |
| F066 | 클래스에 따라 후속 검사 분기 | S2-05, S2-04, S2-08 | pending |
| F067 | 한 모델을 여러 클래스에서 공유 | S2-05, S2-04, S2-08 | pending |
| F068 | 여러 모델 결과를 합쳐 최종 판정 | S2-05, S2-04, S2-08 | pending |
| F069 | 고정 ROI 검사 | S2-05, S2-04, S2-08 | pending |
| F070 | 검출 객체의 ROI로 후속 검사 | S2-05, S2-04, S2-08 | pending |
| F071 | 회전 객체의 Fitted ROI와 정방향 후속 검사 | S2-05, S2-04, S2-08 | pending |
| F072 | ROI를 원해상도 패치로 분할 | S2-05, S2-04, S2-08 | pending |
| F073 | Blob 크기·개수와 누락 판정 | S2-05, S2-04, S2-08 | pending |
| F074 | 분할 영역의 평균 밝기로 임계값 판정 | S2-05, S2-04, S2-08 | pending |
| F075 | 곡선·자유 경로 길이 측정 | S2-05, S2-04, S2-08 | pending |
| F076 | 검출 영역 넓이 측정 | S2-05, S2-04, S2-08 | pending |
| F077 | GAN 원본 이미지의 생성 위치 지정 | S4-10, S3-09 | pending |
| F078 | GAN 여러 생성 영역 지정 | S4-10, S3-09 | pending |
| F079 | OCR 문자 규칙과 오인식 교정 | S4-06, S2-05 | pending |
| F080 | OBB 중심 대칭 방식 라벨링 | S3-05, S4-07 | pending |
| F081 | OBB 한쪽 면을 기준으로 라벨링 | S3-05, S4-07 | pending |
| F082 | OBB 비정형 객체용 라벨링 | S3-05, S4-07 | pending |
| F083 | 여러 GPU에서 서로 다른 학습 작업 동시 실행 | S1-03, S1-05, S4-11 | pending |
| F084 | 같은 GPU에 가벼운 모델 여러 개 동시 배치 | S1-03, S1-05, S4-11 | pending |
| F085 | 학습·AI 라벨링·추론 작업을 GPU별 배분 | S1-03, S1-05, S4-11 | pending |
| F086 | 하나의 큰 모델을 여러 GPU로 학습 | S1-03, S1-05, S4-11, S4-12 | pending |
| F087 | MIG 논리 GPU 할당 | S1-03, S1-05, S4-11 | pending |
| F088 | 학습 작업 등록·상태·대기열 API | S1-03, S1-05, S4-11 | pending |
| F089 | AI 라벨링·전문 모델의 서버 자원 사용 | S1-03, S1-05, S4-11 | pending |
| F090 | 전체 플로우를 하나의 호출로 실행 | S2-05, S4-14, S5-01 | pending |
| F091 | 여러 모델의 순차·병렬 추론 | S2-05, S4-14, S5-01 | pending |
| F092 | 추론 최대 시간 제한 | S5-02, S6-08 | pending |
| F093 | C++ 네이티브 Runtime 연동 | S6-08, S4-14 | pending |
| F094 | C# 네이티브 Runtime 연동 | S6-08, S4-14 | pending |
| F095 | Python Runtime 연동 | S6-08, S4-14 | pending |
| F096 | Predictor·Executor 호출 구조 | S6-08, S4-14 | pending |
| F097 | CPU 추론 | S1-05, S6-03, S7-03 | pending |
| F098 | CUDA GPU 추론 | S1-05, S6-03, S7-03 | pending |
| F099 | Intel OpenVINO CPU·GPU·iGPU·NPU | S1-05, S6-03, S7-03 | pending |
| F100 | Jetson 등 Embedded 보드 실행 | S1-05, S6-03, S7-03 | pending |
| F101 | 추가 변환 작업 없이 하드웨어에 적용 | S1-05, S6-03, S7-03 | pending |
| F102 | 학습 모델 파일 출력 | S4-13, S6-08 | pending |
| F103 | 학습 설정 JSON 출력·재사용 | S4-13, S6-08 | pending |
| F104 | 평가 지표 JSON 출력 | S4-13, S6-08 | pending |
| F105 | 이미지별 예측 JSON 출력 | S4-13, S6-08 | pending |
| F106 | 외부 UI에 학습 기능을 통합 | S6-08, S3-01, S4-12 | pending |
| F107 | 외부 JSON 라벨로 현장 재학습 | S6-08, S3-01, S4-12 | pending |
| F108 | 높은 신뢰도 예측을 자동 라벨로 사용 | S3-06, S3-09 | pending |
| F109 | 새 데이터에서 자동 재학습·재배포 | S5-08, S3-09, S4-14 | pending |
| F110 | 현장과 중앙 서버를 묶어 모델 관리 | S5-06, S4-14 | pending |
| F111 | 장비에서 학습과 추론 동시 운용 | S1-03, S5-01, S7-05 | pending |
| F112 | 데이터 증강 방법 자동 선택 | S4-12, S0-01 | pending |
| F113 | 권한을 가진 client-server 공동 프로젝트 | S1-07, S6-10 | pending |
| F114 | 원본·수정 데이터와 처리 이력 관리 | S3-02, S3-08 | pending |
| F115 | JPG·PNG·BMP·TIFF 이미지 입력 | S3-01, S3-05, S6-03 | pending |
| F116 | DCM·DICOM 이미지 입력 | S3-01, S3-05, S6-03 | pending |
| F117 | 외부 JSON 라벨 입력 | S3-01, S3-05, S6-03 | pending |
| F118 | 외부 mask 라벨 이미지 입력 | S3-01, S3-05, S6-03 | pending |
| F119 | 저장 플로우로 사전 단일·일괄 검사 | S2-08, S4-13, S5-02 | pending |
| F120 | 학습 CLI의 지정 출력 폴더 | S6-08, S2-09 | pending |
| F121 | 학습 CLI help·진행률·지표 출력 | S6-08, S2-09 | pending |
| F122 | Probability Threshold로 예측 필터 | S0-02, S2-05, S3-06 | pending |
| F123 | Size Threshold로 영역 필터 | S0-02, S2-05, S3-06 | pending |
| U001 | Action-specific error resolution with verified effects | S2-02, S1-04 | pending |
| U002 | Common GUI preparation and workflow for every supported model family | S2-09, S4-11, S7-02 | pending |
| U003 | Unified resumable task center and honest cancel/resource states | S1-02, S1-04, S2-09 | pending |
| U004 | Model and runtime readiness assistant | S1-05, S2-03 | pending |
| U005 | Persistent package library with optimization and deployment re-entry | S6-03, S4-14, S5-06 | pending |
| U006 | Node-linked input/output debugger | S2-06, S2-08 | pending |
| U007 | Image-based ROI editor and live crop preview | S2-05, S2-06 | pending |
| U008 | Fixed-set flow version comparison | S2-08, S4-13 | pending |
| U009 | Run to selected node and invalidate stale results | S0-05, S2-08 | pending |
| U010 | Reusable subgraph templates and explicit cross-project mapping | S2-08 | pending |
| U011 | Human-readable decision evidence | S2-06, S2-05 | pending |
| U012 | Flow workspace navigation and draft/saved/deployed states | S2-04, S2-08 | pending |
| U013 | Unified data readiness including blur/exposure/near duplicate diagnostics | S3-07, S3-01 | pending |
| U014 | Non-destructive image edit with annotation transforms and version history | S3-08, S0-01 | pending |
| U015 | Review prioritization for errors/disagreements/threshold cases | S3-09, S4-13 | pending |
| U016 | Resumable review queue and origin return | S3-09, S2-06 | pending |
| U017 | Auto-training budgets and progress | S4-12, S1-03 | pending |
| U018 | Korean condition labeling with image examples and configurable real VLM provider | S3-06 | pending |
| U019 | Explicit anomaly image-versus-region purpose and evaluation profile | S4-05 | pending |
| U020 | Independent object direction targets alongside axial OBB angle | S3-05, S4-07, S4-08 | pending |
| U021 | Generic server connection wizard with real input preflight | S1-05, S2-01 | pending |
| U022 | Device capability, live verification and approval state separation | S1-05, S4-14, S5-06 | pending |
| U023 | PLC/MES forms, advanced JSON and local receiver tests | S5-04 | pending |
| U024 | Dedicated operator inspection workspace | S2-10, S5-05 | pending |
| U025 | Install/update/compatibility checks and redacted diagnostics | S6-04, S6-09, S5-07 | pending |
| U026 | Common buttons/forms/status/loading/errors | S2-02 | pending |
| U027 | Names and next actions in basic view, technical details collapsed | S2-03, S2-09 | pending |
| U028 | Collapsible guidance, larger imagery and legible training visualization | S2-02, S2-04 | pending |
| U029 | Text-and-color states and keyboard focus/navigation | S2-02, S2-10 | pending |
| U030 | GUI, persistence, reopen, error/cancel and downstream handoff acceptance | S7-01, S7-03, S7-04 | pending |
| U031 | SDK dependency and installation readiness for Python/C++/C# | S6-08 | pending |
| U032 | Clear replica-DDP, frozen-encoder and finite search capability boundaries | S4-12, S4-01, S1-05 | pending |
| U033 | Explicit hardware verification matrix for GPU/MIG/NPU/Edge | S1-05, S6-03, S7-03 | pending |

## 기존 비등록 범위와 추가 서비스 검증

이 목록은 registry 밖의 승인된 범위 16개와 서비스 qualification 요구 29개를 추가로 연결한다. 번호는 검토용 식별자이며 82개 작업에 포함된 요구다. 별도 신규 기능 수로 중복 집계하지 않는다.

| 감사 ID | 요구 | 담당 task | 상태 |
| --- | --- | --- | --- |
| baseline-B001 | Stable project/image/label/split/model/flow/result identity and explicit class truth | S1-01, S3-02, S3-03, S0-03 | pending |
| baseline-B002 | Project backup/restore and source isolation | S3-10, S5-09, S1-08 | pending |
| baseline-B003 | Flexible five-stage inspection composition and chains | S2-05, S2-08, S7-02 | pending |
| baseline-B004 | Trace/Undo/Redo/draft/version/open-versus-activate | S0-05, S2-08 | pending |
| baseline-B005 | Whole-flow evaluation, cohort export parity and target execution | S4-13, S4-14, S5-06 | pending |
| baseline-B006 | Annotation format interoperability and grouped split leakage prevention | S3-05, S3-07 | pending |
| baseline-B007 | Team label guidance, assignments, leases and two-person review | S3-04, S1-07 | pending |
| baseline-B008 | Prompt/model/batch auto-label candidates with explicit adoption | S3-06 | pending |
| baseline-B009 | Model lifecycle A/B, reevaluation, threshold approval and rollback | S4-13, S4-14, S0-04 | pending |
| baseline-B010 | Ten-family complete native path and truthful capability inventory | S4-01, S4-02, S4-03, S4-04, S4-05, S4-06, S4-07, S4-08, S4-09, S4-10, S7-02 | pending |
| baseline-B011 | Search, transfer, fast retraining and optimization boundaries | S4-12, S6-03 | pending |
| baseline-B012 | Independent watch-folder/HTTP/camera/PLC/MES/fleet service | S5-01, S5-02, S5-03, S5-04, S5-06 | pending |
| baseline-B013 | Generic remote/server GPU allocation and owned job recovery | S1-03, S1-04, S1-05, S4-11 | pending |
| baseline-B014 | Independent CLI/REST and Python/C++/C# interoperability | S6-08 | pending |
| baseline-B015 | Continuous intake/review/retrain while incumbent stays available | S3-09, S5-08, S4-14 | pending |
| baseline-B016 | Non-destructive image edit, orientation and calibrated measurements | S3-08, S2-05 | pending |
| qualification-S001 | Windows SCM inspection service independent of login and desktop | S5-01, S7-03 | pending |
| qualification-S002 | Self-contained offline Windows installer and model prerequisite inventory | S6-02, S6-03, S6-04 | pending |
| qualification-S003 | Windows file/path/process semantics | S1-09, S6-02 | pending |
| qualification-S004 | Native Windows CPU/CUDA and device-qualified release matrix | S1-05, S6-03, S7-03 | pending |
| qualification-S005 | Transactional Windows install/upgrade/uninstall and service ownership | S6-02, S6-04 | pending |
| qualification-S006 | Compiled SDK distribution for clean Windows targets | S6-08 | pending |
| qualification-S007 | Required CI with native multi-OS build and packaged smoke gates | S6-05 | pending |
| qualification-S008 | OSS reproducible build and dependency/weight provisioning | S6-05, S6-07, S6-03 | pending |
| qualification-S009 | Publisher signing and release provenance | S6-06 | pending |
| qualification-S010 | Configured trusted update channel and recovery qualification | S6-04, S6-06 | pending |
| qualification-S011 | Component, dependency and model-weight license inventory plus SBOM | S6-01, S6-05 | pending |
| qualification-S012 | Privacy and external-provider data boundaries | S3-06, S5-10, S6-09 | pending |
| qualification-S013 | OSS maintenance/security/support delivery | S6-07, S6-09, S7-08 | pending |
| qualification-S014 | Shared-server auth, TLS and service-secret storage qualification | S1-07, S5-10 | pending |
| qualification-S015 | Durable audit and configurable retention | S5-07, S5-09, S5-10 | pending |
| qualification-S016 | Complete backup and clean-machine restore with recovery bounds | S5-09, S1-08, S3-10 | pending |
| qualification-S017 | Fault-injected service intake/result delivery/recovery | S5-02, S7-04 | pending |
| qualification-S018 | Operational metrics, readiness and diagnostics | S5-07, S7-05 | pending |
| qualification-S019 | Published scale, performance and soak qualification | S7-05 | pending |
| qualification-S020 | GPU sharing/MIG/DDP pressure and fair scheduling qualification | S1-03, S1-05, S4-12, S7-04 | pending |
| qualification-S021 | Bounded input queues, quotas and overload handling | S5-02, S1-03 | pending |
| qualification-S022 | Physical camera/RTSP/device intake qualification | S5-03, S7-06 | pending |
| qualification-S023 | Physical PLC/MES protocol/mapping/interlock qualification | S5-04, S7-06 | pending |
| qualification-S024 | Representative model-quality and threshold approval per task/product | S7-06, S4-13, S4-14 | pending |
| qualification-S025 | Full ten-family native/Windows/independent acceptance matrix | S7-02, S7-03 | pending |
| qualification-S026 | Complete legacy-scope coverage gate | S7-01 | pending |
| qualification-S027 | Windows usability/accessibility and context safety qualification | S2-02, S0-05, S7-03 | pending |
| qualification-S028 | Generic server connectivity and remote runtime provisioning qualification | S1-05, S4-11, S6-03 | pending |
| qualification-S029 | Multi-user transaction and datastore stress qualification | S1-08, S7-04, S7-05 | pending |

## 작업별 명칭

| task | 산출물 |
| --- | --- |
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
| S1-08 | 기존 JSON과 DB migration |
| S1-09 | Windows 프로세스와 실행 entrypoint |
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
| S4-13 | 동일 cohort 모델과 전체 flow 평가 |
| S4-14 | 모델 및 전체 flow 승인 |
| S5-01 | 독립 검사 서비스 |
| S5-02 | 폴더와 HTTP 입력 |
| S5-03 | 카메라와 video adapter |
| S5-04 | PLC와 MES 범용 연동 |
| S5-05 | 제품 recipe와 traceability |
| S5-06 | 장비 fleet와 단계 배포 |
| S5-07 | 관측과 운영 알림 |
| S5-08 | data drift와 개선 반복 |
| S5-09 | 백업과 복구 및 보존기한 |
| S5-10 | 서비스 보안과 운영 설정 |
| S6-01 | 오픈소스와 모델 라이선스 정책 |
| S6-02 | Windows CPU 설치 프로그램 |
| S6-03 | 선택형 GPU와 runtime pack |
| S6-04 | 오프라인 설치와 업데이트 |
| S6-05 | 공개 CI와 source 재현성 |
| S6-06 | 서명과 릴리스 채널 |
| S6-07 | 외부 기여와 프로젝트 운영 |
| S6-08 | SDK와 자동화 API |
| S6-09 | 공개 위생과 지원 자료 |
| S6-10 | 브라우저 및 확장 개발 경계 |
| S7-01 | 기존 기능 전체 coverage 계약 |
| S7-02 | 10모델군 실제 작업 시나리오 |
| S7-03 | Windows 실제 설치와 사용 QA |
| S7-04 | 팀 동시 작업과 fault injection |
| S7-05 | 데이터 규모와 연속 운전 |
| S7-06 | 공정 품질 승인과 장비 검증 |
| S7-07 | 공개 후보와 릴리스 판정 |
| S7-08 | 파일럿 feedback과 지속 유지 |
| S0-09 | 초기 UI와 Electron 검증 환경 |
| S6-11 | 공개 배포 라이선스 결정 |

## 완료 evidence 형식

`legacy_id, task_id, source_sha, artifact/run/target IDs, input/label/truth/graph/model/package hashes, action, expected, observed, screenshot/log/receipt, acceptance_state, prerequisites, reviewer`를 기록한다. 버튼을 클릭했다는 증거와 모델/작업 완료·품질/운영 승인은 각각 별도 근거다.

기존 항목별 근거는 `docs/service-upgrade-evidence.json`의 RequirementEvidence 기록(구현·GUI·저장·다시 열기·실패·인계·대상 실행, native Windows, 선행 조건)에 남긴다. 각 항목은 pending, verified(근거 포함) 또는 not_required(사유 포함)이며, 기존 registry의 주장은 근거가 아니다. 근거 하나에는 담당 task, 40자 커밋, 종류(click·api·unit·real_input·native_windows·target), action·expected·observed·reviewer와 저장소 시험 경로 또는 해시가 붙은 receipt를 적는다. GUI는 실제 클릭(scripts/e2e 시험 또는 해시가 붙은 receipt), native Windows는 Windows 실행(receipt 또는 Windows workflow가 실행하는 시험), 대상 실행은 실제 장비 실행 receipt와 산출물 해시만 인정한다. not_required에는 사유와 검토자를 적는다. `scripts/check_service_plan.py`가 이 표의 새 검증 칸을 기록에서 다시 계산하고, 모든 항목이 근거를 갖추고 native Windows가 verified일 때만 accepted를 허용한다.

원본 데이터나 credentials를 공개 evidence에 넣지 않는다. private 원본 mapping은 보존하고 공개 fixtures 또는 redacted summary와 source hash로 연결한다.
