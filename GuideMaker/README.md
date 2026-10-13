# Guide Maker

이미지를 불러와서 **왼쪽에는 원본**, **오른쪽에는 같은 이미지 위에 가이드**를 그리고,
원본 해상도 그대로 `원본이름_guide.확장자` 로 저장하는 툴입니다. 윈도우 / 맥 공용.

![icon](assets/GuideMaker.png)

## 다운로드
저장소 오른쪽 **Releases** → `Guide Maker v...` 중 최신 버전에서 내 컴퓨터에 맞는 파일을 받으세요.

| 컴퓨터 | 파일 |
|---|---|
| 윈도우 | `GuideMaker_Setup_<버전>.exe` → 더블클릭해서 설치 |
| 맥 (M1/M2/M3/M4) | `GuideMaker_<버전>_arm64.dmg` → 앱을 Applications로 끌어다 놓기 |
| 맥 (Intel) | `GuideMaker_<버전>_x86_64.dmg` |

- 윈도우에서 "Windows의 PC 보호" 창이 뜨면 **추가 정보 → 실행**
- 맥에서 "열지 않음" 경고가 뜨면 (처음 한 번만)
  - **시스템 설정 → 개인정보 보호 및 보안** → 아래쪽 **그래도 열기**
  - 또는 터미널에 `xattr -cr "/Applications/Guide Maker.app"`

## 사용법
1. **이미지 불러오기** (한 장 / 여러 장) 또는 **폴더 불러오기**
2. 오른쪽 화면에 가이드 그리기
   - 도구: 펜 / 박스 / 채운 박스 / 직선 / 화살표
   - 색: 색 버튼 또는 빠른 색, 투명도 5~100%, 두께 1~60
3. 아래 **저장 폴더** 지정 → **현재 이미지 내보내기** 또는 **전체 내보내기**
4. 저장 이름: `shot_0010.png` → `shot_0010_guide.png` (원본 해상도)

지원 형식: jpg, png, tif, bmp, webp, tga (EXR 미지원)

### 단축키 (맥은 Ctrl 대신 Cmd)
| 키 | 기능 |
|---|---|
| Ctrl+O | 이미지 불러오기 |
| Ctrl+E | 현재 이미지 내보내기 |
| Ctrl+Z / Ctrl+Y | 되돌리기 / 다시 실행 |
| ← / → | 이전 / 다음 이미지 |
| 1~5 | 도구 선택 |
| [ / ] | 두께 줄이기 / 늘리기 |

---

## 관리자용

### 폴더 구조
```
GuideMaker/src/guide_maker.py 툴 코드 (버전 번호 APP_VERSION)
assets/                       아이콘
config/                       설치 파일에 같이 들어가는 사용법
build/build_windows.ps1       윈도우 설치 파일 만들기 (PyInstaller + NSIS)
build/build_mac.sh            맥 dmg 만들기
build/installer.nsi           윈도우 설치 프로그램 설정
(저장소 맨 위) .github/workflows/guidemaker.yml 태그를 올리면 자동 빌드 + Releases 업로드
CHANGELOG.md                  변경 기록 (릴리스 설명에 그대로 들어감)
```

### 새 버전 내기
1. `src/guide_maker.py` 의 `APP_VERSION` 올리기 (예: `1.0.0` → `1.1.0`)
2. `CHANGELOG.md` 맨 위에 `## v1.1.0` 과 바뀐 내용 추가
3. 커밋하고 태그 올리기
   ```
   git commit -am "Guide Maker v1.1.0"
   git tag guidemaker-v1.1.0
   git push origin main guidemaker-v1.1.0
   ```
4. 10~20분 뒤 Releases에 윈도우 / 맥 설치 파일 3개가 자동으로 올라감

태그(`guidemaker-v` 뒤 번호)와 `APP_VERSION`이 다르면 빌드가 멈춥니다(실수 방지).

### 직접 빌드
- 윈도우: Python 3.12 + NSIS → `powershell -ExecutionPolicy Bypass -File build\build_windows.ps1`
- 맥: `bash build/build_mac.sh` (Homebrew 파이썬이면 `PY=/opt/homebrew/bin/python3.14`)

### 소스로 바로 실행
```
pip install pillow
python GuideMaker/src/guide_maker.py
```
