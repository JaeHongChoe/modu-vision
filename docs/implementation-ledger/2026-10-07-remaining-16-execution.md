# 남은 15개와 직전 완료 항목의 실행 기록

2026-10-07. 기존 16개를 모두 추적하며 실행 가능한 구현과 검증을 진행한다. 현재 부모 구현 검증은 **67/82**, 미완료 구현 표시는 **15/82**, 전체 실사용 수락은 **0/82**이다. S5-10만 이번에 구현 검증으로 승격했다. 개별 검사·slice·부모 수는 서로 더하지 않는다.

| ID | 작업 | 실제 완료한 부분 | 남은 조건 |
|---|---|---|---|
| S1-08 | 이력·전역 마이그레이션 | 종료된 학습·라벨·epoch·6종 specialist 및 7종 원격 작업 원본 보존; 독립 flow report의 모델·서버별 원본 검증 추가, 관련186개 통과 | 현재 local 기본·원격 학습·native specialist worker의 실제 POSIX live 이관 완료; 구형 프로토콜·native target·독립 수락 검증은 남음 |
| S5-01 | 독립 검사 서비스 | 독립 프로세스·CPU 검사·owned start/stop/restart 및 Linux 격리 실행 | 실제 전용 계정·재부팅·장비 권한·GPU/camera 서비스 운용; Windows 실기 QA는 면제 |
| S5-10 | 서비스 보안 | 구현 검증 완료: HTTPS·인증·세션·권한·경로·외부 secret 저장소·원자적 이전·실제 앱 readback | 운영 인증서·계정 배치와 실제 target/독립 최종 승인 |
| S6-02 | Windows 설치 | 설치 구성·unsigned packaged CPU/restart 검증 기록 보존 | Windows11 설치·제거 실기 QA는 사용자 면제이며 통과로 집계하지 않음 |
| S6-03 | 선택 runtime pack | 별도 FP32/FP16 pack 및 OpenVINO/NNCF INT8 pack64파일 설치·재검증; 실제 Xeon CPU 양자화·full flow·OCR 및 관련140개 통과 | 다른 provider/장치·대표 정답 cohort·native license/publisher 승인 |
| S6-04 | 오프라인 업데이트 | owned POSIX portable 앱·DB cutover, crash finish, 새 account/profile 보존 forward recovery; actual frozen CLI5회·별도 CPU known-image2회; 새 main/preload/renderer 검토·복구 연결, 관련Python125/Node40 및 UI2개 통과 | 실제 OS installer·설치된 home·실 publisher 서명·native positive 설치 및 installed-app known-image handoff |
| S6-05 | 공개 CI | 기존 hosted success 보존; 전체 CPU2706 pass/고지 누락1 fail·고지62 및 optional27 repair pass | 최신 게시 source 전체 CI 완료; 기존 queue 및 실패 기록 보존 |
| S6-06 | 서명·채널 | unsigned 상태와 checksum/source 연결 | 실제 publisher/서명 키·최종 artifact와 stable/beta 운영 정책 |
| S7-01 | 전체 기능 행동 coverage | 199개 curated action, 545개 시나리오 검증; retention·template 실제 browser/Electron 근거 추가 | 848개 curated 시나리오와 누락 메뉴/shortcut; 기존156개 feature 전체 수락은0 |
| S7-02 | 10개 모델군 시나리오 | 실제 family별 학습/추론·다중 checkpoint·flow/package control 기록 | 사람이 검토한 대표 분류/정상/OCR/OBB/향상/GAN truth와 품질 판정 |
| S7-03 | Windows 실사용 | 기존 unsigned runner 결과 보존 | 사용자 면제. 신규 실기 테스트를 시작하지 않으며 pass로 바꾸지 않음 |
| S7-04 | 동시 작업·장애 | 2개 계정/agent·actual CPU·재접속·권한/충돌 control; controlled signed portable 앱·DB cutover 및 crash recovery | 물리적 장애와 실제 publisher가 서명한 native 앱 업데이트 |
| S7-05 | 규모·72시간 운전 | 같은 연속 운전 22.01시간/1317 cycles; Mac/Linux10k·100k metadata와80MP decode 측정 완료 | 72시간 종료 receipt 및 실제 target RAM/disk/p95/tact 측정 |
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

- clean source62b20cc의 browser2 passed/0 skipped/0 retry를 원장에 연결했다. 독립 후보4개·별도 취소4개 시나리오를 추가했으며, 새 취소 action도 목록에 포함해178 actions/327 verified/919 pending이다. Intentional422 외 page error/blocked loopback0, owned backend/port 종료 및 teardown leftovers0이다. 전체156개 기능 수락0과 부모67/82·미완료15/82는 유지한다.

- GitHub hosted run37552989986은 exact sourcec2e7b6에서 전체 workflow success로 종료됐다. Run/job/step API 원본 해시를 보존했다. 이 API 관찰에서 테스트 수나1.1GB artifact 내부를 다시 추출하지 않았으며 최신62b20cc 및 이후 게시 소스의 CI 성공으로 옮겨 쓰지 않는다. 공개 receipt `2026-10-07-hosted-source-ci-c2e7b6.json`.


## 추가 실행: 실제 live control 이관과 CUDA pack

