# 남은 16개 대상의 실행 기록

2026-10-07. 기존 16개를 모두 추적하며 실행 가능한 구현과 검증을 진행한다. 현재 부모 구현 검증은 **67/82**, 미완료 구현 표시는 **15/82**, 전체 실사용 수락은 **0/82**이다. S5-10만 이번에 구현 검증으로 승격했다. 개별 검사·slice·부모 수는 서로 더하지 않는다.

| ID | 작업 | 실제 완료한 부분 | 남은 조건 |
|---|---|---|---|
| S1-08 | 이력·전역 마이그레이션 | 종료된 학습·라벨·epoch·6종 specialist 및 7종 원격 작업 원본 보존; 독립 flow report의 모델·서버별 원본 검증 추가, 관련186개 통과 | 실행 중 worker/불확실 lease의 소유권·fence 이관 및 구형 프로토콜 |
| S5-01 | 독립 검사 서비스 | 독립 프로세스·CPU 검사·owned start/stop/restart 및 Linux 격리 실행 | 실제 전용 계정·재부팅·장비 권한·GPU/camera 서비스 운용; Windows 실기 QA는 면제 |
| S5-10 | 서비스 보안 | 구현 검증 완료: HTTPS·인증·세션·권한·경로·외부 secret 저장소·원자적 이전·실제 앱 readback | 운영 인증서·계정 배치와 실제 target/독립 최종 승인 |
| S6-02 | Windows 설치 | 설치 구성·unsigned packaged CPU/restart 검증 기록 보존 | Windows11 설치·제거 실기 QA는 사용자 면제이며 통과로 집계하지 않음 |
| S6-03 | 선택 runtime pack | 별도 FP32/FP16 pack 및 OpenVINO/NNCF INT8 pack64파일 설치·재검증; 실제 Xeon CPU 양자화·full flow·OCR 및 관련140개 통과 | 다른 provider/장치·대표 정답 cohort·native license/publisher 승인 |
| S6-04 | 오프라인 업데이트 | owned POSIX portable 앱·DB cutover, crash finish, 새 account/profile 보존 forward recovery; actual frozen CLI5회·별도 CPU known-image2회; 새 main/preload/renderer 검토·복구 연결, 관련Python125/Node40 및 UI2개 통과 | 실제 OS installer·설치된 home·native layout·실 publisher 서명·native positive 설치 및 installed-app known-image handoff |
| S6-05 | 공개 CI | 이전 정확한 source의 complete hosted success; heartbeat 검사 수정·Mac58/Linux119 통과 | 최신 게시 source 전체 CI 완료; 기존 queue 및 실패 기록 보존 |
| S6-06 | 서명·채널 | unsigned 상태와 checksum/source 연결 | 실제 publisher/서명 키·최종 artifact와 stable/beta 운영 정책 |
| S7-01 | 전체 기능 행동 coverage | 177개 curated action, 319개 시나리오 검증; portable 버튼9개/15개 근거 추가 | 920개 curated 시나리오와 누락 메뉴/shortcut; 기존156개 feature 전체 수락은0 |
| S7-02 | 10개 모델군 시나리오 | 실제 family별 학습/추론·다중 checkpoint·flow/package control 기록 | 사람이 검토한 대표 분류/정상/OCR/OBB/향상/GAN truth와 품질 판정 |
| S7-03 | Windows 실사용 | 기존 unsigned runner 결과 보존 | 사용자 면제. 신규 실기 테스트를 시작하지 않으며 pass로 바꾸지 않음 |
| S7-04 | 동시 작업·장애 | 2개 계정/agent·actual CPU·재접속·권한/충돌 control; controlled signed portable 앱·DB cutover 및 crash recovery | 물리적 장애와 실제 publisher가 서명한 native 앱 업데이트 |
| S7-05 | 규모·72시간 운전 | 별도 연속 운전 진행 중: 13.06시간/782 cycles; 기존 실패와 합산하지 않음 | 72시간 종료 receipt 및 실제 target RAM/disk/p95/tact 측정 |
| S7-06 | 공정 품질·장비 | simulator 계약과 실행/품질 상태 구분 | 실제 camera/PLC/MES 및 제품/Lot별 정답·미검/과검 승인 기준 |
| S7-07 | 공개 후보 판정 | 최신 standalone backend freeze·startup/restart 및270개 의존성 license bytes 누락0 | native library 조건·업데이트/coverage·실제 서명·독립 리뷰·pilot |
| S7-08 | 사용자 pilot·운영 | feedback/지원/backup 절차 및 승인 경계 기록 | 실제 처음 쓰는 참여자 pilot과 유지보수 책임·SLA 결정 |

