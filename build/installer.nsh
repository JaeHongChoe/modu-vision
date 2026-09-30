; build/installer.nsh
; Vision AI Studio - Custom NSIS Windows Installer Script
; Automatically detects and installs prerequisite system runtimes (Visual C++ 2015-2022 x64)

!include "LogicLib.nsh"

!macro customInstall
  DetailPrint "Checking Visual C++ 2015-2022 Redistributable (x64)..."
  
  ; Check x64 registry entry for VC++ 2015-2022 (v14.x)
  SetRegView 64
  ClearErrors
  ReadRegDWORD $0 HKLM "SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64" "Installed"
  
  ${If} ${Errors}
    StrCpy $0 0
  ${EndIf}

  ${If} $0 != 1
    DetailPrint "Visual C++ Redistributable is not found. Checking bundled runtime..."
    ${If} ${FileExists} "$INSTDIR\resources\vc_redist.x64.exe"
      DetailPrint "Installing Visual C++ Redistributable (Silent)..."
      ExecWait '"$INSTDIR\resources\vc_redist.x64.exe" /quiet /norestart' $1
      DetailPrint "Visual C++ installation finished with exit code $1."
    ${Else}
      DetailPrint "Bundled vc_redist.x64.exe not present; skipping local runtime install."
    ${EndIf}
  ${Else}
    DetailPrint "Visual C++ Redistributable (x64) is already installed."
  ${EndIf}
!macroend
