; ============================================================
;  Joy4_Novel Installer script (Inno Setup)
;  사용법:
;    1) build.bat 으로 dist\Joy4_Novel\Joy4_Novel.exe 생성
;    2) Inno Setup Compiler 를 설치 (https://jrsoftware.org/isdl.php)
;    3) 이 파일을 Inno Setup Compiler 로 열고 [Build > Compile]
;    4) output\Joy4_Novel_Setup.exe 생성됨 — 이걸 배포
; ============================================================

#define MyAppName     "Joy4_Novel"
#define MyAppVersion  "1.0.0"
#define MyAppExeName  "Joy4_Novel.exe"
#define MyAppPublisher "Joy4_Novel"

[Setup]
AppId={{B7C9D3F1-5E4A-4D8B-A2E0-7F1D9C2B4E6A}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
OutputDir=output
OutputBaseFilename=Joy4_Novel_Setup_{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
ArchitecturesInstallIn64BitMode=x64
UninstallDisplayIcon={app}\{#MyAppExeName}
; 한국어 + 영어 라이선스/인터페이스
ShowLanguageDialog=auto

[Languages]
Name: "korean";  MessagesFile: "compiler:Languages\Korean.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "바탕화면에 바로가기 만들기"; GroupDescription: "추가 아이콘:"; Flags: unchecked

[Files]
; dist\Joy4_Novel 하위의 모든 파일/폴더를 설치 폴더로 복사
Source: "dist\Joy4_Novel\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}";       Filename: "{app}\{#MyAppExeName}"
Name: "{group}\{#MyAppName} 제거";   Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{#MyAppName} 실행"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; 사용자 데이터(%APPDATA%\Joy4_Novel)는 남겨둠.
; 완전 삭제를 원하면 아래 줄의 주석을 풀 것:
; Type: filesandordirs; Name: "{userappdata}\Joy4_Novel"