- sourcef8b24e8의 owned POSIX 기본 worker는 immutable ready acknowledgment와 원래 PID/create time/command/spec/fence를 증명한 경우에만 현재 generation의 ledger/lease/journal을 따른다. 실제12-epoch CPU 학습은 동일 worker·attempt로 한 번 완료됐고326 관련 검사가 통과했다. Carried lease에 신규 실행 권한을 주거나 불확실 예약을 지우지 않는다.
- source1326b2c의 원격 학습은 원래 등록 profile·uploaded spec·양쪽 journal·실제 SSH identity·handle·observer를 함께 확인한다. 실제42번의 별도 network-none/read-only/no-GPU CPU 학습은 live cutover 뒤 같은 worker를 유지하고40epochs 요청 중 실제15epochs 뒤 기존 trainer의 patience4 정책으로 completed 상태가 됐다.713개 선택 source와48개 원본 PNG를 독립 byte 비교했으며 launch/attempt 각1, 실제 종료 확인 뒤 예약 해제, owned container removed이다.94 관련 및276 이력 회귀 검사가 통과했다. Specialist live와 구형 프로토콜은 별도 계약이 없으므로 전체 S1-08 완료로 세지 않는다.
- sourcef8b24e8의 실제 browser2/Electron2 검사는 modal의 Delete/화살표/undo가 배경 라벨을 바꾸지 않는지와 닫은 뒤 정상 편집·저장·재열기를 확인했다. Saved queue는 오류/불일치/임계값 우선순위, invalid threshold422, 검토자 누락, skip/review 이력, 정확한 cursor 재열기와 원본 변경 거절을 확인했다. Curated187 actions/348 verified/961 pending, 기능 수락0이다. AST의1369 UI 선언은 실행 증거로 세지 않는다.
- source dd163b7의 별도 NVIDIA pack은28 files/316132771 bytes/9 pinned wheels/17 original license members이다. 실제 L40S에서 FP32/FP16 각각80 CUDA kernel events와 CPU 실행0을 관찰했다.2 heldout tensors 및 known image는 원본 해시를 유지하고 최대 오차는 FP32 2.98e-8, FP16 0이다.27 related checks pass/0skip이며 inactive install·재설치 해시와 owned container 종료를 확인했다. CUDA/cuDNN은 기존 immutable qualification image에서 제공하며 pack에 native library를 새로 묶지 않는다. Tiny controls는 현장 품질·Studio job·native legal/publisher 승인이 아니다.
- 이전 CI sourceec127afd는 CPU 단계 성공 뒤 browser165 passed/1 failed/1 skipped로 끝났다. 실패는 파생 데이터의 재학습·비교 후 승인 패널에서 후보 assessment가 보이지 않는 경로이다. 동일 현행 소스의 로컬 focused browser는 통과했지만 CI의 원본 화면과 error context에서 기준·후보가 뒤집힌 것을 확인했다. 늦게 갱신된 추천 모델이 수동 선택을 덮는 경로를2개 red 검사로 재현했고, 수동 선택·비운 선택·임계값을 보존하고 사라진 모델은 거절하도록 수정했다. 관련 renderer54 passed이다. 전체 archive/큰 trace의 검증을 주장하지 않는다. 최신 전체 CI 성공으로 바꾸어 기록하지 않는다.
- 독립72시간 운전은 같은 PID/create time에서15.55h/931 cycles, duplicate/backpressure 각931까지 계속 진행 중이다. 전체72시간 종료·실 target 측정은 미완료이며 이전 시도와 합산하거나 재시작하지 않았다.
- 공개 receipt: `2026-10-07-live-local-control-f8b24e8.json`, `2026-10-07-live-remote-control-1326b2c.json`, `2026-10-07-nvidia-cuda12-offline-pack-dd163b7.json`, modal/queue f8b24e8 browser/electron 각각. Root 검토이며 독립 리뷰는 agent 실행 한도로 미실행이다.

- 저장 검토 큐의 추가 source/queue 선택·margin422·원래 평가 복귀·미저장 라벨 guard·실제 검수 policy gate와 학습 미제출 경로는 exploratory browser/Electron 각1개에서 통과했다. 새 비교 수동 선택과 실제 CPU 비교 및 기존 파생 데이터 재학습은 exploratory browser2개 통과이다. 커밋된 소스로 별도 재실행하며 이 dirty 실행을 clean action 근거로 쓰지 않는다.

## 추가 실행: source5743664의 실제 비교·검수 큐 인계

- Clean committed source5743664에서 browser3/Electron3 passed,0 retry이다. 수동 비교 모델/비운 선택을 추천 갱신이 덮지 않고 실제 CPU 비교 요청과 두-image saved report가 정확한 선택 ID를 유지한다. 기존 파생 데이터의 실제 재학습·비교·원본 복귀도 각각 다시 통과했다.
- 두 저장 평가로 만든 서로 다른 큐의 선택·재열기, invalid margin422와 correction, 최신 평가 대신 정확한 원래 평가로 복귀, 실제 미저장 bbox10,10,30,30 guard와 저장 content를 확인했다. 실제 approved-only readiness가 eligible0을 거절하며, 별도 permissive fixture policy만 학습 준비로 이동했다. 학습 제출이나 사람의 라벨 승인은 생성하지 않았다.
- 원장에 기존 누락 action5개를 추가해192 actions/369 verified/975 pending이다. 목록 확장으로 pending 수가 늘었으며 검증된 시나리오는21개 늘었다. 현재 부모67/82·미완료15/82와 기능/전체 수락0은 유지한다. 모든 owned backend/port/Electron은 종료됐고 teardown leftovers/unowned references0이다.
- 실제 CI 실패의 원본 두 ZIP member 해시와 causal red2/green54, clean UI6개를 별도 repair receipt에 연결했다. 전체 archive와 큰 trace는 검증하지 않았으며 최신 hosted 전체 CI 성공으로 바꾸지 않았다.

## 추가 실행: 전문 모델 원래 실행의 POSIX live 이관