## 이번 변경과 근거

- 기존 6종 specialist relocation과 실패/취소 partial artifact의 원본 해시 보존을 보완했다. 7종 원격 작업의 원본 journal/spec/received manifest/output membership를 종료 이력에 연결했다. 강제 재실행도 이전 run 원본을 덮지 않는다. 미확인 launch/exit·불확실 lease를 새 실행 권한으로 바꾸지 않는다.
- host-bound secret store와 owner-only 원자적 legacy credential 이전은 값·선택 target·감사기록을 유지한다. 실제 Electron/browser에서 실패, 응답 유실, 재조회, 재열기를 확인했다.
- 오프라인 공급 파일은 exact-version wheel/sdist 또는 wheel metadata+공식 immutable release commit에 묶인 원본 license bytes만 읽는다. 다운로드·설치·실행을 compiler가 자동 수행하지 않는다.
- compiler의 직접 실행 import 실패, Linux 자원 export 누락과 fake SSH test의 pretrained cache 의존은 실패 기록을 남긴 뒤 수정했다. 120ms heartbeat 실패도 실제 갱신 event를 관찰하는 검사로 교정했다. Production 만료·fence·인증 요건은 완화하지 않았다.

## 검증 기록

- 원격 이력 관련 Mac163 passed; security74 passed; account/session37 passed.
- license/compiler 관련33 passed, 직접 실행 supplier24 passed; archive/heartbeat58 passed. 중복되는 검사이므로 합계를 고유 검사 수로 쓰지 않는다.
- 최신 source867f4a9의 격리 Linux CPU119 passed. Network none, GPU 접근 없음, read-only container root, capabilities ALL 제거, 실행 뒤 owned container removed. 정확한 committed 입력781개와 resource14개를 해시로 연결했다.
- actual standalone backend source177b233: executable SHA-256 `3702ecb7c0ee159c29e88c6d762cc03cb1eadcbf0aaaaee89f4ec2957f289fcb`; build325 resources, 11809 files; startup/restart and two known-image CPU executions passed. The original package and image hashes stayed unchanged. Python270 component/336 license files/14 supplemental suppliers/missing0. 서명·native library legal compatibility·공개 배포 승인은 포함하지 않는다.
- actual GUI receipts는 `docs/verification/receipts/2026-10-07-fleet-credential-migration-{browser,electron}.json` 및 `2026-10-07-actual-fleet-rollout-1376a34-browser.json`이다. 전체 기능 coverage나 사람이 승인한 품질을 증명하지 않는다.
- complete hosted source7e183c9 success와 newer0eadf8c의2319 passed/7 skipped/1 failed를 함께 보존한다. 최신 source의 full CI는 따로 확인한다.
- independent72h frozen sourcea184b1e는 계속 실행 중이다. 이전50-cycle disk-reserve 실패와 합산하지 않는다.

## 공개 receipt

`2026-10-07-remaining-contract-execution.json`, `2026-10-07-linux-qualification-final.json`, `2026-10-07-latest-frozen-backend-177b233.json`을 `docs/verification/receipts/`에서 확인한다. 원본 logs/XMLs/screenshots/vendor archives는 소유한 비공개 외장 scratch에 보존한다. 기존 source trees/영수증/soak 프로세스를 삭제하거나 중단하지 않았다.

독립 리뷰는 subagent 실행 한도로 미실행이다. Root 검토를 독립 승인으로 기록하지 않는다. 서명 키·실제 장비·참여자·대표 품질 정답·72시간 경과를 대신 만들어 완료 처리하지 않는다.

## 추가 실행: portable 앱과 DB의 업데이트 transaction

