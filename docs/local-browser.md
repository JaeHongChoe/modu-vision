# 이 PC에서 브라우저로 Studio 실행하기

소스를 설치한 PC에서 `npm run web`을 실행하고 `https://localhost:8443`으로
접속합니다. Python 엔진이 이 PC에서 계산하고, 웹 전용 폴더에 계정·프로젝트·작업을
저장합니다. Electron 창은 열리지 않습니다. LAN·외부 호스팅용 실행기가 아닙니다.

## 준비와 실행

[README의 개발 환경 설치](../README.md#빠른-시작)를 먼저 완료하세요.
Node.js 24, Python 3.13, `npm ci`로 설치한 renderer 의존성과 기존 Python requirements가
필요합니다. 실행기는 패키지를 설치하거나 모델을 내려받지 않습니다.
저장소 루트 터미널에서 실행합니다.

macOS에서 기존 가상환경을 선택하는 예:

```bash
export VISION_AI_PYTHON="$PWD/.venv/bin/python"
npm run web
```

`.venv` 또는 `venv`가 저장소에 있으면 자동으로 선택하므로 환경변수는 생략할 수
있습니다. 지정 값은 **Python 실행파일 경로 하나**입니다. 옵션이 붙은 명령문을
넣지 마세요. 가상환경이 없으면 macOS/Linux는 `python3`, Windows는 `python`을 찾습니다.

Linux의 명령 형식은 다음과 같습니다. 이 브라우저 실행기의 Linux 런타임은 아직
검증하지 않았습니다.

```bash
export VISION_AI_PYTHON="$PWD/.venv/bin/python"
npm run web -- --no-open
```

Windows PowerShell의 선택·도움말 명령 형식입니다.
**현재 Windows 서버 실행은 지원하지 않습니다.** 개인 키의 Windows ACL 비공개성을
검사하기 전까지 TLS 단계에서 명시적으로 중단합니다. 이 명령 예는 Windows 실행
검증이나 지원을 뜻하지 않습니다.

```powershell
$env:VISION_AI_PYTHON = "$PWD\.venv\Scripts\python.exe"
npm run web -- --help
```

현재 확인 범위는 macOS의 실행기 단위·로컬 HTTPS 테스트입니다. 실제 화면에서의
업로드·학습·평가, 대상 장치와 OS별 설치 검증은 각각 별도 근거가 필요합니다.

처음에는 터미널에서 관리자 이름, 비밀번호, 비밀번호 확인을 입력합니다.
이름은 영문·숫자·`_ . @ -`로 3~80자, 비밀번호는 12~1024자입니다.
비밀번호 입력은 화면에 표시되지 않습니다. 숨김 입력을 사용할 수 없으면 계정을
만들지 않습니다. 계정은 기존 AccountStore의 암호 해시로 저장됩니다.
이미 계정이 있으면 다시 생성하지 않습니다.

터미널 입력이 없는 첫 실행은 거절됩니다. 같은 데이터 폴더를 지정해 일반 터미널에서
다음 명령을 한 번 실행하고 계정을 설정하세요. 브라우저에는 최초 관리자 생성 기능이
노출되지 않습니다.

```bash
npm run web -- --data-dir "/absolute/path/to/modu-web-data"
```

실행할 때마다 현재 소스의 renderer를 해당 데이터 폴더의 `renderer/`에 빌드합니다.
이 폴더는 실행기 전용이며 다시 빌드할 때 비워집니다. 기존 문서·소스를 넣지 마세요.
동일 데이터 폴더의 잠금을 먼저 확보하므로 중복 실행이 활성 서버의 화면 파일을
다시 만들지 않습니다. 데이터 폴더가 다르면 빌드 결과도 분리됩니다.
빌드 실패 시 서버를 시작하지 않습니다.

## 주소와 옵션

```bash
npm run web -- --port 8444 --no-open
python -m backend.web --data-dir "/absolute/path/to/modu-web-data" --port 0 --no-open
npm run web -- --help
```

두 번째 명령의 `python`은 의존성이 설치된 인터프리터를 사용하세요. 기본 포트는
8443이며 `--port 0`은 OS가 빈 포트를 선택합니다. 실제 주소는 준비 출력으로 확인합니다.
바인딩은 항상 `127.0.0.1`입니다. 브라우저 주소는 정확히 `https://localhost:포트`를
사용하세요. 다른 Host·Origin, HTTP, 위조 전달 헤더로 경계를 우회할 수 없습니다.
`--help`는 데이터 생성·빌드·서버 실행을 하지 않습니다.

실제 HTTPS `/health` 확인과 인증서 검증이 끝나면 다음 형태의 한 줄을 출력한 뒤
기본 브라우저를 엽니다. `--no-open`은 자동 열기만 끕니다.

```text
MODU_WEB_READY={"url":"https://localhost:8443","pid":12345,"data_dir":"...","certificate_file":".../tls/localhost-cert.pem","certificate_sha256":"..."}
```

이 레코드에는 주소·서버 PID·데이터 경로·공개 인증서 위치와 지문만 들어갑니다.
`certificate_sha256`는 **인증서 DER 바이트의 SHA-256 지문**을 소문자 16진수로
표현한 값이며 PEM 파일 자체의 해시가 아닙니다. 쿠키·CSRF·서버 capability는 출력하지
않습니다. 주소 자동 열기가 실패해도 출력 주소로 직접 접속할 수 있습니다.

## HTTPS 인증서 확인

기본 인증서와 개인 키는 데이터 폴더의 `tls/localhost-cert.pem`,
`tls/localhost-key.pem`입니다. localhost SAN이 있는 자체 서명 인증서를 생성하고
다음 실행에서 재사용합니다. OS·브라우저 신뢰 저장소는 변경하지 않습니다.
처음 접속할 때 자체 서명 인증서 경고가 나올 수 있습니다. 주소·인증서 지문을 직접
확인한 뒤 해당 로컬 서버에 대한 브라우저 선택을 진행하세요.
브라우저 전체의 인증서 검사 기능을 끄지 마세요.

macOS/Linux에서 공개 인증서의 지문을 확인하는 예:

```bash
openssl x509 -in "/absolute/path/to/modu-web-data/tls/localhost-cert.pem" -noout -fingerprint -sha256
```

OpenSSL은 대문자와 콜론으로 표시할 수 있습니다. 콜론을 제외하고 대소문자 구분 없이
준비 레코드의 지문과 비교합니다. 브라우저 인증서 상세의 SHA-256 값과도 비교하세요.
비밀번호나 개인 키 내용을 복사·공유할 필요는 없습니다.

이미 보유한 localhost SAN 인증서와 비공개 키를 지정할 수도 있습니다.

```bash
npm run web -- --cert-file "/absolute/path/localhost-cert.pem" --key-file "/absolute/path/localhost-key.pem"
```

두 파일을 함께 지정해야 합니다. 유효기간·localhost DNS SAN·키 일치를 검사합니다.
POSIX에서는 개인 키가 현재 사용자 소유이고 그룹·다른 사용자 접근 권한이 없어야
합니다. macOS에서는 확장 ACL이 있는 키도 보수적으로 거절합니다. 잘못된 기존 파일,
일부만 남은 기본 인증서 쌍, 링크 파일은 자동으로 덮어쓰거나 권한을 바꾸지 않습니다.
경로와 키/부모 폴더의 ACL을 확인한 뒤 두 파일을 명시적으로 복원·교체하세요.
Windows에서는 아직 이 개인 키 보호 검사가 구현되지 않아 실행을 중단합니다.

## 로그인, 저장, 재시작

화면에서 만든 로컬 계정으로 로그인한 뒤 프로젝트를 만들거나 선택합니다.
데이터 관리에서 ZIP을 올려 검증·채택하고 작업 소스로 선택한 뒤 라벨과 분할을
저장할 수 있습니다. 학습·평가는 이 PC의 Python 엔진과 설치된 장치·모델을 사용합니다.
[브라우저 작업 흐름과 권한](extension-contracts.md#browser-studio)을 참고하세요.

새로고침이나 재실행 뒤에는 다시 로그인하고 기존 프로젝트·작업 센터를 엽니다.
같은 데이터 폴더를 사용하면 계정·프로젝트·저장 작업이 유지됩니다.
기본 웹 데이터 경로는 다음과 같습니다.

| OS | 기본 데이터 폴더 | 실행기 지원 상태 |
| --- | --- | --- |
| macOS | `~/Library/Application Support/Modu Vision Web` | 로컬 실행기 테스트 확인 |
| Linux | `$XDG_DATA_HOME/modu-vision-web`, 미설정 시 `~/.local/share/modu-vision-web` | 런타임 미검증 |
| Windows | `%LOCALAPPDATA%\Modu Vision Web` | TLS 키 ACL 검사 미지원으로 실행 불가 |

Electron의 데이터나 저장소 `projects/`를 자동으로 가져오지 않습니다.
`--data-dir`은 웹 전용 새 폴더를 지정하세요. 계정 DB만 기존 데스크톱 프로젝트에
복사해 연결하지 마세요.

종료는 실행한 터미널에서 **Ctrl+C**를 누릅니다. 열린 HTTP 스트림은 최대 5초의 요청
정리 대기 후 닫고 기존 backend lifespan을 실행합니다. 학습 작업의 종료·분리는 기존
엔진의 detach·작업 원장·복구 정책을 따릅니다. 서버 종료가 학습 강제 종료를 뜻하지
않습니다. 재실행 시 같은 폴더를 열어 작업 상태를 확인하세요.
데이터 폴더 잠금은 서버 인스턴스의 소유를 나타내며 detached worker나 물리 GPU의
독점 사용을 보장하지 않습니다. 데스크톱과 웹은 저장 공간이 달라도 물리 GPU를
공유하므로 동시에 학습할 때 자원 사용을 확인하세요.

일반 브라우저에서는 파일 선택·다운로드를 사용합니다. 네이티브 폴더 선택, 로컬
파일/폴더 열기, 데스크톱 업데이트·OS 서비스 등록은 지원하지 않습니다.
서버 경로 입력은 브라우저가 임의 PC 폴더에 접근할 권한을 주는 기능이 아닙니다.

## 실행 오류

- **포트 사용 중:** 해당 소유 프로세스를 확인하거나 `--port 8444`처럼 포트를 바꿉니다.
  실행기는 다른 프로세스를 종료하지 않습니다.
- **같은 데이터 폴더 실행 중:** 먼저 실행한 서버 터미널을 확인합니다. 잠금 파일을
  지우거나 PID만 보고 프로세스를 종료하지 마세요. 정상 종료·프로세스 종료 뒤에는
  OS 잠금이 해제되므로 같은 폴더로 다시 실행할 수 있습니다.
- **첫 계정 입력 불가:** 일반 터미널에서 같은 `--data-dir`로 실행해 계정을 설정합니다.
- **renderer 빌드 실패:** 기존 `npm ci` 설치와 오류 출력을 확인합니다. 실행 중 자동
  설치는 하지 않습니다. 링크가 있는 출력 폴더는 빌드 전에 거절합니다.
- **Python 실행 불가:** 기존 의존성이 설치된 Python의 전체 경로를 `VISION_AI_PYTHON`에
  지정합니다. 이 실행기는 의존성을 추가하지 않습니다.
- **인증서 오류:** 오류의 경로·유효기간·SAN·키·권한/ACL을 확인합니다. 기존 파일을
  보존한 상태에서 사용자가 명시적으로 쌍을 복원·교체해야 합니다.
- **준비 확인 실패:** 브라우저를 열지 않고 자신이 시작한 서버만 정리합니다. 출력된
  원인을 확인한 뒤 같은 명령을 다시 실행하세요.