- Clean source9bf7f1e에서 macOS 관련190 passed/0 skipped와42번 Linux40 passed/0 skipped이다. 각 OS에서 실제 CPU OCR 학습2epoch가 같은 원래 backend thread·attempt·fence로 한 번 완료됐고, 원본 이미지·기존 ledger bytes·새 모델 receipt SHA를 확인했다. 원래 private endpoint와 예약은 완료 후 해제됐다.
- Kernel peer PID와 immutable spec/namespace/lease/fence acknowledgment를 함께 확인한다. 다른 process의 가짜 응답·숫자1을 참으로 둔 응답·끝난 closure·변조된 원본을 거절한다. 현재 ledger의 취소는 원래 event를 사용하고, 후속 fence는 원래 publisher를 차단하며 준비 중 중단은 재실행 없이 forward finish한다.
- Linux 첫39 passed/1 failed는 검사용 uvicorn 부재로 실제 학습 전 실패했다. 원본을 보존하고 보관 중인 정확한 offline wheel로 별도 검사용 venv를 보완했다. 최종 network-none/read-only/non-root/no-GPU container는 exit0/owned removed이며716개 선택 source bytes를 commit과 독립 대조했다.
- 공개 receipt `2026-10-07-live-specialist-control-9bf7f1e.json`. 구형 실행에 원래 protocol acknowledgment를 만들어 주지 않으며 drain 요건을 유지한다. 실제 설치 대상·현장 품질·독립 수락은 pending이고 부모67/82·미완료15/82를 유지한다.

## 추가 실행: 확인 당시 내용에 연결된 복구 보관함

- Clean source483ddd9에서70 related checks와 browser2/Electron2 actual cases passed이다. 미리 확인한 내용·경로·root identity·현재 보호 목록·정책·mtime을 묶어 변경 시 이동 전 actual409로 거절한다. 직접 API의 기존 명시 요청은 호환되며 앱은 반드시 미리 확인한 SHA를 보낸다.
- 빈 입력·잘못된 정책·원본 보호·저장 전 취소·재열기·기존 복원 대상 거절을 확인했다. 통신 오류503은 명시적 test fixture이며 실제 서버 정책과 원본 bytes가 바뀌지 않음을 확인한 뒤 직접 재시도했다. 새 미리 확인 뒤 정확한 payload를 이동·복원했고 기존 archive/fresh restore 회귀도 각각 통과했다.
- 기존4 actions의 미검증21 scenarios를 근거와 연결해192 actions/390 verified/954 pending이다. 실제 source image hashes와 occupied target을 보존했고 영구 삭제나 quality approval을 수행하지 않았다. 부모67/82·미완료15/82·전체 수락0을 유지한다.

## 추가 실행: 템플릿 클래스 원본과 실제 앱 저장·취소·재열기

- Original checkpoint의 클래스와 기존 sidecar의 일치를 읽고 없는 클래스를 actual422로 거절한다. CPU restricted reader만 사용하며 unsafe retry·metadata 작성·학습은 하지 않는다. Clean481ebfb의 관련106 passed이고 최종42edae7의 해당 production bytes와 같다.
- Final42edae7 browser1/Electron1 safety cases와 기존0544e0e browser1/Electron1 cross-project regressions는 각각 정확한 source에 연결했다. 전체·부분 저장, 빈/121자 이름, 명시적 fixture503, 미제출 저장 취소, populated class 취소, 실제 모델/class 매핑, JSON·원본 hash·실제 재열린 node 선택을 확인했다.
- Native 초기 실패는 real HOME 아래의 기존 template store를 읽던 harness 문제다. 기존 파일을 보존하고 명시적 owned store로 바꿨다. 초기 공유-store 실행을 격리 coverage로 세지 않으며, 로딩 중의 옛 재열기 screenshot 대신 실제 선택된 모델 node를 다시 확인했다.
- 기존4 template actions의 미검증23 scenarios를 채워192 actions/413 verified/931 pending이다. 전체 feature 수락0과 부모67/82·미완료15/82는 유지한다. Public receipt:2026-10-07-template-checkpoint-controls-42edae7.json.

## 추가 실행: Mac과42번 격리 LinuxCPU의 규모 측정

- Clean0544e0e의 선택313개 source files를 commit과 독립 byte 대조했다. Mac과 network-none/no-GPU/non-root/read-only Linux container에서 각각1만·10만 metadata의 전 행을 중복 없이 읽고30회 search timing을 측정했다. 각80MP uniform original의 hash를 유지했다. 사진10만장·GUI성능·품질·현장 택트 증거로 쓰지 않는다.
- 10만 행 paging p95는 Mac1.709ms/Linux2.817ms, test-filter search p95는 Mac14.131ms/Linux17.954ms였다. Mac10만 case peak RSS904,249,344 bytes, Linux1,091,051,520 bytes이며 Linux는 별도2CPU/8GiB 제한이다. 정확한 출력 file inventory와 logical/allocated bytes를 기록했고 shared filesystem free delta를 소유 파일 사용량과 구분했다.
- 각 호스트의 두 queue controls에서 원래5000 REVIEW rows·duplicate5000·backpressure100·재조회5000을 확인했다. 실제 모델 inference나72시간 경과는 이 짧은 검사에 포함하지 않는다. Linux owned container는 exit0/removed이며 다른 작업을 중단하지 않았다.
- 같은 independent72h process/observer/caffeinate의 create time을 다시 읽었고 기존 운전을 유지했다. Public receipt:2026-10-07-local-linux-capacity-0544e0e.json. 부모67/82·미완료15/82·수락0을 유지한다.

## 추가 실행: 프로젝트 백업·복원의 오류·취소·재시도

- Clean source5e96e5e의 actual browser1/Electron1에서 원본 폴더 안으로 백업하는 요청의422, 빈 입력 비활성화, 제출 전 취소와 재열기, 실제 archive 생성과 새 project 복원을 확인했다.503은 명시적인 통신 fixture이며 서버 실패로 바꾸어 기록하지 않는다.
- 오류·취소에는 새 archive/restore target이 없고 선택한 project와 원본 hash가 유지됐다. 직접 수정 재시도 뒤 exact archive SHA·복원 marker·모든 source image hash와 실제 재열린 restore record를 대조했다.
- 기존4 actions의 미검증16 scenarios를 채워192 actions/429 verified/915 pending이다. Full feature 수락0과 부모67/82·미완료15/82는 유지하며 Windows·현장 품질·배포자 서명·독립 승인은 포함하지 않는다.

