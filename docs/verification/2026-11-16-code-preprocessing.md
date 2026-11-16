# 코드 전처리 개발 검증

분류·영역 분할의 saved input recipe에 `modu-numeric-v1` code operation을
추가했다. 원본을 변경하지 않는 수치 AST 해석기와 기존 도우미의
`configure_input_recipe` 실행 경로를 사용한다. 자유 Python 실행·운영체제
샌드박스·모든 모델군 지원·초음파 현장 품질을 의미하지 않는다.

## 검사 결과

- 초기 코드 구현의 macOS 기본 Node에서 renderer/main 3,474 PASS, 기존
  opt-in 2 SKIP, 실패 0.
- numeric/input/export 및 CI 선택·보존 계약 141 PASS. 기존 multipart
  deprecation 경고 1개 유지.
- renderer/main 타입·E2E 타입·build 통과. 기존 Vite 500kB chunk 경고 유지.
- 실제 renderer/로컬 backend 합성 E2E 2/2 PASS. 새 코드 경로와 기존 데이터
  준비 경로를 함께 확인했다. 각 fixture의 원본 64장 SHA가 유지됐다.
- 실제 FFT 미리보기·해시 저장, 편집 후 저장 자격 소멸, import 코드 422,
  이전 저장 코드 보존, 전체 규칙/선택 위치의 도우미 작성창 전달을 확인했다.
  자동 전송·실제 provider/모델 호출·외부 요청·page error는 0이었다.
- 넓은 코드 편집기, 숨긴 코드 블록의 펼침 상태와 다른 블록 레이아웃 분리,
  720px overflow 부재를 실제 화면과 geometry assertion으로 확인했다.
- 합성 주파수의 FFT 감소, DC·낮은 주파수 신호·채널 분리, 원본·마스크
  불변, 코드 전 입력 1MP 경계, 금지 문법·자원 제한과 checkpoint mismatch를
  검사했다. 복사된 두 runtime만 사용하는 독립 `python -I` 프로세스의 실제
  FFT 결과 픽셀 SHA가 backend와 같았다.
- 기존 코드 없는 모델 binding과 export 모듈 구성을 유지했다. 새 테스트 2개를
  full CPU lane에 추가하고, 기존 workflow/명령 해시를 바꾸지 않는 엄격한
  추가 범위 검사와 fast 선택·변형 거절 검사를 통과했다.
- 독립 소스 검토: 큰 정수 상수의 OverflowError를 재현하여 ValueError로
  수정했다. 이후 backend/domain/resource/export, scoped composer handoff,
  추가 CI 범위 검토에서 미해결 P0–P2를 찾지 못했다.

## 기록과 한계

private run `2026-11-16T06-13-38-779Z-61134`의 원본 manifest와 screenshot,
backend/renderer/type/build 로그를 `.superpowers/sdd/2026-11-16-code-preprocessing`
및 `.superpowers/sdd/2026-11-16-rea-supervisely`에 보존했다. 초기 신규 E2E는
중첩 summary를 두 개 찾은 test locator에서 멈췄고, 직접 자식 summary를
선택하도록 고친 뒤 통과했다. 이 실패를 제품의 FFT 실패로 분류하지 않는다.

숫자 AST의 크기·호출·배열 예산을 제한하지만 native NumPy 호출은 in-process다.
5초 elapsed 검사는 협력적 확인이며 강제 중단 기한이 아니다. FFT 예시의
주파수는 시작 값이며 실제 초음파의 패턴 주파수·결함 보존·평가 성능을
확인하지 않았다. 도우미는 실제 계정 호출 대신 tool/schema/실행기 회귀로
연결을 검사했다. 이전 모델·원본·사용자 계정·TLS·실행 중 앱과 웹은 보존했다.

로컬 검사 결과와 원격 exact-head CI, main 병합, 앱·웹 배포는 별개다.
이 문서만으로 원격 CI나 배포 완료를 주장하지 않는다.

## 원격 CI 후속 수정

`b8d5044e`의 fast `43815899580`은 11 job 중 8 SUCCESS, 3 FAILURE였다.
브라우저 4개 shard는 통과했다. static은 CI의 Node 24에 없는 전역
`localStorage`에 의존하던 네 개 단위 테스트에서 실패했다. 테스트 fixture에
독립 메모리 저장소를 주입하고 실제 handoff 삭제·격리를 검사했다. 전역
저장소를 교체하거나 제품의 실행 가드를 바꾸지 않았다. Node 24.22.1에서
동일 실패를 먼저 재현한 뒤 전체 renderer/main **3,475 PASS / 기존 2 SKIP /
실패 0**을 확인했다.

