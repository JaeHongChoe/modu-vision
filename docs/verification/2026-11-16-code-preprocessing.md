# 코드 전처리 개발 검증

분류·영역 분할의 saved input recipe에 `modu-numeric-v1` code operation을
추가했다. 원본을 변경하지 않는 수치 AST 해석기와 기존 도우미의
`configure_input_recipe` 실행 경로를 사용한다. 자유 Python 실행·운영체제
샌드박스·모든 모델군 지원·초음파 현장 품질을 의미하지 않는다.

## 검사 결과

- renderer/main 3,474 PASS, 기존 opt-in 2 SKIP, 실패 0.
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