- 저장한 이미지 identity의 실제 확인이 끝나기 전 선택 확정이 켜지는 오류를 production component red1/green4로 재현·수정했다. 새로 직접 고른 유효한 row는 유지되며 뒤늦은 저장 선택 오류가 덮지 않는다. Clean actual browser/Electron에서 응답 지연·503·정확한 필터 재시도·선택 취소·재열기와 모든 원본 hash를 대조했다. 기존 invalid 차원은 임의로 승격하지 않았다.

## 추가 실행: 전체 CPU 선택 replay와 의존성 고지 교정

- Clean1e82c22에서 실제 Linux CI의2737-case 선택을 Mac에서 실행해2706 passed/1 failed/30 skipped이다. 고지 문서에 cryptography가 빠진 원래 실패를 보존했다. 실제 inventory로 cffi·cryptography·pycparser 세 행을 재생성했고 관련62 passed이다. 공개 라이선스 승인으로 대체하지 않는다.
- Clean5e96e5e에서 기존 별도 검증 interpreter로 DICOM26와 actual OpenVINO full-flow1을 실행해27 passed/0 skipped이다. Mac에서 실행할 수 없는 Windows sharing2와 다른 file-system spelling1은 계속 skip이며 native Windows pass로 세지 않는다. Backend/workflow 원본은1e82c22와 같고 새 renderer875 passed 및 두 타입 검사, actual browser2/Electron2 passed이다. 최신 hosted CI는 별도 확인한다.
- 실제 Mac 현재 사용자 context의 유효한 code-signing identity0개를 읽기 전용으로 확인했다. 실 publisher authority나 키를 만들어 signed positive 검사로 바꾸지 않았다. Public receipts:2026-10-07-local-ci-replay-and-repairs-5e96e5e.json,2026-10-07-native-publisher-preflight-1e82c22.json.

## 추가 실행: 실제 원본 변경 거절과 exact 복구

- Clean704b685 browser1/Electron1에서 원본을 별도 보존하고 같은 경로를 다른 PNG로 교체했다. 실제 accepted revision은 같은 UUID의 변경 SHA를 반환했고 선택 경고와 확정 비활성화를 확인했다. 취소 후 기존 선택은 그대로였고 exact 원본 복구·재검증 후 found로 돌아왔다. 별도 replacement도 보존했다.
- 기존 library control7차원과 파생 데이터의 actual 재학습·비교·원본 복귀를 동일 clean704b685 browser2/Electron2에서 재실행했다. 확장된 spec의 새 파일 hash로 원장 근거를 갱신했으며 기존5e96e5e receipts는 역사 기록으로 보존했다. 새 invalid1차원으로430 verified/914 pending이다.
- 이전 hosted6719d5b의 실패 화면·문맥 두 ZIP member만 해시로 확인했다. 전체1.26GB archive·큰 trace는 확인하지 않았다. 해당 옛 source의 후보/기준 뒤집힘은 이미 현재 source에서 수동 선택 scope로 교정돼 있다. 새 actual UI 실행에서 비교·assessment의 원래 모델/후보/checkpoint identity가 같고 human attestation은 미제출이다. 최신 hosted 전체 성공은 별도다.

## 추가 실행: 완료 모델을 재학습하지 않은 OCR asset pack

- 원래 Linux CPU2epoch 완료 checkpoint와 generated 이미지4개의 hash를 다시 읽어 새5-file CTC code/model/MIT asset pack에 연결했다. 실제 명시적 inactive 설치·idempotent 재설치·payload 재검증 후 설치된 두 source modules로 fresh process2회 CPU 추론했다. 출력은 같고 원본/팩 hash는 그대로이며 지원하지 않는 vertical recipe는 거절했다.
- 기존 Torch/NumPy/Pillow/OpenCV base runtime이 필요하며 새 OCR 외부 engine이나 base wheels를 배포하지 않았다. Runtime inventory의 generic execution flag는 false를 유지하고 이 명시적 qualification만 따로 기록했다. Studio 자동 활성화·전체 offline base 설치·대표 OCR 품질·native publisher 서명·독립 수락은 남는다.
- Public receipts는 first-party-ocr-asset-pack-704b685, library-original-followup-704b685, hosted-6719d5b-derived-reread-704b685이다. 부모67/82·미완료15/82·실사용 전체 수락0은 유지한다.

## 검증 공간 보존 및 같은72시간 운전

- 원래 종료된 CPU 검증 tmp16,586,718,534 logical bytes와10월4일 완료 shard tmp14,163,626,352 logical bytes를 실제 외장 TAR에 파일별 hash·hardlink content·member 목록까지 대조해 보존했다. 원래 source freezes/logs/XML/receipts와 모델·사진·live mount는 유지했다. 내부 free14,431,764,480 bytes 및 검증 볼륨 free17,306,185,728 bytes로 회복했다. Logical input 합계와 실제 capacity 증가량을 구분한다.
- 같은 process/create time의 independent72h는 현재 18.51h/1108 cycles 실행 중이다. 재시작·중단·이전 실패 시간 합산을 하지 않았으며 terminal72h는 pending이다. 이 개발 host 정리는 실제 제품의 물리 disk fault 수락으로 사용하지 않는다.

## 추가 실행: 진단 선택·취소·오류·실제 download 인계

- Cleanb976b7d의 actual browser1/Electron1 passed이다. 선택 후 미제출 취소, 빈 section 비활성화, 설치/저장503의 명시적 fixture, pending controls 잠금, 기존 JSON hash 보존, 같은 section 직접 재시도, actual download와 persisted JSON의 내용 일치·redaction, 실제 재열기와 원본 hash를 확인했다. Native download 수는 main session의 will-download event1→2를 읽었다.
- 최초071695f는 실제 앱이 Default Project를 자동 생성함에도 미선택 project라고 가정해 실패했다. 원래 report·화면·teardown을 보존하고 해당 가정을 수정했다. 미실행 empty-project 차원은 승격하지 않았다. 기존3 actions의 pending11 dimensions만 추가해192 actions/441 verified/903 pending이다.
- 이전 hosted15b9043은 CPU2564 passed/고지1 failed/7 skipped로 종료됐다. Browser phase는 실행하지 않았다. 실패 원인은 옛 source의 cryptography 고지 누락이며 현재5e96e5e의 실제 inventory 고지 교정·62개 pass와 exact bytes를 대조했다. 최신 hosted 전체 성공으로 바꾸지 않는다.
- Parent67/82·pending15/82·전체 수락0은 유지한다. Public receipt:diagnostic-control-closure-b976b7d 및 hosted-15b9043-notice-failure.