CPU shard 4/5는 새 전처리 파일을 과거 소스 집합과 비교해 안전하게 거절한
검사였다. 과거 여섯 집합의 profile·파일 해시는 유지하고, 새 수치 해석기를
포함한 별도 아홉 파일 집합을 추가했다. 파일 누락·변조·심볼릭 링크·검사
도중 변경·미등록 observer의 거절 조건을 유지했다. 관련 모델 없는 소스
계약 110개와 resident-resource 10개가 통과했다. 이는 실제 GPU 실행이나
서버 운영 수락이 아니다. 원본 CI 로그, 실패한 checkout merge-ref와 run의
head, RED/GREEN 기록을 private `ci-b8-failure`에 따로 보존했다.

## 전체 브라우저 회귀와 설정 화면 복구

`9ee6ab5d`의 전체 회귀 [43819019988](https://github.com/JaeHongChoe/modu-vision/actions/runs/43819019988)는
CPU job SUCCESS, browser job FAILURE였다. 브라우저 결과는 69 FAIL / 307 PASS /
2 SKIP였다. 새 블록 화면에서 기존 모델 버튼·설정 위치를 찾던 시험 동선이
맞지 않았고, 도우미의 명시적 설정 열기도 접힌 details만 열어 숨겨진 React
탭을 선택하지 못하는 실제 연결 누락이 있었다. 이 실패를 삭제하거나 CPU
성공으로 전체 회귀 성공을 대신하지 않는다.

설정 열기는 현재 화면 소유자와 등록 revision, 프로젝트·원본·라벨셋·API·자료
권한 범위를 확인하고 해당 블록을 선택한다. 접힌 설정과 초점은 같은 요청이
유효할 때만 갱신한다. 소유자 교체·동일 객체의 재등록·화면 해제·수동 탭
이동 뒤에는 이전 요청이 초점을 가져오지 않는다. 취소된 이동은 학습 설정을
쓰기 전에 거절한다. 학습·분할·실제 provider 호출을 추가하지 않았다.

시험은 실제 모델 탭과 radio 카드, 현재 작업대의 설정을 선택하도록 갱신했다.
기존 10초 범위, 원본 해시·저장 영수증·작업 변경 거절·취소·재연결·Epoch·
예산·무제출 단언은 유지했다. 접힌 모델 선택 목록도 펼쳐 10개 카드와 검색창을
접근성 검사에 포함했다.

- Node 24.22.1 집중 회귀 218 PASS. 전체 renderer/main 3,488 PASS /
  기존 opt-in 2 SKIP / 실패 0. 제품·E2E 타입 검사 통과.
- 수정된 작업 소스의 영향 범위 90개 브라우저 사례를 누적 확인했다. 초기
  부분 실행의 실패와 중단은 보존했고, 변경하지 않은 통과 사례는 반복하지
  않았다. 마지막 유효 결과 90개가 모두 PASS이며 단일 90개 실행의 성공을
  주장하지 않는다. 각 실행의 renderer bundle SHA는 동일했다.
- 합성 자료의 실제 CPU 모델 시험과 도우미 모의 연결을 구분했다. 실제
  사용자 학습·계정 provider 요청·현장 품질 수락은 수행하지 않았다. 각 실행의
  owned 프로세스·포트 정리와 남은 참조 부재를 확인했다.
- 과거 GUI receipt 315개와 원본 action 기록은 보존했다. 229개 소스 이력
  선언은 과거 시험 바이트와 현재 시험을 구분하며, 보존된 Git blob을 실행
  시점으로 바꾸어 주장하지 않는다. 기존 검증기 두 개가 통과했고
  `accepted_features`, `accepted_work_packages`, `accepted_legacy_rows`는 0이다.

원본 실패 로그와 RED/GREEN·누적 선택·정리 증거는 private
`.superpowers/sdd/2026-11-16-training-code-release`에 보존했다. 이 후속 로컬
결과도 새 최종 커밋의 원격 fast/full, 병합 및 설치 완료와 구분한다.
