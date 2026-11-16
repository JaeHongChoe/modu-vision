# 모두비전 전체 학습 플랫폼 기반: Geti

이 폴더는 Geti **전체 application과 getitune 학습 library**를 고정 소스로 보존한다. 라벨 도구 일부나 UI 예제만 가져온 구조가 아니다. 기반 버전은 `app/v3.3.0`, commit `ddcf394d550bf1b84ca262888260043af361f5e7`이다.

```sh
git submodule update --init -- platforms/geti/upstream
node scripts/geti-platform.cjs check --json
```

첫 명령은 사용자가 선택해 전체 소스를 받는 단계이며, 두 번째 명령은 로컬 Git·manifest만 읽는다. `check`는 설치·서버·학습·외부 요청·upstream script를 실행하지 않는다. 다른 소유 시험 checkout을 검사할 때는 같은 작업트리 안의 절대 경로를 `--source /absolute/path`로 지정한다.

검사는 repository origin, 정확 commit/tree, tracked clean, source count, application/backend·UI·library와 전주기 source owner, core LICENSE SHA256, 선언된 toolchain을 확인한다. `source_verified`는 **소스 확인만** 뜻한다. 출력의 `runtimeVerified`와 `productIntegrationVerified`는 이 검사가 통과해도 `false`다. ignored/untracked 실행 산출물은 tracked source 변경과 구분하며, 실제 실행에는 별도 소유·무결성 검증이 필요하다.

Geti UI는 React 19.1.1·Node >=24.2.0·npm >=11.14.0, backend는 Python >=3.14,<3.15를 선언한다. 기존 모두비전 React 18/Python 3.13 환경을 덮어쓰지 않는다. 공식 설치 대상과 현재 Mac ARM 가능성 검증은 구분한다. `application`의 현재 task는 classification·detection·instance segmentation이며 기존 모두비전 10모델군 전체의 대체를 뜻하지 않는다.

초기 단계에서는 설치 앱·8443 웹·사용자 데이터·GPU·계정 설정을 바꾸지 않는다. 전체 runtime의 데이터→라벨→학습→평가→내보내기 시험, 모두비전 인증/프로젝트 연결, 기존 기능과 한국어 UI 통합, 실제 패키징은 별도 완료 단계다. [설계](../../docs/superpowers/specs/2026-11-16-full-training-platform-design.md)와 [실행 계획](../../docs/superpowers/plans/2026-11-16-full-training-platform.md)을 따른다.

의존성·모델은 core license와 별도로 확인한다. 특히 upstream CPU extra는 optional Ultralytics를 포함하므로 무조건 설치하지 않는다. [고지](UPSTREAM-NOTICES.md)를 확인한다.
