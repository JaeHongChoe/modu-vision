# 실제 앱 복구·진단과 DICOM 캐시 무결성

소프트웨어 구현 집계는66/82, 미완료16이며 Windows 실사용 QA1개 면제로 진행 대상15개다. 최종 수용0개와 모든 원래 조건을 유지한다.

## 실제 앱 실행

깨끗한 `0f3be05ab94628359dab75aca99ce33fee6af67e`에서 브라우저3건과 macOS Electron3건, 총6건이1회 실행/재시도·skip 없이 통과했다.12개 실제 스크린샷과 exact-case receipt를 보존한다.

- 이미지 라이브러리:248개 입력에서 첫 페이지 밖의 항목을 태그·제품·Lot·오류로 찾고, 빈 검색 결과와 손상된 이미지/라벨 오류를 구분했다. UUID와 원본 해시에 따른 선택은 reload 후 유지되고 학습 요청은 보내지 않았다.
- 프로젝트 복구:정책 저장, 보호 모델 이동 거부, 보고서의 복구 보관함 이동·원래 바이트 복원, 검증된 archive와 아직 실행하지 않은 restore의 상태 분리, 빈 새 폴더로의 복원, 원본·라벨·검수 audit·SQLite·설정·보호 기록 보존을 확인했다. 이미 존재하는 target은 거부됐다. 모델 파일은 fixture이며 추론하지 않았다.
- 지원 자료:직접 선택한1개 section만 저장했다. 실제 다운로드·HTTP response·프로젝트 영속 JSON이 같고, controlled 오류에 넣은 민감값·연락처·원본 이미지·원본 해시는 빠졌다. 빈 선택에서는 저장이 비활성화됐다. 외부 업로드하지 않았다.

U025와 U030의 명시적인11개 action/23개 scenario 근거를 추가했다. 전체 원장은30개 action/55개 verified scenario/155개 pending scenario이며150개 기능의 action 목록은 여전히 미검토다. 선언된 버튼 수는 실행 수용으로 세지 않는다. picker·cancel·shortcut 및 나머지 필수 시나리오는 pending이다.

## DICOM 구현 변경

제품 소스 `4cf1912634bbdf4f8f2dd791487846c675d72750`:

- 정상 원본에서 만들 예상 PNG와 저장 cache를 byte/hash로 대조한다. 손상된 PNG나 원본을 재결합한 receipt는 보존하고 거부한다.
- encoder가 끝나기 전에는 최종 파일을 만들지 않는다. 완전한 PNG의 atomic publication과 private receipt를 별도로 기록하며, receipt 중단 후 동일 PNG만 재사용한다.
- 표시 GET도 현재 프로젝트의 원본·receipt·실제 PNG를 재검증하고 확인한 bytes를 응답한다. 변경된 원본, 바뀐 cache, rebound receipt는422이며 browser cache를 사용하지 않는다.
- 자동 min/max 표시와 명시적 DICOM window를 구별하는 `windowing_mode`를 view identity에 포함한다. 예전 cache는 직접 다시 표시 영상을 생성해야 한다. 원본·라벨·학습 설정은 변경하지 않는다.

8개의 통제된 cache/publication/HTTP 문제가 먼저 실패했고 자동 min/max 재현 문제가 추가로 실패했다. 수정 후 관련 데이터/라벨/근거 검사171개가 통과했다. actual pydicom3.0.2의 built-in RLE mono/RGB/2-frame3개는 압축 전후 선택 frame의 픽셀 및 저장 영상이 일치했다. macOS arm64 CPU와 별도 qualification environment의 합성 입력 범위다. JPEG/JPEG-LS/JPEG2000/다른 provider·장치·clinical 품질·프레임별 annotation이나 학습은 수용하지 않는다.

후속 GUI 실행은 통과했으나 실행 중 근거 문서 작성으로5개 case가 dirty source로 기록되었다. 엄격한 collector가 이를 clean receipt로 수용하지 않았다. 진단 로그는 보존하고 현재 커밋을 고정한 전체 clean GUI 재실행을 진행한다. 이 진단 pass로 native/feature acceptance를 올리지 않는다.

## 계속 남은 조건

72시간 실행은 고정된 별도 소스로 계속 진행한다. 현재 소스 공개 CI, 살아 있는 worker/lease migration, app+DB 설치 cutover, 실제 서명/publisher, 운영 비밀 정책,10개 모델군 사람이 검토한 truth, 장비·공정 품질과 첫 사용자 파일럿은 별도 미완료 조건이다. 현재 앱3가지 경로 통과는 전체156기능 완료나 공개 릴리스 승인이 아니다.