## 추가 실행: 실제 태그·플래그 충돌과 라벨 복제 보존

- Cleanc1446e4 actual browser1/Electron1 passed이다. 빈 태그/복제 이름·작업자 누락·41자 flag actual422·명시적 빈 flag 제거·중복 정규화·미제출 취소·controlled503 후 직접 재시도·원래 annotation bytes와 새 clone의 동일 내용·재열린 active identity/CSS/JSON을 확인했다.
- 별도 fixture actor가 실제 preference revision을 올린 뒤 옛 화면 저장은 actual409로 거절됐다. 별도 actor의 파일 hash를 유지하고 직접 새로 읽고 재시도해 그 tag를 보존했다. Fixture actor/라벨을 사람의 공정 품질 정답이나 승인으로 세지 않는다.
- 최초39e6220은 정상 UI가 다른 image/default team records를 등록하기 전 metadata hash를 고정해 실패했다. 원래 label JSON은 바뀌지 않았고 등록 record를 확인했다. Fixture의 정상 read를 먼저 수행해 고정했으며 원래 실패와 report/teardown은 보존했다. Product code는 바꾸지 않았다.
- 기존3 actions의 pending12 dimensions를 채워192 actions/453 verified/891 pending이다. Parent67/82·pending15/82·전체 수락0과 Windows 면제를 유지한다. Public receipt:project-display-control-closure-c1446e4.

## 추가 실행: 실제 완료 모델의 Best·Important 표시

- Clean0084be0의 actual browser1/Electron1 passed이다. 모드별 격리 synthetic40 images에서 CPU/pretrained=false 학습2개가 각각2/2epochs 완료됐다. 모델 flag의 빈 선택·41자 actual422·미제출 취소·controlled503·별도 actor의 actual revision409·직접 새로 읽기/재시도·선택 모델의 빈 flag 제거·실제 재열기를 확인했다.
- Best/Important와 두 번째 모델의 표시는 서로 분리됐고 tag/labelset 설정과 actual original checkpoint/meta/job receipts/source hashes가 유지됐다. Flag controls는 추가 학습·deployment write를 제출하지 않았고 active deployment는 null이다. Tiny 실제 학습은 대표 품질 승인이 아니다.
- 누락됐던 F018의 curated action1개/실행한7 dimensions를 추가해193 actions/460 verified/891 pending이다. 새 action을 추가했으므로 기존 미실행 차원을 임의로 줄이지 않는다. Parent67/82·pending15/82·전체 수락0은 유지한다. Public receipt:completed-model-flag-closure-0084be0.

## 추가 실행: 파생 원본 전환·실제 캡처 저장·검수 충돌·수집 정책

- Cleanba52900의 actual browser3/Electron3 passed, retries0, owned teardown이다. 변경된 parent/derived source actual409·정확한 bytes 복구·미제출 취소·controlled503 잠금/재시도·reload source identity와 원래 vector annotations/실제 referenced masks/split/image/review record hashes를 확인했다. 기존F114 pending10차원을 채웠다.
- 캡처는 실제 persisted synthetic service records6건이며 모델 실행 기록을 만들어낸 것이 아니다. 실제 reviewer101자/버전201자422, 별도 fixture actor의 actual409 충돌·직접 refresh/retry, UNKNOWN/not_used/needs_review의 inactive copy를 확인했다. 수집 정책은 빈JSON noPOST·budget0/옛revision actual409·cancel·controlled503·직접revision2/seed81·immutable history[1,2]·actual reopen JSON/hash를 확인했다. 새 정책 아래의 새 캡처 샘플링 실행은 아직 pending이다.
- 큐의 원래 evaluation 복귀·unsaved annotation 보호·strict readiness 거절과 exact saved bbox[10,10,30,30]도 두 모드에서 재실행했다. 기존 whole-suite trace의 raw image 완료 전100% 클릭→완료 Fit250% 때문에 생긴 fixture 실패는 보존하고 실제 decode/Fit를 기다리게 했다. Product 검증 규칙은 완화하지 않았다.
- 새4 curated actions26차원과 기존10차원으로197 actions/496 verified/883 pending이다. 새 등록invalid/정책 downstream은 미실행이며 부모67/82·pending15/82·전체 수락0을 유지한다. Public receipt:source-capture-control-closure-ba52900.

## 추가 실행: 실제 public browser 전체 선택과 수정 replay

- Cleane481810에서 CI와 같은 public selector183 cases를 실제 로컬 Chrome으로 실행했다.181 passed/1 failed/1 skipped/retries0이다. 최초 direct ExFAT attempt의 SQLite readonly 실패와 이후 별도 APFS write/WAL preflight를 보존했다. 실행 중인 기존72h scratch는 바꾸지 않았다.
- 실패1건은 queue fixture의 decode/Fit 시점이다. 수정 후 cleanba52900 actual browser3/Electron3가 통과했고 같은 원래 queue assertions의16차원/32entries를 새 실제receipt에 재연결했다. 기존5743664 receipts와 원래action entries export는 보존했으며 재연결로 새 차원 수를 늘리지 않았다.
- UNet app-flow skip은 통과로 세지 않는다. 기존 전체 선택을 수정 후 전체 성공으로 바꾸지 않으며 최신 hosted CI도 pending이다. Public receipt:public-browser-replay-e481810.

## 추가 실행: UNet 실제 앱 흐름

