; Guide Maker 윈도우 설치 프로그램 (NSIS)
; makensis -DVERSION=1.0.0 -DSRC=<payload 폴더> -DOUT=<출력 exe> installer.nsi
; 공식 윈도우용 NSIS 에는 64비트 stub 이 없어서 기본(32비트 유니코드)으로 빌드. 64비트 stub 이 있는 환경이면 -DAMD64
!ifdef AMD64
  Target amd64-unicode
!else
  Unicode true
!endif
!include "MUI2.nsh"

!ifndef VERSION
  !define VERSION "1.0.0"
!endif
!define APPNAME "Guide Maker"
!define APPKEY "GuideMaker"
!define UNINSTKEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APPKEY}"

Name "${APPNAME} ${VERSION}"
OutFile "${OUT}"
InstallDir "$LOCALAPPDATA\Programs\${APPKEY}"
InstallDirRegKey HKCU "Software\${APPKEY}" "InstallDir"
RequestExecutionLevel user
SetCompressor /SOLID lzma
BrandingText "${APPNAME} ${VERSION}"

!define MUI_ICON "${SRC}\GuideMaker.ico"
!define MUI_UNICON "${SRC}\GuideMaker.ico"
!define MUI_ABORTWARNING
!define MUI_FINISHPAGE_RUN "$INSTDIR\GuideMaker.exe"
!define MUI_FINISHPAGE_RUN_TEXT "Guide Maker 실행"

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
  ; 실행 중이면 업데이트가 덮어쓸 수 있게 잠시 기다렸다가 강제 종료
  nsExec::Exec 'taskkill /F /IM GuideMaker.exe'
  Sleep 800

  SetOutPath "$INSTDIR"
  File "${SRC}\GuideMaker.exe"
  File "${SRC}\GuideMaker.ico"
  File "${SRC}\README.txt"
  ; 설정 파일은 이미 있으면 사용자가 고친 걸 유지

  SetOutPath "$INSTDIR"
  CreateDirectory "$SMPROGRAMS\${APPNAME}"
  CreateShortcut "$SMPROGRAMS\${APPNAME}\${APPNAME}.lnk" "$INSTDIR\GuideMaker.exe" "" "$INSTDIR\GuideMaker.ico"
  CreateShortcut "$SMPROGRAMS\${APPNAME}\${APPNAME} 제거.lnk" "$INSTDIR\Uninstall.exe"
  CreateShortcut "$DESKTOP\${APPNAME}.lnk" "$INSTDIR\GuideMaker.exe" "" "$INSTDIR\GuideMaker.ico"

  WriteUninstaller "$INSTDIR\Uninstall.exe"
  WriteRegStr HKCU "Software\${APPKEY}" "InstallDir" "$INSTDIR"
  WriteRegStr HKCU "Software\${APPKEY}" "Version" "${VERSION}"
  WriteRegStr HKCU "${UNINSTKEY}" "DisplayName" "${APPNAME}"
  WriteRegStr HKCU "${UNINSTKEY}" "DisplayVersion" "${VERSION}"
  WriteRegStr HKCU "${UNINSTKEY}" "Publisher" "Studio Mago"
  WriteRegStr HKCU "${UNINSTKEY}" "DisplayIcon" "$INSTDIR\GuideMaker.ico"
  WriteRegStr HKCU "${UNINSTKEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "${UNINSTKEY}" "UninstallString" '"$INSTDIR\Uninstall.exe"'
  WriteRegStr HKCU "${UNINSTKEY}" "QuietUninstallString" '"$INSTDIR\Uninstall.exe" /S'
  WriteRegDWORD HKCU "${UNINSTKEY}" "NoModify" 1
  WriteRegDWORD HKCU "${UNINSTKEY}" "NoRepair" 1
SectionEnd

Section "Uninstall"
  nsExec::Exec 'taskkill /F /IM GuideMaker.exe'
  Sleep 500
  Delete "$DESKTOP\${APPNAME}.lnk"
  RMDir /r "$SMPROGRAMS\${APPNAME}"
  Delete "$INSTDIR\GuideMaker.exe"
  Delete "$INSTDIR\GuideMaker.ico"
  Delete "$INSTDIR\README.txt"
  Delete "$INSTDIR\guide_maker_log.txt"
  Delete "$INSTDIR\Uninstall.exe"
  RMDir "$INSTDIR"
  DeleteRegKey HKCU "${UNINSTKEY}"
  DeleteRegKey HKCU "Software\${APPKEY}"
SectionEnd
