# Guide Maker

이미지를 불러와서 **왼쪽에는 원본**, **오른쪽에는 같은 이미지 위에 가이드**(펜, 박스, 화살표, 그리드, 글자)를 그리고,
원본 해상도 그대로 `원본이름_guide.확장자` 로 저장하는 툴입니다. 윈도우 / 맥 공용.

![icon](assets/GuideMaker.png)

## 다운로드
**[Guide Maker 받기](https://github.com/HsongLim/MagoTools/releases?q=guidemaker&expanded=true)** → 맨 위(최신 버전)에서 내 컴퓨터에 맞는 파일을 받으세요.

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
앱 오른쪽 아래 **?** 에 마우스를 올리면 요약, 누르면 전체 설명서가 열립니다.

1. **이미지 불러오기** (한 장 / 여러 장), **폴더 불러오기**, 또는 이미지 파일·폴더를 창에 **끌어다 놓기**
2. 오른쪽 화면에 가이드 그리기
   - 도구 (숫자 키): 1 선택 · 2 펜 · 3 박스 · 4 채운 박스 · 5 직선 · 6 화살표 · 7 그리드 · 8 글자 · 9 지우개
   - 어느 도구에서든 **Ctrl(맥은 Cmd) + 드래그** = 화살표
   - **그리드**: 드래그로 만들기, 코너 = 코너핀, Shift + 코너 = 모양 그대로 크기, 변 가운데 = 한 변만 이동. 칸 수와 선 굵기(실제 이미지 픽셀) 설정
   - **글자**: 이미지 위를 클릭한 자리에서 바로 입력 → 다른 곳 클릭 / Esc 로 끝. 두 번 클릭하면 고치기. 테두리는 기본 꺼짐
3. **선택해서 고치기** (모든 가이드 공통): 가이드를 클릭하면 선택
   - 끌기 = 이동, 손잡이 = 크기 / 모양 (화살표·직선은 양 끝 점으로 방향), 휠 = 확대 / 축소
   - 선택한 채 색 · 투명도 · 굵기 · 글자 크기 · 테두리 · 그리드 값을 바꾸면 바로 적용
   - Delete / Backspace = 지우기, Esc = 선택 풀기, 지우개 도구 = 클릭한 것 하나 지우기
4. 내보내기: **가이드만**(`shot_0010_guide.png`) / **원본 + 가이드 붙이기**(`shot_0010_compare.png`) / **둘 다**
   - 붙일 때 좌우 / 위아래 배치, 구분선, "원본 / 가이드" 이름표를 켜고 끌 수 있음
   - 옆의 **미리보기** 칸에 저장될 모습이 작게 보임
   - 모두 원본 해상도 그대로, 화면의 손잡이·선택 표시는 저장되지 않음

지원 형식: jpg, png, tif, bmp, webp, tga (EXR 미지원)

### 업데이트
프로그램을 켤 때 새 버전이 있으면 창이 뜹니다. **예**를 누르면 받아서 설치하고 다시 켜집니다. (v1.1.0부터)
오른쪽 위 **업데이트 확인** 버튼으로 직접 확인할 수도 있습니다.

### 단축키 (맥은 Ctrl 대신 Cmd, 한글 입력 상태에서도 동작)
| 키 | 기능 |
|---|---|
| Ctrl+O | 이미지 불러오기 |
| Ctrl+E | 현재 이미지 내보내기 |
| Ctrl+Z / Ctrl+Y | 되돌리기 / 다시 실행 |
| 1~9, V | 도구 선택 (V = 선택) |
| ← / → | 이전 / 다음 이미지 |
| [ / ] | 굵기 줄이기 / 늘리기 |
| Delete / Backspace | 선택한 가이드 지우기 |
| Esc | 선택 풀기 |

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

설명서는 `src/guide_maker.py` 의 `help_text()` 한 곳에서 관리합니다.
앱의 **?** 창과 설치 파일에 들어가는 사용법 txt 가 모두 여기서 만들어지니, 기능이 바뀌면 여기를 고치세요.

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
5. 이미 설치한 사람은 앱을 켜면 "새 버전이 있습니다" 창이 뜸 (태그가 `guidemaker-v`로 시작하는 릴리스 중 가장 높은 버전)

태그 대신 GitHub 저장소 → **Actions** → **guidemaker** → **Run workflow** 로 만들어도 됩니다 (main 의 `APP_VERSION` 으로 릴리스 생성).

태그(`guidemaker-v` 뒤 번호)와 `APP_VERSION`이 다르면 빌드가 멈춥니다(실수 방지).

### 직접 빌드
- 윈도우: Python 3.12 + NSIS → `powershell -ExecutionPolicy Bypass -File build\build_windows.ps1`
- 맥: `bash build/build_mac.sh` (Homebrew 파이썬이면 `PY=/opt/homebrew/bin/python3.14`)

### 소스로 바로 실행
```
pip install pillow
python GuideMaker/src/guide_maker.py
```
