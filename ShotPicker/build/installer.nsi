; Shot Picker 윈도우 설치 프로그램 (NSIS)
; makensis -DVERSION=1.0.0 -DSRC=<payload 폴더> -DOUT=<출력 exe> installer.nsi
; 공식 윈도우용 NSIS 에는 64비트 stub 이 없어서 기본(32비트 유니코드)으로 빌드. 64비트 stub 이 있는 환경이면 -DAMD64
!ifdef AMD64
  Target amd64-unicode
!else
  Unicode true
!endif
!include "MUI2.nsh"
!include "FileFunc.nsh"

!ifndef VERSION
  !define VERSION "1.0.0"
!endif
!define APPNAME "Shot Picker"
!define APPKEY "ShotPicker"
!define UNINSTKEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APPKEY}"

Name "${APPNAME} ${VERSION}"
OutFile "${OUT}"
InstallDir "$LOCALAPPDATA\Programs\${APPKEY}"
InstallDirRegKey HKCU "Software\${APPKEY}" "InstallDir"
RequestExecutionLevel user
SetCompressor /SOLID lzma
BrandingText "${APPNAME} ${VERSION}"

!define MUI_ICON "${SRC}\ShotPicker.ico"
!define MUI_UNICON "${SRC}\ShotPicker.ico"
!define MUI_ABORTWARNING
!define MUI_FINISHPAGE_RUN "$INSTDIR\ShotPicker.exe"
!define MUI_FINISHPAGE_RUN_TEXT "Shot Picker 실행"

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "Korean"

VIProductVersion "${VERSION}.0"
VIAddVersionKey /LANG=${LANG_KOREAN} "ProductName" "${APPNAME}"
VIAddVersionKey /LANG=${LANG_KOREAN} "FileVersion" "${VERSION}"
VIAddVersionKey /LANG=${LANG_KOREAN} "ProductVersion" "${VERSION}"
VIAddVersionKey /LANG=${LANG_KOREAN} "FileDescription" "${APPNAME} 설치"
VIAddVersionKey /LANG=${LANG_KOREAN} "LegalCopyright" "Studio Mago"

Section "Install"
  ; 실행 중이면 업데이트가 덮어쓸 수 있게 종료시키고, 실행 파일이 풀릴 때까지 기다림 (최대 약 15초)
  nsExec::Exec 'taskkill /F /IM ShotPicker.exe'
  StrCpy $R2 0
  unlock_retry:
    ClearErrors
    IfFileExists "$INSTDIR\ShotPicker.exe" 0 unlocked
    Delete "$INSTDIR\ShotPicker.exe"
    IfFileExists "$INSTDIR\ShotPicker.exe" 0 unlocked
    IntOp $R2 $R2 + 1
    IntCmp $R2 30 unlocked
    Sleep 500
    nsExec::Exec 'taskkill /F /IM ShotPicker.exe'
    Goto unlock_retry
  unlocked:

  SetOutPath "$INSTDIR"
  File "${SRC}\ShotPicker.exe"
  File "${SRC}\ShotPicker.ico"
  File "${SRC}\README.txt"

  SetOutPath "$INSTDIR"
  CreateDirectory "$SMPROGRAMS\${APPNAME}"
  CreateShortcut "$SMPROGRAMS\${APPNAME}\${APPNAME}.lnk" "$INSTDIR\ShotPicker.exe" "" "$INSTDIR\ShotPicker.ico"
  CreateShortcut "$SMPROGRAMS\${APPNAME}\${APPNAME} 제거.lnk" "$INSTDIR\Uninstall.exe"
  CreateShortcut "$DESKTOP\${APPNAME}.lnk" "$INSTDIR\ShotPicker.exe" "" "$INSTDIR\ShotPicker.ico"

  WriteUninstaller "$INSTDIR\Uninstall.exe"
  WriteRegStr HKCU "Software\${APPKEY}" "InstallDir" "$INSTDIR"
  WriteRegStr HKCU "Software\${APPKEY}" "Version" "${VERSION}"
  WriteRegStr HKCU "${UNINSTKEY}" "DisplayName" "${APPNAME}"
  WriteRegStr HKCU "${UNINSTKEY}" "DisplayVersion" "${VERSION}"
  WriteRegStr HKCU "${UNINSTKEY}" "Publisher" "Studio Mago"
  WriteRegStr HKCU "${UNINSTKEY}" "DisplayIcon" "$INSTDIR\ShotPicker.ico"
  WriteRegStr HKCU "${UNINSTKEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "${UNINSTKEY}" "UninstallString" '"$INSTDIR\Uninstall.exe"'
  WriteRegStr HKCU "${UNINSTKEY}" "QuietUninstallString" '"$INSTDIR\Uninstall.exe" /S'
  WriteRegDWORD HKCU "${UNINSTKEY}" "NoModify" 1
  WriteRegDWORD HKCU "${UNINSTKEY}" "NoRepair" 1
SectionEnd

; 앱 안 업데이트: 조용히 설치(/S)한 뒤 /RELAUNCH 가 있으면 앱을 다시 켬
Function .onInstSuccess
  ${GetParameters} $R0
  ClearErrors
  ${GetOptions} $R0 "/RELAUNCH" $R1
  IfErrors done
    Exec '"$INSTDIR\ShotPicker.exe"'
  done:
FunctionEnd

Section "Uninstall"
  nsExec::Exec 'taskkill /F /IM ShotPicker.exe'
  Sleep 500
  Delete "$DESKTOP\${APPNAME}.lnk"
  RMDir /r "$SMPROGRAMS\${APPNAME}"
  Delete "$INSTDIR\ShotPicker.exe"
  Delete "$INSTDIR\ShotPicker.ico"
  Delete "$INSTDIR\README.txt"
  Delete "$INSTDIR\shot_picker_log.txt"
  Delete "$INSTDIR\Uninstall.exe"
  RMDir "$INSTDIR"
  DeleteRegKey HKCU "${UNINSTKEY}"
  DeleteRegKey HKCU "Software\${APPKEY}"
SectionEnd