- source8bf877d의 standalone binary SHA-256 `1acb6e4777c7453a517259ed8d98d1dd028f012e397acdbe8561c43b4c453330`, build identity `81f4ac434219be3c939e8fe40fc24460f3c58594f0d21a0086838aeafaa3fb93`. 326개 runtime resource를 source8bf877d 및 test-only sourceebba4cc와 각각 대조했다. 기존 freeze 기록은 바꾸지 않는다.
- Mac42 passed에는 실제 Node/Python trust 계약 비교12건이 포함된다. 관련110 passed, license/compiler33 passed. Linux108 passed/12 skipped(Node 부재); Linux skip을 통과로 집계하지 않는다. 최초 dependency 부재 및 child PYTHONPATH 손실 실패는 수정 전 기록으로 보존한다.
- 실제 frozen CLI5회로 1.0.0 설치→1.1.0 업데이트→새 account/profile 쓰기→forward recovery→재열기를 실행했다. DB fence는1→2→3이며 새 데이터가 유지됐다. 동일 binary의 CPU known-image 검사2회는 별도로 통과했다. 실제 Electron 설치 앱에서 이미지가 실행됐다는 근거로 사용하지 않는다.
- 새 freeze의 Python270 component/336 license files/14 supplemental suppliers/missing0를 확인했다. Native library 조건과 공개 승인 및 실 publisher는 미확인이다.
- Linux export의 raw binding에 checkout_dirty=false가 잘못 기록됐다. 실제 checkout에는 관련 없는 untracked 문서가 있었다. 원본 binding을 보존하고 새 receipt에 정정했으며 선택된706개 source+15개 resource의 모든 bytes를 commit과 독립 대조했다. Clean checkout을 주장하지 않는다.
- 공개 근거: `docs/verification/receipts/2026-10-07-frozen-portable-update-8bf877d.json`, `docs/verification/receipts/2026-10-07-linux-portable-update-ebba4cc.json`. S6-04 전체 구현·GUI·운영 수락은 아직 pending이며 부모 수는67/82, 미완료15/82를 유지한다.

## 추가 실행: 독립 portable flow 보고서 이력

- source087b927에서 기존 학습 부모가 없는 독립 `reports/remote_flow`를 원래 등록 프로젝트에 연결한다. 한 디렉터리의 서로 다른 모델 목록·서버 profile도 각 archive의 원본 spec/digest로 검증한다. 현재 checkpoint가 나중에 변경돼도 과거 보고서의 원래 binding을 바꾸지 않는다.
- 실제 보고서 생성 경로와 controlled transport를 사용하는 신규25건 및 기존 원격 작업30건(55 passed), 전역 이전/복구/학습 이력/앱 업데이트 관련186 passed를 확인했다. 변조/미확인 종료/linked path를 거절하며 SSH 접속·worker 소유권·품질 승인을 생성하지 않는다.
- 원래 red/fixture 교정/중단 검사 로그를 보존했다. 완료 XML이 없는 중단 검사를 pass로 세지 않는다. 공개 receipt: `docs/verification/receipts/2026-10-07-portable-flow-history-087b927.json`. 실행 중 worker의 fence 이관과 구형 프로토콜은 아직 남아 있으므로 S1-08 부모 전체는 pending이다.

## 추가 실행: 별도 OpenVINO 오프라인 runtime pack

- source087b927의 선택된723개 입력을 커밋과 독립 대조했다. 별도 pack12개 파일/76,128,570 bytes의 inventory SHA-256은 `935c8038c38d03cb877d8d54c32a3940ad5144ea28ec65ce1289a873d0226bb7`이다. OpenVINO2026.4.1·NumPy2.2.6·packaging24.2·telemetry2025.2.0의 정확한 원본 wheel·license bytes를 보존한다.
- 기본 이미지에 OpenVINO가 없는42번 Xeon CPU에서 inactive pack 설치→복사 해시→동일 재설치를 확인했다. 검사용 전용 venv에서 실제 FP32/FP16 변환·두 heldout tensor 및 full-flow 검사를 실행했고125 passed/0 skipped이다. 실제 tiny control은 Torch보다 느렸으므로 속도 향상을 주장하지 않는다.
- 최초 HTTP example export 누락(124 passed/1 error)과 두 번째 child Python 환경 선택 실패(124 passed/1 failed)를 보존했다. Production의 trusted child import 경계는 바꾸지 않았다. 최종 container exit0/owned removed, network none/GPU 없음/read-only root이다.
- 공개 receipt `docs/verification/receipts/2026-10-07-openvino-offline-pack-087b927.json`. INT8/NNCF·다른 장치·대표 정답 품질·실제 publisher/native legal/독립 승인은 pending이다. 부모 수는67/82·미완료15/82를 유지한다.

