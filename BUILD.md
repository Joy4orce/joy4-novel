# Joy4_Novel 빌드 · 배포 가이드

## 1. 독립 실행 EXE 만들기

개발 PC(현재 폴더)에서:

```
build.bat
```

더블클릭만 하면 됩니다. 내부적으로:
1. `pip install -r requirements.txt` + `pyinstaller` 설치
2. `Joy4_Novel.spec` 로 PyInstaller 실행
3. `dist\Joy4_Novel\Joy4_Novel.exe` 및 의존 파일 일체가 생성

빌드 시간: 1~3분. 결과 폴더 크기: 대략 40~80MB.

### 간이 배포 (압축 배포)

`dist\Joy4_Novel` 폴더 **전체**를 압축해서 대상 PC에 풀고
`Joy4_Novel.exe` 를 더블클릭하면 끝. Python 설치 **불필요**.

## 2. 정식 설치 파일(Setup.exe) 만들기

1. Inno Setup Compiler 설치: https://jrsoftware.org/isdl.php
2. `build.bat` 로 `dist\Joy4_Novel\` 생성 (반드시 먼저!)
3. `installer.iss` 를 Inno Setup 으로 열고 `Build > Compile` (F9)
4. `output\Joy4_Novel_Setup_1.0.0.exe` 가 생성 — 이걸 배포

설치 후 동작:
- 기본 설치 경로: `C:\Program Files\Joy4_Novel`
- 시작 메뉴 · (선택) 바탕화면 바로가기 생성
- 사용자 데이터(설정/로그): `%APPDATA%\Joy4_Novel`
- 제어판에서 깔끔하게 제거 가능

## 3. 데이터 저장 위치

| 실행 방식          | 위치                              |
|------------------|----------------------------------|
| `python main.py` | 프로젝트 폴더 (포터블)                |
| 빌드한 EXE        | `%APPDATA%\Joy4_Novel`           |

API 키, 프롬프트/사전, 로그 모두 이 폴더에 저장됩니다.

## 4. 아이콘 넣기 (선택)

1. 256×256 `.ico` 파일을 프로젝트 폴더에 `icon.ico` 로 저장
2. `Joy4_Novel.spec` 의 `icon=None` 을 `icon="icon.ico"` 로 변경
3. `build.bat` 재실행

## 5. GitHub Releases 자동 배포

빌드 환경이 없어도 태그만 푸시하면 GitHub Actions가 Windows에서 빌드해
포터블 zip + 인스톨러 exe를 Release에 첨부합니다.

### 새 버전 릴리스
```bash
git tag v1.0.0
git push origin v1.0.0
```
→ Actions 탭에서 진행상황 확인 → 완료 시 Releases 페이지에 두 파일 게시:
- `Joy4_Novel_1.0.0_portable.zip` — 압축 풀고 `Joy4_Novel.exe` 실행
- `Joy4_Novel_Setup_1.0.0.exe` — 정식 인스톨러

### 빌드만 테스트 (릴리스 안 만듦)
Actions 탭 → "Release" 워크플로우 → "Run workflow" → 임의 버전 입력.
완료 후 Artifacts에서 결과물 다운로드 가능.

### 사용자 다운로드 안내
배포 시 사용자에게 다음 URL 안내:
```
https://github.com/Joy4orce/joy4-novel/releases/latest
```

## 6. 트러블슈팅

- **"Failed to execute script"**: `dist\Joy4_Novel\Joy4_Novel.exe` 를 콘솔에서 실행해 에러 확인.
  임시 콘솔 로그가 필요하면 `Joy4_Novel.spec` 의 `console=False` → `True` 로 변경 후 재빌드.
- **drag-and-drop 이 안 됨**: tkinterdnd2 번들 실패. `build.bat` 재실행, 실패 시
  `pip install --force-reinstall tkinterdnd2` 후 재빌드.
- **백신이 exe 를 차단**: PyInstaller 의 공통 이슈. 코드 서명을 하거나
  사용자에게 예외 처리 안내.
