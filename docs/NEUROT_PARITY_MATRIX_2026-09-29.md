# Neuro-T 기능 비교 감사 · 2026-09-30 갱신

## 판정 기준과 범위

이 문서는 뉴로클의 **공식 공개 자료**에 적힌 기능과 이 저장소의 코드·테스트·실데이터 QA를 대조한다. `구현`은 해당 동작의 코드와 직접 검증 근거가 있다는 뜻이다. `부분`은 이름이나 일부 작업만 같고 입력·출력·자동화·운영 범위가 좁다는 뜻이다. `미구현`은 이번 저장소 조사에서 해당 제품 동작을 수행하는 경로를 찾지 못했다는 뜻이다. **기능 구현, 네이티브 앱 클릭 검증, 실데이터 실행, 모델 성능, 현장 적용 승인은 서로 다른 판단**이다.

비교 대상은 Neuro-T의 데이터 관리→레이블링→학습→평가→플로우차트/Inference Center와 관련 Neuro-R, Neuro-T Engine 기능이다. 뉴로클은 이를 서로 다른 제품으로 설명한다([Neuro-T 제품](https://www.neuro-cle.com/en/product/neuro-t), [Neuro-T Engine 제품](https://www.neuro-cle.com/en/product/neuro-t-engine), [현장 적용](https://www.neuro-cle.com/en/feature/deployment)). 따라서 다른 제품 기능을 Neuro-T 데스크톱의 확정 기능처럼 옮겨 적지 않는다. 사용자가 제공한 비공개 세미나 발표자료의 원본·이미지·문구는 이 저장소에 포함하지 않았다.

## 1~5 버전에서 공식 자료로 확인한 범위

| 시기/버전 | 공식 공개 자료에서 확인되는 내용 | 이번 감사의 해석 |
| --- | --- | --- |
| 최초 제품(2020; 공식 글에서 `1.0` 표기는 미확인) | GUI에서 이미지 분류와 간단한 표시 작업 후 자체 알고리즘으로 모델을 만드는 Neuro-T, 현장 실행용 Neuro-R을 발표했다. [뉴로클 최초 출시 글](https://www.neuro-cle.com/post/%EB%88%84%EA%B5%AC%EB%82%98-%EC%9E%90%EB%8F%99%EC%9C%BC%EB%A1%9C-%EB%94%A5%EB%9F%AC%EB%8B%9D-%EB%AA%A8%EB%8D%B8-%EB%A7%8C%EB%93%A0%EB%8B%A4-%EB%89%B4%EB%A1%9C%ED%81%B4-%EB%94%A5%EB%9F%AC%EB%8B%9D-%EB%B9%84%EC%A0%84-%EC%86%8C%ED%94%84%ED%8A%B8%EC%9B%A8%EC%96%B4-neuro-t-neuro-r-%EC%B6%9C%EC%8B%9C) | 노코드 데이터→학습의 출발점으로 확인. 초기 세부 모델·버튼 수는 확인하지 않았다. |
| 2.x(2021) | 뉴로클의 과거 공식 뉴스 목록에서 **2.3 출시**를 확인했다. 상세 페이지는 이번 조사에서 열리지 않아 항목별 차이를 공식 원문으로 재확인하지 못했다. [뉴로클 2021 뉴스 목록](https://www.neurocle-ai.com/blog/categories/2021) | Fast Retraining 등 현재 기능을 2.3의 확정 신규 기능으로 소급 표기하지 않는다. |
| 3.x(2022~2023) | 공식 [회사 연혁](https://www.neuro-cle.com/company/about)은 2022년 3.0 출시를 기록한다. 공식 [3.2 발표](https://www.neurocle-ai.com/en/post/neuro-t-neuro-r-3-2-released)는 2023년 검출↔분할 레이블 파일 활용, 이미지 그룹·필터, 복수 프로젝트의 학습 우선순위 관리 등을 설명한다. | **3.2의 추가**와 3.0 최초 제공을 구분한다. 3.0 항목별 공식 릴리스 노트는 이번 조사에서 확보하지 못했다. |
| 4.0(2023 출시를 뉴로클이 2024년에 회고) | GAN 가상 결함 생성, 정상 데이터 기반 Anomaly Classification/Segmentation, Auto-Labeling·Auto-Selector·Keyword Labeler, 다중 모델 Flowchart와 Inference Center를 설명한다. [4.0 소개](https://www.neuro-cle.com/en/resource/newsroom/gan-model-for-vision-inspection), [4.0 수상 발표](https://www.neuro-cle.com/en/resource/newsroom/neuro-t-innovators-awards-platinum-2024) | 기능 존재를 확인할 수 있으나, 각 기능의 정확한 최초 도입 버전까지 모두 확정할 수는 없다. |
| 4.5(2025-09-01) | Neuro-T 오토레이블링 CPU/GPU 선택, GAN 다중 생성 영역, 분할 결과의 평균 회색값 threshold. Neuro-R 추론 속도 개선과 추론 최대 시간 제한도 발표했다. [4.5 공식 발표](https://www.neuro-cle.com/resource/newsroom/neurocle-neuro-t-neuro-r-4-5-ai-vision-inspection-performance-update) | 학습기와 런타임의 기능을 구분한다. |
| 5.0(공식 신규 기능 화면·회사 공지) | [공식 신규 기능](https://www.neuro-cle.com/en/feature/new)은 Neuro-T Engine의 API/CLI 기반 현장 재학습과 단일/멀티 GPU 작업 배분을 `v5.0`으로 표시한다. 뉴로클 [공식 회사 공지](https://www.linkedin.com/company/neuro-cle/)는 Neuro-T·Neuro-R 5.0 발표 웨비나를 언급한다. | Engine의 5.0 항목을 **Neuro-T GUI 5.0 기능**으로 옮기지 않는다. 회사 공지만으로 GUI 기능별 제공 범위·배포 상태는 확정할 수 없어 별도 공식 릴리스 노트가 확보되면 보강한다. |

현재 공식 [Neuro-T 제품 화면](https://www.neuro-cle.com/en/product/neuro-t)은 **10가지 모델**, 공식 [모델 기능 화면](https://www.neuro-cle.com/en/feature/models)과 [Neuro-T Engine 화면](https://www.neuro-cle.com/en/product/neuro-t-engine)은 **11가지 모델**로 적는다. 페이지 간 기준/시점이 명시되지 않았으므로 어느 수를 특정 GUI 버전의 확정 스펙으로 간주하지 않는다.

## 전체 기능 비교

| 영역·뉴로클 기준 | 우리 상태 | 저장소 근거와 남은 차이 |
| --- | --- | --- |
| 프로젝트 생성·열기, 데이터 출처 저장·Studio 라벨/분할 격리 | 구현(프로젝트 API 테스트) | [프로젝트 API](../backend/api/routes_project.py), [프로젝트 UI](../src/renderer/components/wizard/ProjectWorkspaceDialog.tsx), [격리 테스트](../backend/tests/test_project_annotation_isolation.py). 동일 원본 폴더를 두 프로젝트에서 열어도 Studio 편집 라벨·저장 분할·데이터 버전·가져오기 클래스·학습 준비에 쓰는 라벨·데이터 fingerprint가 프로젝트별로 구분된다. 기존 전역 라벨/분할은 첫 연결 때 프로젝트로 복사하고 전역 원본은 보존한다. 원본 이미지 자체는 공용이며 다중 사용자 권한·동시 수정 잠금은 별도 기능이다. |
| 이미지 폴더/LabelMe 가져오기, 분할·분포 확인 | 구현 | [데이터 API](../backend/api/routes_dataset.py), [데이터 UI](../src/renderer/components/dataset/DatasetStudio.tsx), [실데이터 QA](QA_NEUROT_FUNCTIONAL_2026-09-29.md). 88개 원본/80개 대응 라벨 데이터로 가져오기와 56/16/8 분할을 확인했다. |
| 데이터 버전·검증·복원 | 구현(코드), 검증 확대 필요 | [버전 API](../backend/api/routes_dataset_versions.py), [버전 UI](../src/renderer/components/dataset/DatasetVersionPanel.tsx), [테스트](../backend/tests/test_dataset_versions.py). 최근 추가된 기능으로, 여러 실제 프로젝트의 복원 후 단계별 회귀는 별도 QA가 필요하다. |
| 공동 작업·색 태그·플래그 | 부분 | 프로젝트 저장과 주석 저장은 있지만 뉴로클의 동시 작업, 이미지 색 태그 및 라벨/모델 플래그 중심 협업 체계([공식 레이블링](https://www.neuro-cle.com/en/feature/labeling))와 동등하지 않다. |
| 수동 bbox·회전 bbox·polygon·brush/eraser와 저장 | 구현 | [레이블링 UI](../src/renderer/components/labeling/Step2Labeling.tsx), [주석 API](../backend/api/routes_annotation.py), [실데이터 QA](QA_NEUROT_FUNCTIONAL_2026-09-29.md). 실제 라벨을 수정·저장·재열기 했다. |
| 검출↔분할 레이블 재사용과 데이터 그룹·필터 | 부분 | [주석 변환 API](../backend/api/routes_annotation.py)와 데이터 분할/필터가 있다. 공식 [3.2 발표](https://www.neurocle-ai.com/en/post/neuro-t-neuro-r-3-2-released)의 모델 유형 간 일괄 레이블 파일 교환, 자유로운 그룹 분류, 프로젝트 간 학습 우선순위 관리를 동일한 흐름으로 제공하지 않는다. |
| Auto-Selector, Shape Converter | 부분 | [주석 API](../backend/api/routes_annotation.py)의 `/auto-select`, `/shape-converter`와 UI가 있다. 일부 실제 이미지 클릭·Undo 검증은 있으나 뉴로클의 학습형 대량 추천·동일 품질을 입증하지 못한다([공식 레이블링](https://www.neuro-cle.com/en/feature/labeling)). |
| 완료 모델을 이용한 라벨 후보 생성·검토 | 구현(기능), 품질 미승인 | [제안 API](../backend/api/routes_label_suggestions.py), [단일 이미지 검토 UI](../src/renderer/components/labeling/ModelAssistPanel.tsx), [일괄 작업 UI](../src/renderer/components/labeling/BulkLabelAssist.tsx), [테스트](../backend/tests/test_label_suggestions.py). 같은 출처·작업 유형의 완료 모델로 후보를 만들고, 검토·채택/거절 이력을 저장한다. 채택 직전 버전을 보존하고 원본 LabelMe 파일을 수정하지 않는다. 실제 이미지 8장의 일괄 추론은 두 번 모두 8/8 완료·실패 0건이었다. 첫 실행은 후보 0건, 다음 실행은 한 이미지에서 polygon 후보 6개가 생성되어 검토 화면에서 확인했다. 여섯 후보는 채택하지 않았으며 품질을 승인하지 않았다. 별도 완료 모델의 실제 후보 1건은 검토 후 채택했다. |
| 소량 수동 라벨로 다수 이미지 Auto-Labeling | 부분 | 공식 [Auto-Labeling](https://www.neuro-cle.com/en/feature/labeling)은 소수 수동 라벨을 바탕으로 다수 이미지의 자동 추천을 설명한다. 현재는 **이미 학습 완료된 모델**로 미라벨 이미지 여러 장의 후보를 비동기 생성하고 검토 대기열에서 연다. 소량 라벨 입력만으로 모델을 자동 학습·갱신하는 흐름과 추천 품질 보장은 없다. |
| Keyword/Prompt Labeler | 미구현 | 텍스트 입력으로 영역을 찾아 bbox를 생성하는 공식 [Keyword Labeler](https://www.neuro-cle.com/en/feature/labeling) 경로가 없다. `Prompt Labeler`의 GUI 5.0 귀속은 이번 공식 공개 자료만으로 확정하지 않았다. |
| 분류·검출·분할·이상탐지 학습 | 구현 | [학습 엔진](../backend/engine/trainer.py), [학습 API](../backend/api/routes_training.py). 이 네 작업과 Fast/Precision 두 preset을 제공한다. |
| 공식 모델군 범위(OCR, 회전 객체 검출, Patch Classification, GAN, Enhancement, Alignment 등) | 부분(네 작업 범주) | 현재 네 작업 외 [공식 모델 목록](https://www.neuro-cle.com/en/feature/models)의 전용 학습·추론 경로는 없다. 회전 bbox **레이블** 지원은 회전 객체 검출 **모델** 지원과 다르다. 절차식 합성 데이터 생성기는 GAN 학습·Generation Center가 아니다. 모델 수 자체도 공식 페이지 간 차이가 있어 동일한 모델군을 제공한다는 뜻이 아니다. |
| 오토딥러닝 구조·초매개변수 자동 탐색 | 미구현 | [학습 엔진](../backend/engine/trainer.py)은 Fast/Precision 두 고정 preset으로 구조·값을 선택한다. 공식 [자동 구조·초매개변수 최적화](https://www.neuro-cle.com/en/feature/training)에 대응하는 탐색·후보 비교 실험은 없다. |
| 학습 취소·재접속·원격 서버 GPU 선택 | 구현(한 서버 경로 실증) | [학습 API](../backend/api/routes_training.py), [원격 컴퓨트 UI](../src/renderer/components/compute/ComputeServerPanel.tsx), [원격 작업](../backend/remote/coordinator.py). 등록한 외부 GPU 프로필에서 시작·완료 및 학습 중단 후 자원 반환을 검증했다([실데이터 QA](QA_NEUROT_FUNCTIONAL_2026-09-29.md)). 범용 서버 프로필은 코드에 있으나 모든 OS/GPU 조합 검증을 뜻하지 않는다. |
| 새 데이터 기반 Fast Retraining | 미구현 | 공식 [Fast Retraining](https://www.neuro-cle.com/en/feature/training)은 기존 성능을 유지하며 재학습하는 방식이다. 현재 새 학습 시작과 이전 모델 평가가 있어도 warm-start·성능 유지 조건·승인 흐름이 연결되지 않았다. |
| 모델 평가·이미지별 오검/미검·보고서 | 구현 | [평가 API](../backend/api/routes_evaluation.py), [평가 UI](../src/renderer/components/evaluation/EvaluationStudio.tsx), [보고서 API](../backend/api/routes_report.py). 8개 test 이미지의 평가 동작을 확인했지만 대표 OK 코호트가 없어 성능 수치의 운영 해석은 제한된다. |
| 후보 모델 vs 현행 모델 비교·승격·롤백 | 부분 | [동일 이미지 비교 UI](../src/renderer/components/evaluation/ModelComparisonPanel.tsx)와 [API](../backend/api/routes_model_comparisons.py)가 같은 test 이미지에 두 완료 모델을 실행해 판정 차이·모델/이미지 식별값을 저장한다. 실제 고해상도 이미지 1장에서 판정 불일치 1건을 저장·재열기 확인했다. 정상/불량이 함께 있는 충분한 holdout의 품질 판정, 승인된 active 모델 교체, 운영 rollback 거래는 없다. |
| Neuro-R 수준 현장 API·가속·실시간 택트 보장 | 미구현(현장 런타임) | [단일 모델 내보내기](../backend/api/routes_export.py)와 [전체 플로우 패키지](../backend/engine/flow_package.py)는 오프라인 전달물을 만든다. 실장비 연속 입력용 상시 API 서비스, 최대 추론시간 보장, 장치별 최적화와 현장 검증은 없다([공식 현장 적용](https://www.neuro-cle.com/en/feature/deployment)). |
| 양자화/경량화 | 미구현 | [추론 UI](../src/renderer/components/inference/InferenceCenterStudio.tsx)에서 FP16 선택은 비활성이다. 현재 ONNX/TorchScript 변환을 공식 [양자화](https://www.neuro-cle.com/en/feature/training)와 동일시하지 않는다. |
| 검사 이력·작업자 REVIEW·CSV/JSON | 구현(기능·실데이터 확인) | [이력 API](../backend/api/routes_inspections.py)는 저장된 플로우·체크포인트·이미지 출처를 확인한 뒤 서버에서 실행하고, 프로젝트별 SQLite에 원판정과 별도 작업자 판정 이력을 남긴다. [테스트](../backend/tests/test_inspection_history.py)는 재시작 후 재열기, 클라이언트 원판정 조작 거부, 중단 작업 처리, 미검사 행의 작업자 판정 거부를 다룬다. 최신 네이티브 앱에서 실제 이미지 8/8장 완료(NG 6, REVIEW 2, 오류 0), 작업자 REVIEW 사유 저장, 5단계 이동 중 run 종료와 잔여 6장 skipped를 확인했다. 재시작 후 CSV·JSON 재열기는 이전 앱 QA의 근거이며, 최근 서버 실행 변경 이후 전체 재검증은 남아 있다. |
| 중앙 서버형 다수 장비 관제·자동 재학습 | 미구현 | 여러 서버의 수동 프로필 선택과 원격 작업은 있다. 공식 [중앙 관제/MLOps](https://www.neuro-cle.com/en/feature/mlops)의 다중 라인 수집→라벨 배포→재학습→승격→운영 모니터링은 없다. |

## 5단계 플로우차트 집중 감사

뉴로클은 [공식 학습 페이지](https://www.neuro-cle.com/en/feature/training)에서 여러 검사 모델 연결과 적용 전 Inference Center 검증을, [Neuro-T 제품 페이지](https://www.neuro-cle.com/en/product/neuro-t)에서 복합 모델 프로젝트의 설계·검토를 설명한다. 우리 구현은 아래와 같은 **제약된 실행 가능 DAG**이다. 사용자 요구의 `5단계`는 앱의 다섯 번째 화면이며, 새 `5모델 체인` 템플릿과 구분한다.

| 플로우 동작 | 상태 | 코드·검증 및 경계 |
| --- | --- | --- |
| 노드 추가·이동·연결·삭제·확대/축소 | 구현 | [편집기](../src/renderer/components/flowchart/FlowchartStudio.tsx), [그래프 검사](../src/renderer/components/flowchart/flowchartGraph.ts), [UI 검증 스크립트](../scripts/verify-flowchart-editing.js). 모든 세부 조작의 이번 최종 빌드 클릭 검증은 별개다. |
| 원본 픽셀 좌표의 고정 ROI | 구현(실이미지 검증) | [편집기](../src/renderer/components/flowchart/FlowchartStudio.tsx)에서 X/Y/너비/높이를 입력하고, [엔진](../backend/engine/flowchart_engine.py)에서 원본 좌표를 유지한다. 실제 고해상도 이미지·완료 모델의 앱 엔진과 독립 패키지 결과가 일치했다. ROI 회전·캔버스 드래그 편집은 지원하지 않는다. |
| 단일, 병렬, 검출→ROI 검사, 검사→검사 연속 연결 | 구현 | [엔진](../backend/engine/flowchart_engine.py), [API](../backend/api/routes_flowchart.py), [회귀 테스트](../backend/tests/test_flowchart_editable_graph.py). 입력 영상/ROI/결과의 edge type을 검증하고 ROI는 원본 좌표로 복원한다. |
| 모델 결과별 pass/fail/review 분기와 무조건 edge | 구현 | [엔진](../backend/engine/flowchart_engine.py)과 [테스트](../backend/tests/test_flowchart_editable_graph.py). 게이트를 통과하지 않은 노드는 실행 근거에서 제외한다. |
| 판정 정책과 불완전 결과 처리 | 구현(제한) | 한 개 decision에서 `any_defect_is_ng`, `score_gt_threshold`, `max_flaws_allowed`를 지원한다. 빈 ROI·모델 제한 등은 REVIEW 근거로 기록하고 암묵적으로 NG로 바꾸지 않는다. 추가 정책·사용자 정의 수식은 없다. |
| 5모델 체인/조건 검사 템플릿 | 구현(템플릿) | [템플릿 API](../backend/api/routes_flowchart.py), [엔진](../backend/engine/flowchart_engine.py). 5모델 모두 실제 체크포인트를 연결해 실데이터로 돌렸다는 뜻은 아니다. |
| 모델 선택·출처 확인·저장본 버전 복구 | 구현 | 완료 체크포인트 catalog/검증, 프로젝트+출처별 저장, immutable 버전, active pointer를 [플로우 API](../backend/api/routes_flowchart.py)에서 제공한다. [편집기](../src/renderer/components/flowchart/FlowchartStudio.tsx)는 선택 목록과 저장 버전을 사용한다. |
| 노드별 실행 trace·ROI 근거·OK/NG/REVIEW 출력 | 구현 | [엔진](../backend/engine/flowchart_engine.py)의 노드 입력/출력 건수·선택 edge·skip 이유와 [결과 UI](../src/renderer/components/flowchart/IntermediateCropDrawer.tsx). 출력 1~3개로 제한한다. |
| 외부 GPU에서 연속 플로우 실행 | 구현(실측 범위) | 등록한 GPU 프로필에서 **검출→분할→분할의 3모델 노드/2종 체크포인트**와 실제 이미지 1장을 실행했다. 결과 NG, 검출 ROI 4개, 전 단계 trace를 확인했다. 상세 결과 JSON은 고객 이미지 정보가 있어 Git에서 제외했다. 5개의 서로 다른 체크포인트·조건 분기 전체의 원격 실데이터 검증은 아직 없다. |
| 플로우 전체 오프라인 패키지 | 구현(API·선택 이미지 결과 일치 확인) | [내보내기 API](../backend/api/routes_export.py)는 활성 프로젝트의 저장된 플로우 버전과 출처·작업 유형이 맞는 완료 체크포인트를 확인한 뒤 그래프·모델·실행 코드·SHA-256 목록을 묶는다. [독립 실행기](../backend/engine/flow_package_runtime.py)는 손상 파일을 거부한다. 선택한 실제 이미지로 앱 CPU 엔진과 독립 패키지의 판정·ROI·노드 결과를 대조한 [테스트](../backend/tests/test_flow_package.py)가 통과했다. [6단계 UI](../src/renderer/components/inference/FlowPackagePanel.tsx)는 검증 생략 시 `미검증`으로 표시한다. 단일 이미지의 동일성은 대상 장비 배포·연속 입력·실시간 성능 검증을 대신하지 않는다. |
| 무제한/임의 타입의 범용 플로우 엔진 | 미구현 | 현재 노드는 input, fixed ROI, detection crop, inspection, decision, output이며 모델 1~8개·단일 decision·출력 1~3개, 모델 입력 edge 한 개의 DAG만 허용한다. 루프, 이미지 정렬/OCR 노드, 임의 커스텀 연산, 모델별 서버 분산, ROI별 독립 조건 결합은 지원하지 않는다. |

### 5·6단계 화면 흐름 감사와 남은 디자인 작업

공식 [Neuro-T 제품 소개](https://www.neuro-cle.com/en/product/neuro-t)와 [학습 기능](https://www.neuro-cle.com/en/feature/training)은 복수 모델을 플로우차트로 연결하고 Inference Center에서 적용 전 검증하는 **작업 흐름**을 설명한다. 공개 페이지는 버전 선택 방식, 버튼 배치, 검사 이력 UI의 상세 사양을 제공하지 않는다. 아래는 그 흐름을 기준으로 현재 [5단계 편집기](../src/renderer/components/flowchart/FlowchartStudio.tsx)와 [6단계 검사·내보내기](../src/renderer/components/inference/InferenceCenterStudio.tsx)를 읽어 도출한 **우리 제품의 개선 항목**이며, 뉴로클 화면에 특정 버튼이 존재한다는 주장은 아니다.

| 우선 | 현재 동작과 사용자 혼동 지점 | 남은 화면 개선·검증 기준 |
| --- | --- | --- |
| P0 코드 반영, 초안 경고 앱 확인 필요 | [5단계](../src/renderer/components/flowchart/FlowchartStudio.tsx)는 결과에 실행 당시의 `미저장 초안` 또는 `활성 저장 버전 ID`를 표시한다. [6단계 배치](../src/renderer/components/inference/BatchInspectionPanel.tsx)와 [패키지](../src/renderer/components/inference/FlowPackagePanel.tsx)는 미저장 변경을 알리고 5단계 저장 화면으로 돌아가는 동작을 제공한다. 6단계 작업은 저장본을 사용한다. | 최종 앱에서 초안 검사→저장 없이 6단계 이동→배치/패키지 경고와 실행 대상 식별자를 확인한다. 저장·활성화 후 경고가 사라지고 동일 그래프를 쓰는지 확인한다. 별도 선택 대화상자는 아직 없다. |
| P0 코드·기본 앱 확인, 경계 사례 필요 | 6단계 배치와 패키지는 기본적으로 `활성 저장 버전`을 고른다. 두 패널의 [공통 카드](../src/renderer/components/flowchart/SavedFlowIdentityCard.tsx)는 버전 ID·그래프 SHA-256·모델 식별자를 표시한다. [배치 실행 이력](../src/renderer/components/inference/BatchInspectionPanel.tsx)은 별도로 검사 시작 시 확인한 체크포인트 SHA-256을 표시한다. 최신 네이티브 앱에서 실제 검사에 쓰인 버전·그래프·두 체크포인트 SHA 표시를 확인했다. 배치는 시작 직전에 활성 버전과 그래프 해시를 다시 확인하고 그 그래프를 요청에 고정한다. 패키지에서는 다른 저장 버전을 사용자가 직접 선택할 수 있다. | 최종 앱에서 활성 버전이 최신 버전과 다를 때 5단계·배치·패키지 기본 선택과 실제 요청을 대조한다. 패키지에서 비활성 버전을 선택할 때도 해당 ID/해시/모델이 보이는지 확인한다. 카드에 프로젝트·출처·작업 유형을 한곳에 묶어 표시하는 작업은 남아 있다. |
| P1 코드·화면 이탈 앱 확인, 프로젝트 전환 필요 | [배치 검사](../src/renderer/components/inference/BatchInspectionPanel.tsx)는 화면 이탈·페이지 종료 시 해당 컴포넌트가 생성한 검사 run만 `stopped`로 종료 요청한다. 백엔드는 run 생성 당시 프로젝트로 이력과 종료 요청을 연결한다. 네이티브 앱에서 8장 배치 중 2장 처리 후 5단계로 이동해 run `stopped`, 나머지 6장 `skipped`, 6단계 재진입 후 이력 재표시를 확인했다. | A→B 프로젝트 전환 중 늦은 실행·종료 응답이 A의 DB와 모델에만 연결되는지는 백엔드 회귀 테스트에서 확인했고, 네이티브 전환은 별도 검증한다. 개발 모드 HMR 재현과 프로세스가 run 생성 응답 전에 종료될 때의 상시 만료/복구 정책도 남아 있다. |
| P1 | 연결 오류는 상단 문구로 나오지만 [편집기](../src/renderer/components/flowchart/FlowchartStudio.tsx)에서 문제 노드/edge로 곧바로 이동하거나 해당 포트를 강조하는 흐름은 없다. 복합 조건 플로우에서는 어느 분기가 막혔는지 찾기 어렵다. | 저장/실행 전 검사에서 잘못된 노드·edge를 선택·강조하고, 미연결 모델/필요 입력형/도달하지 못하는 출력에 대한 수정 동작을 제공한다. 정상·NG·REVIEW 경로를 샘플 이미지로 확인할 수 있게 한다. |
| P1 | [패키지 패널](../src/renderer/components/inference/FlowPackagePanel.tsx)은 성공 시 경로와 1장 결과 일치 여부를 보여주지만, 실패 응답의 구조화된 `package_path`/불일치 항목은 화면에 직접 표시하지 않는다. 패키지 자체도 현장 API 서비스나 장비 배포 상태가 아니다. | 실패 화면에 원인·불일치 필드·패키지 위치를 표시하고 재검증 동작을 제공한다. 성공 화면에는 파일 열기, manifest 확인, 오프라인 실행 명령과 대상 환경 검증 결과를 분리한다. 공식 [현장 적용 기능](https://www.neuro-cle.com/en/feature/deployment)의 다중 모델 단일 API는 별도 Neuro-R 런타임 범위로 구분한다. |
| P2 | [배치 결과](../src/renderer/components/inference/BatchInspectionPanel.tsx)는 OK/NG/REVIEW와 이미지별 trace, 작업자 수정 이력을 제공하지만 `아직 작업자 확인이 필요한 결과`만 모은 큐와 수정 전후 요약은 없다. | 미처리 REVIEW/오류 필터, 원판정→작업자 판정의 명확한 대비, 처리 건수/잔여 건수, 수정 사유 추적을 한 검사 세션 안에 배치한다. 이는 공개 뉴로클 사양의 확정 복제가 아니라 우리 운영 검토 흐름의 개선이다. |

## 검증 경계와 다음 수용 기준

1. **현재 확인된 실데이터 경로:** 고객 제공 이미지 중 80개 대응 라벨 이미지로 분할·검출을 각 1 epoch 학습하고 8개 test 이미지를 평가했다. 8개 라벨 후보 일괄 추론과 배치 검사, 외부 GPU의 3모델 노드 플로우를 실행했다. 1 epoch와 NG 중심 데이터는 검사 정확도 또는 정상 제품 과검률을 입증하지 않는다([기존 실데이터 QA](QA_NEUROT_FUNCTIONAL_2026-09-29.md)).
2. **5단계 추가 수용:** 최신 네이티브 앱에서 저장된 플로우의 6단계 실제 이미지 8장 검사와 버전·그래프·체크포인트 식별은 확인했다. 5개의 서로 다른 완료 모델로 만든 체인, 조건 분기에서 건너뛴 노드의 trace, 초안→저장→재시작 복구, 원격 실행에서 6단계 인계까지의 연속 조작은 별도로 확인한다.
3. **비교 기능의 남은 큰 차이:** 소량 라벨에서 모델을 자동 학습하는 흐름/문장 레이블링, 공식 모델군 전체, 자동 구조·초매개변수 탐색, 성능 보존 재학습, 후보 승격·롤백, 생산 장비 지연시간·중앙 관제가 빠져 있다. 이 기능은 버튼이나 파일명이 존재한다는 이유만으로 `구현`으로 바꾸지 않는다.
4. **운영 수용:** 정상(OK) 표본 추가 후 동일 holdout에서 오검·미검·ROI 위치 품질을 계산하고, 현행/후보 비교·승인·배포·롤백을 기록해야 한다. 현장 인라인 사용은 별도의 장비/택트/장애 복구 시험이 필요하다.
5. **프로젝트 전환 수용:** Studio 라벨·분할 격리 코드는 백엔드를 새로 실행한 뒤 확인한다. 기존 전역 라벨과 분할은 새 프로젝트 범위에 최초 1회 복사되며 전역 원본은 보존한다. 프로젝트 라벨이 이미 있으면 자동 병합하지 않는다. 분할은 1회 처리 마커를 남겨 프로젝트에서 삭제한 뒤 재열어도 전역 분할이 다시 들어오지 않는다. 같은 원본을 공유한 A/B 프로젝트의 서로 다른 라벨·분할 유지가 [회귀 테스트](../backend/tests/test_project_annotation_isolation.py)에 포함됐다.

### 이번 코드 감사 증거

- 전체 백엔드 회귀: `python -m pytest backend/tests -q --disable-warnings` → **690 passed, 11 skipped, 95 warnings**(2026-09-30, 166.90초). 11건 skip과 경고가 있으므로 모든 환경·외부 서버 조합의 통과로 해석하지 않는다.
- 프런트 집중 회귀: 배치 19, 플로우 기본 16, 편집 14, 시작 11, 뷰포트 4, 버전 활성화 1, 단계 인계 5로 **70개 통과**. 프로젝트 recipe/전환 회귀 스크립트도 통과했다. `npm run typecheck`와 `npm run build`가 통과했고 빌드에는 기존 Vite dynamic-import·큰 청크 경고가 남았다.
- 최신 네이티브 앱: 저장본을 이용한 실제 이미지 **8/8장 완료, NG 6·REVIEW 2·오류 0**, 작업자 REVIEW 사유 저장을 확인했다. 별도 중단 재현에서는 2장 처리 후 5단계로 이동했고 run `stopped`, 나머지 6장 `skipped`, 6단계 재진입 시 이력 재표시를 확인했다. 프로젝트 전환과 HMR 경합의 네이티브 검증은 남아 있다.
- 원격 3모델 노드 실행의 모델·이미지·패키지 해시와 노드 trace는 Git 제외된 로컬 QA 기록에 보존한다. 고객 데이터 경로·파일명·실제 식별자는 이 문서에 넣지 않았다.
- 이 문서는 코드 감사와 위 범위의 검증 기록이다. 실데이터 기능 검증은 모델 품질·현장 승인·Neuro-T 제품 동등성을 입증하지 않는다.
