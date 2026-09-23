!include "MUI2.nsh"

Name "Hugmunn"
OutFile "${OUTPUT}"
InstallDir "$LOCALAPPDATA\Programs\Hugmunn"
InstallDirRegKey HKCU "Software\Hugmunn" "InstallDir"
RequestExecutionLevel user
Unicode true
SetCompressor /SOLID lzma
!define MUI_ICON "${ICON}"
!define MUI_UNICON "${ICON}"
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "English"

Section "Hugmunn"
  SetShellVarContext current
  SetOutPath "$INSTDIR"
  File /r "${SOURCE}\*"
  WriteUninstaller "$INSTDIR\Uninstall.exe"
  CreateShortcut "$SMPROGRAMS\Hugmunn.lnk" "$INSTDIR\Hugmunn.exe"
  CreateShortcut "$DESKTOP\Hugmunn.lnk" "$INSTDIR\Hugmunn.exe"
  WriteRegStr HKCU "Software\Hugmunn" "InstallDir" "$INSTDIR"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Hugmunn" "DisplayName" "Hugmunn"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Hugmunn" "DisplayVersion" "${VERSION}"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Hugmunn" "DisplayIcon" "$INSTDIR\Hugmunn.exe"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Hugmunn" "UninstallString" '$"$INSTDIR\Uninstall.exe$"'
  WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Hugmunn" "NoModify" 1
  WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Hugmunn" "NoRepair" 1
SectionEnd

Section "Uninstall"
  SetShellVarContext current
  Delete "$SMPROGRAMS\Hugmunn.lnk"
  Delete "$DESKTOP\Hugmunn.lnk"
  Delete "$INSTDIR\Hugmunn.exe"
  RMDir /r "$INSTDIR\_internal"
  Delete "$INSTDIR\Uninstall.exe"
  RMDir "$INSTDIR"
  DeleteRegKey HKCU "Software\Hugmunn"
  DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\Hugmunn"
SectionEnd