Clean03358ce의 actual browser1 passed(8.8min, retries0, screenshots13, owned teardown)이다. 생성48장/train40/val8·binary64px masks를 앱에서 등록하고 실제CPU UNet를 중단/예약 반환 후 새job으로12epochs36steps 완료했다. 같은job 평가, ROI[8,8,56,56]·모델 class branch 저장, node 실행 근거, 실제val8장(6NG/2REVIEW), 같은 이력UUID reload, CSV download event 및 별도프로세스 one-imageCPU package parity를 확인했다. CSV bytes 자체는 보존하지 않았다. Evaluation selection_overlap=true로 독립 품질 cohort가 아니며 package 승인을 포함하지 않았다. Step02 screenshot은0/0 loading으로 마스크 라벨 표시나 gap을 결론낼 수 없다. Electron/GPU/target/quality/전체 수락은 pending이다. Public receipt:unet-app-flow-closure-03358ce.

## 추가 구현: macOS 앱 내부 Framework 연결의 업데이트 계약

기존portable schema1의 link 거절은 유지하고, darwin schema2의 한 .app/Contents 안에서만 canonical regular files와 서명된 내부 link 목록을 허용했다. 실제 link expansion 뒤 dotdot을 처리하며 escape/dangling/cycle·file/dir/case 충돌·alias 하위 파일·unsigned/다른 target·설치 후 ordinary-file replacement/empty directory/foreign parent 변경을 거절한다. 모든 regular bytes를 검증한 뒤만 link를 생성하고 launch/recovery에서 원시 target·모든 canonical hashes/modes·DB pair를 다시 검사한다. UI review에 실제 layout/link count를 표시하고 main에서 contract를 검증한다.

Owned qualification-key macOS fixture의 actual install→entrypoint→new profile write→forward recovery와 기존파일 보존, malformed/tampered controls 포함 Python109 pass, main18 pass, product/E2E typechecks pass다. 최초 positive red와 설치 후 ordinary-file replacement 실패를 모두 보존했다. 실제 native publisher/codesign/OS installer/install-home/known-image acceptance를 뜻하지 않으며 S6-04 부모는 pending이다. 다음 실제 GUI와 전체 앱 replay는 별도 실행 기록으로 남긴다.

## 추가 실행: 전체 공개 browser 선택과 macOS 링크 레이아웃

- Cleana53edf6의 전체 공개 browser 선택은185 passed/0 failed/0 flaky/1 explicit skip이며 owned teardown이 깨끗하다. User-authentic pretrained suite는 이 선택에서 제외하고 기존 별도 실행 근거를 보존한다. UNet 화면 흐름은 별도03358ce에서 실제12epoch CPU 완료했다. 전체 native Electron·Windows·대표 품질·실 배포자·장비·독립 수락으로 더하지 않는다.
- macOS schema2는 하나의.app/Contents 안의 canonical 정규 파일과 signed 내부 링크만 허용하고 schema1의 link 거절은 유지한다. 실제 owned signed fixture install/entrypoint/새 쓰기 forward recovery 및 변경된 설치 링크 거절을 확인했다. 같은 pinned trust/compatibility/source fence를 사용하고 실제 publisher 키나 OS installer는 만들지 않았다.
- b505ccd의 실제 Python109/Node18와 source bytes binding, a53의 실제 native GUI positive, b505의 실제 Electron 미준비 거절을 각각 기록한다. 최초 native GUI premature reload 실패 및 전체 선택 옵션 누락의 missing-owned-weights 중단은 원본 그대로 보존했다. 나중에 재현한 sibling-directory traversal cycle은 별도 수정·검증 대상이며 이 과거109개 결과에 포함하지 않는다.
- 기존 portable verified 차원은 동일 GUI case를 재실행한 새 근거로 보완했으며 action/scenario 수는 늘리지 않았다. 부모67/82·pending15/82·전체 수락0을 유지한다.

## 추가 실행: 저장한 정책의 실제 후속 적용·native 순환·채널/취소 경계

- Signed sibling-directory aliases가 a53에서 거절되지 않는 원래 red를 보존했다. Canonical directory와 resolved alias의 방향 그래프를 반복 순회해 전체 cycle을 거절하고 정상 framework alias/원본·새 쓰기 recovery를 유지한다. eec5df1에 연결된 selected bytes와 관련110 passed를 확인했다. 실행은 커밋 전이므로 clean GUI와 구분했다.
- Cleaneec5df1 actual browser2/native Electron1 passed, retry0, owned teardown이다. Malformed/absent service job IDs의 실제409·index 미생성과 split hash 보존을 확인했다. 저장 revision1에서 selected2/skipped2, immutable replay와 index/policy hash 보존, revision2 뒤 old skips 유지·new REVIEW capture만 새 policy_ref·selected3/skipped2, actual reload·UNKNOWN/pending·각 snapshot sourceSHA를 확인했다. 모델 실행이나 새 version/source/training은 없다.
- Native controlled signed layout에서는 미선택 read 비활성화·confirmation 해제·channel 변경 때 review/confirmation 폐기, signed stable release의 beta-target actual refusal·no install, 적용 전 화면 종료·reload 후 새 확인 필요, 실제 native fixture install/entrypoint·raw link 변조 거절·exact 복구·같은1.0.0/fence1 재열기를 확인했다. Real publisher 서명이나 OS installer를 대신하지 않는다.
- 기존 pending10차원을 채워197 actions/506 verified/873 pending이다. 부모67/82·pending15/82·전체 수락0은 유지한다. Public receipt:native-sampling-control-closure-eec5df1. 새 수정본 전체 browser replay는 실행하지 않았으며 직전185-pass 전체 선택은 정확한 a53 source에만 연결했다.

## 실행 중: 판정 근거 이미지 디코딩 오류·취소·재열기