## 추가 실행: 명시적으로 선택한 portable 설치의 검토·복구 화면

- source41f6d0b에서 current Studio/user home과 겹치지 않는 별도 owned portable 설치만 선택한다. packaged POSIX 앱·백엔드의 실제 배포자 서명과 고정 trust가 없으면 폴더 선택창 전에 거절한다. Renderer가 임의 명령·경로·새 authority를 제공할 수 없다.
- 검토는 원본·앱 파일·팩·채널·배포자·이전 버전·DB fence에 묶인다. 검토 뒤 새 데이터가 쓰이면 적용을 거절한다. 응답 유실 시 자동 재설치하지 않으며 상태를 다시 읽는다. 복구는 화면에 표시한 installation/update ID와 현재 원본을 다시 대조한다.
- Python 관련125 passed/Node40 passed, 두 타입 검사, browser 및 actual Electron 각1개 통과했다. Browser에서는 실제 main manager와 source Python signed transaction으로 새 account/labels 보존·forward recovery·재열기를 확인했다. 실제 Electron은 미준비 개발 앱의 거절 경로만 확인했다. Native positive 서명은 fixture로 대신 승인하지 않는다.
- 당시 미커밋 근거 문서 때문에 harness의 source_dirty=true를 보존했다. 이 실행을 clean action acceptance receipt로 쓰지 않는다. 공개 receipt `2026-10-07-portable-update-review-41f6d0b.json`. 부모67/82·미완료15/82·전체 수락0은 유지한다.

## 추가 실행: 검토 계약이 포함된 실제 standalone binary

- source41f6d0b의 resource327개를 커밋 원본과 독립 비교했다. Executable SHA-256 `d7ded7066805d6dbc3203e9648629cbb57119cca018557d4b2b062852d432db3`, build identity `eaa78cc4ca72061bd7c3e30d7a2b5d205a2309881bcd2d1f0b63f98849007bdd`. startup/restart와13회 actual CLI로 source-CAS 거절·1.0.0→1.1.0 전환·새 account/labels 보존 forward recovery·재열기를 확인했다. DB fence는1→2→3이다.
- 같은 binary에서 별도 CPU 기준 이미지 검사2회가 완료됐다. Python270 component/336 license files/14 supplemental suppliers/missing0 및 원문 해시를 대조했다. 잘못 선택한9개 supplier의5개 누락 빌드와 preflight field 오류는 각각 기록으로 보존했다. 공개 배포나 native library 법률 승인은 포함하지 않는다.
- 공개 receipt `2026-10-07-reviewed-frozen-portable-update-41f6d0b.json`. Native positive 서명·installed home/OS installer 및 실제 고객 앱의 기준 이미지 인수인계는 별도로 남아 있다.

## 추가 실행: 깨끗한 소스의 portable 버튼별 근거

- source487560a의 browser/Electron2 passed/0 retry를 확인했다. 원장에9개 버튼·입력 action과15개 실제 시나리오를 추가했다. 목록 범위가 늘어 현재 curated177 actions/319 verified/920 pending이며156개 기능 전체 수락0은 유지한다.
- 실제 자식 프로세스를 before_database/after_database에서 각각 exit91시켰다. 전환 전 abort는1.0.0/fence2를 유지했고, 전환 후 abort는 제공되지 않았으며 finish 후 재열기가1.1.0/fence3를 읽었다. 실제 정전과 배포자 서명 검증으로 대체하지 않는다.
- 최초 검사의 재열기는 아직 처리 중인 이전 문구를 완료로 읽어 실패했다. 실패 원본을 보존하고 committed 상태와 버튼 재활성화를 모두 기다리도록 교정했다. 소프트웨어의 fence/권한/복구 조건을 완화하지 않았다.
- 공개 receipt `2026-10-07-portable-update-clean-487560a-browser.json`, `2026-10-07-portable-update-clean-487560a-electron.json`. 실행 후 owned backend/Electron 프로세스·포트 종료 및 escaped/unowned 참조0을 확인했다.

