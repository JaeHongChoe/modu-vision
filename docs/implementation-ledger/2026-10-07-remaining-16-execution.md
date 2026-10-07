# 남은 16개 대상의 실행 기록

2026-10-07. 기존 16개를 모두 추적하며 실행 가능한 구현과 검증을 진행한다. 현재 부모 구현 검증은 **67/82**, 미완료 구현 표시는 **15/82**, 전체 실사용 수락은 **0/82**이다. S5-10만 이번에 구현 검증으로 승격했다. 개별 검사·slice·부모 수는 서로 더하지 않는다.

| ID | 작업 | 실제 완료한 부분 | 남은 조건 |
|---|---|---|---|
| S1-08 | 이력·전역 마이그레이션 | 종료된 학습·라벨·epoch·6종 specialist 및 7종 원격 작업 원본 보존; 관련163개와 새 Linux 검사 통과 | 실행 중 worker/불확실 lease의 소유권·fence 이관, 구형 프로토콜 및 독립 flow report 어댑터 |
| S5-01 | 독립 검사 서비스 | 독립 프로세스·CPU 검사·owned start/stop/restart 및 Linux 격리 실행 | 실제 전용 계정·재부팅·장비 권한·GPU/camera 서비스 운용; Windows 실기 QA는 면제 |
| S5-10 | 서비스 보안 | 구현 검증 완료: HTTPS·인증·세션·권한·경로·외부 secret 저장소·원자적 이전·실제 앱 readback | 운영 인증서·계정 배치와 실제 target/독립 최종 승인 |
| S6-02 | Windows 설치 | 설치 구성·unsigned packaged CPU/restart 검증 기록 보존 | Windows11 설치·제거 실기 QA는 사용자 면제이며 통과로 집계하지 않음 |
| S6-03 | 선택 runtime pack | 실제 OpenVINO CPU/provider·정밀도 control 실행; 기본 backend와 구분 | 별도 pack 배포와 실제 지원 장치/대표 정답 cohort 검증 |
| S6-04 | 오프라인 업데이트 | owned POSIX portable 앱·DB cutover, crash finish, 새 account/profile 보존 forward recovery; actual frozen CLI5회·재실행 및 별도 CPU known-image2회 | 실제 OS installer·설치된 home·Electron UI·native layout·publisher 서명 및 installed-app known-image handoff |
| S6-05 | 공개 CI | 이전 정확한 source의 complete hosted success; heartbeat 검사 수정·Mac58/Linux119 통과 | 최신 게시 source 전체 CI 완료; 기존 queue 및 실패 기록 보존 |
| S6-06 | 서명·채널 | unsigned 상태와 checksum/source 연결 | 실제 publisher/서명 키·최종 artifact와 stable/beta 운영 정책 |
| S7-01 | 전체 기능 행동 coverage | 168개 curated action, 304개 시나리오 검증; 기존 credential 2개 control 추가 | 872개 curated 시나리오와 누락 메뉴/shortcut; 기존156개 feature 전체 수락은0 |
| S7-02 | 10개 모델군 시나리오 | 실제 family별 학습/추론·다중 checkpoint·flow/package control 기록 | 사람이 검토한 대표 분류/정상/OCR/OBB/향상/GAN truth와 품질 판정 |
| S7-03 | Windows 실사용 | 기존 unsigned runner 결과 보존 | 사용자 면제. 신규 실기 테스트를 시작하지 않으며 pass로 바꾸지 않음 |
| S7-04 | 동시 작업·장애 | 2개 계정/agent·actual CPU·재접속·권한/충돌 control; controlled signed portable 앱·DB cutover 및 crash recovery | 물리적 장애와 실제 publisher가 서명한 native 앱 업데이트 |
| S7-05 | 규모·72시간 운전 | 별도 연속 운전 진행 중: 11.49시간/688 cycles; 기존 실패와 합산하지 않음 | 72시간 종료 receipt 및 실제 target RAM/disk/p95/tact 측정 |
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