- 원래 decoder failure control의 red5와 StrictMode effect replay/이전 drag가 새 raster에 이어지는 red2를 보존했다. 실제 decoder 완료 전 조작을 잠그고 source/evidence identity에 연결된 오류를 표시하며 늦은 callback을 거절한다. Overlay 실패는 읽을 수 있는 base를 유지한다. 새로운 layer로 바뀌면 zoom/pan과 drag를 초기화한다.
- Component12/renderer-main888 passed, 두 타입 검사 passed이다. 실행은 코드 커밋 전이며 GUI·대표 품질·독립 수락이 아니다. 실제 앱 transport fault fixture와 stored CPU report/원본 hash 보존·취소·재열기는 다음 clean source 검사에 연결한다. Curated197/506/873과 부모67/15·전체 수락0은 아직 유지한다.

- 최초720c1bb decoder GUI는 range 값 표기와 native route.fetch 인증 경로의 fixture 오류로 중단했다. 기존 actual CPU raster/flow 회귀는 browser/Electron 각1 passed이다. 실패·owned teardown을 보존하고 인증된 실제 read의 명시적 fixture 재생으로 교정했다. 인증을 끄거나 token을 꺼내지 않는다.
- 2c27b6e 재실행에서 viewer는 외부 이미지를 거절했지만 뒤쪽 검사 preview가 같은 외부 URL을 요청하는 실제 경계 결함을 browser/Electron에서 재현했다. 저장 판정 preview는 inline raster, 원본 thumbnail은 지정 APIpath로 제한하고 원본 썸네일을 판정 overlay와 구분한다. Full renderer/main890 passed·최신 두 타입 검사 passed이다. 최종 clean GUI까지 scenario 수를 유지한다.

- 925b48c 재검사에서 main preview/thumbnail은 차단됐지만 기존 ROI card가 외부 crop_thumbnail을 요청하는 경로도 재현했다. 실패 trace의 실제 IMG Full image를 확인해 같은 inline-only 계약으로 교정했다. 원래 실패와 actual CPU/flow 두 회귀 pass를 보존하며 최종 GUI까지 scenario는 승격하지 않는다.

## 추가 실행: 실제 판정 근거 오류 복구와 외부 이미지 주소 차단

- Clean7edd21f actual browser1/native Electron1 passed, retry0, owned teardown이다. 인증된 actual CPU 검사1장의 저장 run과 원본 read 응답을 보존한 뒤 명시적 transport fixture로만 깨진 PNG·빈 raster·외부 주소를 재생했다. 원본/overlay decoder 오류·조작 잠금·valid layer 복구, keyboard25–800%/pan30·pointer45/25·Fit reset·ROI/opacity·Escape·실제 재열기의 같은 run/version/imageSHA와 exact source bytes를 확인했다.
- 실제 main preview·row thumbnail·ROI card까지 foreign request0, inspection/training/write0이다. 모든 original file hashes와 직접 인증된 authoritative run/original JSON SHA가 같았다. Root가 native 오류/base 보존/재열린 original screenshot을 읽었다. 기존 CPU raster/flow browser/Electron2 pass는 정확한925b48c에 연결하며 최종ROI source로 재사용하지 않는다.
- 기존 pending14차원만 승격해197 actions/520 verified/859 pending이다. Error 상태에서 바로 닫기 차원은 실행하지 않아 pending 유지한다. 생성 이미지·untrained feature 통계 checkpoint는 대표 품질이 아니며 부모67/15·전체 수락0·Windows 면제를 유지한다. 원래 red/fixture 실패/crop 외부 요청 trace와 owned teardown은 보존했다.

## 추가 실행: 라벨 초안 취소·오류 뒤 명시적 재시도

- Clean9880a31 actual browser1/native Electron1 passed, retry0이다. 빈 이미지의 Delete/undo/redo·zero-area bbox/OBB·드래그 중 Escape·두 점 polygon Enter·세 점 polygon Escape에서 annotation/dirty save/저장 요청이 없었다. 실제 HUD에서 source 좌표를 읽었다.
- 실제 bbox를 저장한 뒤 polygon 저장을 명시적503으로 거절했다. 서버의 기존 baseline JSON SHA는 그대로였고 두 번째 미저장 shape도 유지됐다. 직접 재시도만 새 revision을 썼고 bbox20,20,70,60·polygon120,30/180,30/150,80 및 reload의 exact 두 annotation을 확인했다. 원본 PNG SHA가 같고 모델·학습을 실행하지 않았다.
- 오류로 바뀐 버튼 이름과1:1 resize pan을 잘못 가정한 초기 fixture 실패4개는 원본 report/trace와 함께 보존했다. Production을 바꾸거나 좌표 기대값을 느슨하게 하지 않았다. Root가 actual native 실패/재열기 화면을 읽었고 owned backend/Electron·port 종료를 확인했다.
- 기존 pending9차원만 채워197 actions/529 verified/850 pending이다. 전체 부모67/82·미완료15/82·전체 수락0 및 Windows 면제를 유지한다. Public receipts:2026-10-07-annotation-draft-boundaries-9880a31-browser/electron.json, annotation-draft-closure-9880a31.json.

## 추가 실행: 플로우 결과와 저장 A/B 미리보기 주소 경계

- 실제 component red5로 외부 master/ROI/A-B 주소 표시를 재현하고 snapshot bytes만 허용하도록 교정했다. 기존 dataset thumbnail은 제한된 relative API 경로만 허용한다. 관련33 passed이며 전체 renderer/main896 passed/0skip이다. 처음 full894 pass/2fail은 새 모듈을 읽지 못한 fixture이며 exact helper 연결 뒤 통과했다. 원본 실패 로그는 보존했다.
- Clean d5e0b86 browser2/native2에서 actual CPU flow·두 버전 A/B 및 기존 저장 ROI mask/map·node output 회귀가 통과했다. 별도 clean b8f0d45 browser1/native1에서 snapshot decode와 실제 보이는 A/B screenshot까지 확인했다. 원래 소스별 receipt를 유지하고 중복 GUI 실행 수를 고유 feature 수로 더하지 않는다.
- Captured authenticated 응답에만 외부 주소를 넣었고 실제 page의 foreign request0, original 이미지 SHA·authoritative history JSON·physical comparison JSON SHA 불변이다. Exact stored raster 복구·재열기를 확인했다. 합성 untrained PaDiM fixture는 품질 승인이 아니다.
- 기존 invalid1차원만 채워197 actions/530 verified/849 pending이다. 부모67/82·미완료15/82·전체 수락0을 유지하며 실행 source·fixture 원문·teardown을 보존한다. Public receipt:2026-10-07-flow-raster-origin-closure-b8f0d45.json.