## 추가 실행: 별도 INT8 pack과 최적화 응답의 원본 연결

- sourcee1ebb16의725개 입력을 커밋 원본과 다시 대조했다. OpenVINO2026.4.1/NNCF3.4.0 및 전체22개 pinned wheel의 원본40개 license/notice를 해당 archive member와 독립 대조했다. 별도 pack64파일/127,569,415 bytes, inventory SHA-256 `6ee309f64fb50c69d6c959055656034adf96b96828babece76ddd8dddbec0add`이다.
- 실제42번 Xeon CPU의 network-none/no-GPU 격리 환경에서 inactive pack 설치·재설치 및 payload 원본 보존을 확인했다. 별도 venv에서 FP32/FP16/INT8, saved whole-flow INT8, OCR geometry 및 이력/업데이트 회귀140 passed/0 skipped이다. Container exit0/owned removed이다.
- INT8의 실제 quantized operation3개, 보정2개/별도 검증2개, 최대 절대 오차0.000689812, argmax 불일치0을 기록했다. Tiny control 속도비0.556으로 Torch보다 느렸으므로 속도 향상을 주장하지 않는다. 대표 현장 품질 승인은 아니다. 공개 receipt `2026-10-07-openvino-int8-offline-pack-e1ebb16.json`.
- 최초 준비 스크립트가 pydot의 `MIT.txt`/`Python-2.0.txt`를 놓쳤다. Wheel의 정확한 METADATA License-File 경로로 원문을 확인했으며 라이선스 라벨이나 다른 원문으로 대체하지 않았다. 별도 byte-audit의 필수 저장 공간 인수 누락도 실패 원본을 남긴 뒤 실제 여유 공간 값으로 교정했다.
- 최적화 화면의 다른 source 캐시, foreign job poll, source 변경 뒤 늦은 제출 응답을 세 회귀 검사로 재현했다. Cache key에 source/task를 포함하고 모든 작업 응답의 ID·package·source를 확인했다. 기존 명시적 작업 재열기도 유지되며 관련 renderer110 passed이다. Browser1 passed에서는 controlled HTTP 응답을 사용해 잘못된 이력·조회 거절과 정상 조회 재개를 확인했다. 이 UI control을 실제 INT8 실행 근거와 합쳐 native/품질 승인으로 쓰지 않는다.
- 부모 구현67/82·미완료15/82·전체 수락0을 유지한다. S1-08의 살아 있는 worker 인계는 store/lease/journal을 함께 전환해야 하며, 현재 drain 요건을 제거하는 방식으로 처리하지 않았다.


## 추가 실행: 원본 연결의 실제 앱 회귀와 오류 회복

- clean source32160f4에서 browser/Electron2 passed/0 skipped/0 retry이다. Browser는 controlled 최적화 HTTP 응답으로 다른 source cache와 foreign job 조회를 거절한다. Electron은 실제 CPU→OpenVINO 변환, 별도 fixture 검토, 저장 패키지 재열기와 owned IR service의 실제 이미지 upload/inference를 완료했다. 원본 package/model/input 해시와 owned process/port 종료를 대조했다.
- 합성 모델은 정상으로만 분류해 NG를 놓친다. 두 이미지의 원본/변환 일치와 permissive fixture review를 현장 품질·사람의 승인으로 사용하지 않는다. 공개 receipt `2026-10-07-optimization-binding-32160f4-{browser,electron}.json`이며 기존3ab27ae 기록을 덮지 않고 새 source 실행 근거를 함께 보존한다.
- 정상 poll 복구 뒤 남는 이전 ID 오류와 취소 재시도 성공 뒤 남는 오류를 실제 red 검사로 재현했다. 해결된 poll 오류만 지우며 별도 취소 실패는 보존한다. 새 명시적 취소 요청은 이전 요청 오류를 정리한다. 관련 inference renderer37 passed/0 skipped, 두 타입 검사 통과이다.
- 추가 controlled browser lifecycle에서는 unavailable/empty·보정/검증 선택 해제·422 제출 실패·foreign cancel 거절·정확한 취소와 cache 재열기를 확인했다. 실제 worker 취소나 model 실행으로 대체하지 않는다. 첫 dirty 실행 기록을 보존하고 버튼별 원장에는 별도 clean 실행만 연결한다.