## 추가 실행: exact e481810 hosted CI 완료

- GitHub workflow37609087737은 sourcee481810에서 completed/success이다. 실제 run/job/step API 원문과 해시를 보존했고 source gate·type/build·CPU·core baseline·browser·license inventory·evidence preservation 단계가 모두 success임을 읽었다.
- 이 API 관찰은 테스트 수나 큰 artifact 내부 bytes를 새로 추출하지 않았다. 현재560f44e 및 이후 변경의 전체 CI 성공으로 바꾸지 않는다. 신규 published source gate는 별도이며 부모15건을 완료 처리하지 않았다. Public receipt:2026-10-07-hosted-source-ci-e481810.json.

## 추가 실행: 현재 라벨 편집의 authority·화면 수명 경계

- 늦은 이전 metadata 요청이 새 화면 버튼을 잠금, 이전 복귀가 새 origin을 삭제, 이전 오류가 새 editor에 표시, 화면을 떠났다가 재진입한 뒤 늦은 요청이 다시 라벨을 여는 실제 component 오류4개를 재현했다. 정확한 actor/project/API generation/transport/origin snapshot과 editor 수명을 확인하도록 고쳤다.
- 관련26 passed/전체 renderer-main903 passed/0skip, product/e2e 타입 검사를 통과했다. Clean source6d782fb에서 actual browser/native 각1회가 retry0으로 통과했다. Metadata503·deliberate retry·늦은 응답을 보류한 상태에서 화면 이동/재진입·정확한 origin 보존·늦은 annotation read0을 확인했다. 원래 red 로그와 screenshot/API/teardown을 보존했다.
- 새 실제 reopen action7차원 및 기존 return empty1차원만 채워198 actions/538 verified/848 pending이다. Return error/cancel은 이 metadata reopen 근거로 채우지 않았다. Historical report JSON hash와 원본 SHA, 정확한 report/image/product/Lot 복귀가 유지됐다. 합성 normal 라벨은 사람의 품질 승인이나 training으로 집계하지 않는다.
- Public receipt:2026-10-07-evidence-edit-closure-6d782fb.json. 부모67/82·미완료15/82·전체 수락0을 유지한다.

## 추가 실행: decoder 오류 상태의 근거 창 닫기·정확한 재열기

- Clean source4a4b4d7에서 실제 browser/native 각1회가 retry0으로 통과했다. 잘못된 base raster로 실제 decoder 오류가 남은 상태에서 기존 복귀 버튼으로 닫았고, 같은 저장 플로우/실행/원본 SHA에 묶인 정상 원본을 명시적으로 재열었다. Controls100percent/reset, backend saved report/evidence JSON hash와 원본 files 불변, foreign request0/write0/owned teardown을 확인했다.
- 기존 close/reopen의 pending error1차원만 채워198 actions/539 verified/847 pending이다. 이전 viewer 회귀 전체 assertion도 같은 clean source에서 재실행했다. 원래 receipt는 그대로 보존하고 registry는 신규 exact test bytes에 묶었다. Public receipt:2026-10-07-viewer-error-close-closure-4a4b4d7.json. 부모15건과 대표 품질·장비·배포자·독립 수락은 계속 pending이다.

## 같은72시간 운전의 추가 관찰

- 2026-10-07T13:28:40.123254+00:00 실제 원본 receipt는 running·22.01시간/1317cycles이고 completed/중복 거절/backpressure 거절이 각각1317/1317/1317이다. 원래 worker/observer/caffeinate PID와 create time·시작시각·checkpoint가 일치한다. 재시작·기존 실패 실행 합산 없이 유지했으며72시간 종료는 아직 아니다. Public receipt:2026-10-07-same-soak-progress-4a4b4d7.json.

## 추가 실행: Studio 선택형 팩 검증·프로젝트 보관 연결

- Source5284df6에서 CLI와 frozen backend가 같은 inert installer를 사용하고, 현재 프로젝트 입력만 받은 뒤 별도 SHA admission을 고정 저장한다. 실제 API/Studio에서 원본 hash, 새 payload hash, OS/arch/protocol과 wheel ABI 태그를 재확인한다. 보관과 실행 환경 활성화·publisher 서명·대상 실행은 구분한다. Shared owner+server administrator 외 설치 거절, 다른 프로젝트 입력/linked path/변조/과대 metadata 거절, 설치 receipt 보존 및 비식별 진단을 확인했다.
- 실제 pydicom3.0.2 wheel+원래 LICENSE2 files/2,379,963 bytes로 clean browser2/native3 cases retry0 passed. 정상 보관·잘못된 pin409·실제 파일 변조·network error·재시도·다른 프로젝트 격리·정확한 재열기와 기존 diagnostic download 회귀를 통과했다. Original receipt와 입력 bytes/active dependency inventory는 보존됐다.
- Separate read-only reviewer가 FIFO 교체 blocking, 정상 driver upgrade의 idempotence, 검증 뒤 receipt 재읽기 race를 발견·재현했고, root는 failing production tests 뒤 수정했다. Focused backend83 pass/0 skip, renderer/main907 pass/0 fail/0 skip, product/e2e types pass. 실제 실행·배포 서명 또는 전체 acceptance 승격은 하지 않았다.
- Public receipt:2026-10-07-runtime-pack-studio-closure-5284df6.json. 새 설치 action의7차원 중 실제6만 검증하여199 actions/545 verified/848 pending; native picker cancel은 미검증이다. 전체 parent15가 전부 즉시 불가능하다는 뜻이 아니며, 코드 구현과 외부 실사용 수락 조건을 분리해 추적한다.
