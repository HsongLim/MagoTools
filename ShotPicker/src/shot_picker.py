# -*- coding: utf-8 -*-
"""
Shot Picker
-----------
AI 로 뽑은 이미지가 잔뜩 든 폴더를 열면 파일 이름 앞의 샷 이름으로 묶어서 샷별로 보여 준다.
샷마다 썸네일을 보면서 마음에 드는 것만 골라(pick) 저장하면
  이미지폴더/pick/      <- 고른 이미지
  이미지폴더/not_pick/  <- 안 고른 이미지
로 복사한다 (원본은 그대로). 원본 플레이트 폴더를 정해 주면 같은 샷의 원본을 켰다 껐다 하며 비교할 수 있다.
"""
import os
import re
import sys
import json
import queue
import shutil
import socket
import platform
import tempfile
import threading
import traceback
import subprocess

from PIL import Image, ImageOps

APP_NAME = "Shot Picker"
APP_VERSION = "1.1.1"
# 업데이트 확인: 이 저장소의 Releases 중 태그가 TAG_PREFIX 로 시작하는 것
GH_REPO = "HsongLim/MagoTools"
TAG_PREFIX = "shotpicker-v"
STUDIO_PROXY = "192.168.50.200:3128"   # 회사 안에서는 이 프록시로 인터넷에 나감 (없으면 바로 연결)
IS_WIN = platform.system() == "Windows"
IS_MAC = platform.system() == "Darwin"
HERE = os.path.dirname(sys.executable if getattr(sys, "frozen", False) else os.path.abspath(__file__))
BUNDLE = getattr(sys, "_MEIPASS", HERE)
SETTINGS_FILE = os.path.join(os.path.expanduser("~"), ".shot_picker.json")

IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp", ".tga")
PICK_DIR, NOPICK_DIR = "pick", "not_pick"


# ============================================================================
# 설정
# ============================================================================
def load_settings():
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_settings(d):
    try:
        tmp = SETTINGS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=1)
        os.replace(tmp, SETTINGS_FILE)
    except Exception:
        pass


# ============================================================================
# 파일 찾기 / 샷 이름
# ============================================================================
def shot_of(path, parts=3):
    """파일 이름 앞에서 '_' 로 나눈 조각 parts 개 = 샷 이름.  EP01_S048_9040_v003_0012.png -> EP01_S048_9040"""
    stem = os.path.splitext(os.path.basename(path))[0]
    toks = stem.split("_")
    return "_".join(toks[:parts]) if len(toks) >= parts else stem


def norm(p):
    return os.path.normcase(os.path.abspath(p))


def scan_images(folder, skip_dirs=()):
    """폴더(하위 폴더 포함) 안의 이미지. skip_dirs(저장 폴더의 pick / not_pick 등)는 건너뜀"""
    skip = {norm(d) for d in skip_dirs if d}
    out = []
    for dp, dns, fns in os.walk(folder):
        dns[:] = sorted(d for d in dns if not d.startswith(".") and norm(os.path.join(dp, d)) not in skip)
        for fn in sorted(fns):
            if fn.lower().endswith(IMAGE_EXTS) and not fn.startswith("."):
                out.append(os.path.join(dp, fn))
    return out


def group_by_shot(paths, parts=3):
    groups = {}
    for p in paths:
        groups.setdefault(shot_of(p, parts), []).append(p)
    for k in groups:
        groups[k].sort(key=lambda x: os.path.basename(x).lower())
    return dict(sorted(groups.items()))


def find_plates(plate_dir, parts=3):
    """원본 플레이트 폴더 -> {샷 이름: 이미지 경로}.  첫 프레임(first / start) 이 있으면 그걸 씀"""
    if not plate_dir or not os.path.isdir(plate_dir):
        return {}
    best = {}
    for p in scan_images(plate_dir):
        s = shot_of(p, parts)
        low = p.lower()
        score = (0 if ("_first_" in low or "start_frame" in low) else 1 if "_last_" not in low else 2, low)
        if s not in best or score < best[s][0]:
            best[s] = (score, p)
    return {s: v[1] for s, v in best.items()}


VIDEO_EXTS = (".mp4", ".mov", ".mxf", ".avi", ".mkv")
VIDEO_DIR = "04_video"
# 작업 폴더 구조 (00_작업폴더_설명.txt 참고)
WS_PLATE, WS_GUIDE, WS_AI, WS_VIDEO, WS_RESULT = "01_plate", "02_guide", "03_ai_images", "04_video", "05_result"


def find_videos(plate_dir, parts=3):
    """원본 플레이트 폴더 -> {샷 이름: 원본 영상}.  org 가 들어간 것, 그중 이름이 뒤인 것(높은 버전)"""
    if not plate_dir or not os.path.isdir(plate_dir):
        return {}
    best = {}
    for dp, dns, fns in os.walk(plate_dir):
        dns[:] = [d for d in dns if not d.startswith(".")]
        for fn in fns:
            if not fn.lower().endswith(VIDEO_EXTS) or fn.startswith("."):
                continue
            path = os.path.join(dp, fn)
            sh = shot_of(fn, parts)
            score = (1 if "org" in path.lower() else 0, fn.lower())
            if sh not in best or score > best[sh][0]:
                best[sh] = (score, path)
    return {k: v[1] for k, v in best.items()}


def rank_name(path, shot, rank):
    """고른 순위를 이름에 넣음:  EP01_S048_9040_v002_01.png (2위) -> EP01_S048_9040_r02_v002_01.png"""
    base = os.path.basename(path)
    rest = base[len(shot):] if base.startswith(shot) else "_" + base
    rest = re.sub(r"^_r\d{2}(?=_)", "", rest)  # 이미 순위가 붙은 이름이면 떼고 다시
    return "%s_r%02d%s" % (shot, rank, rest)


def _stale(folder, shot, sources, keep, parts):
    """folder 안의 이 샷 이미지 중, 예전에 우리가 복사해 둔 것(원본과 크기·시간이 같음)인데 이번 계획에 없는 것"""
    out = []
    if not os.path.isdir(folder):
        return out
    keep = {norm(k) for k in keep}
    for fn in os.listdir(folder):
        fp = os.path.join(folder, fn)
        if (fn.lower().endswith(IMAGE_EXTS) and shot_of(fn, parts) == shot and norm(fp) not in keep
                and os.path.isfile(fp) and any(same_file(src, fp) for src in sources)):
            out.append(fp)
    return out


def plan_save(groups, picks, out_dir, include_empty=False, parts=3):
    """pick / not_pick 복사 계획 -> (복사 [(원본, 넣을 곳)], 지울 예전 복사본, 건너뛴 샷).
    picks[샷] = 고른 순서대로 된 목록 (1위, 2위, ...). pick 쪽 이름에 순위(_r01_)를 붙임.
    not_pick 에서 같은 이름이 다른 폴더에서 또 나오면 이름 뒤에 _2, _3 을 붙임"""
    d_pick, d_no = os.path.join(out_dir, PICK_DIR), os.path.join(out_dir, NOPICK_DIR)
    used = {}
    plan, removals, skipped = [], [], []
    for shot, files in groups.items():
        sel = list(picks.get(shot) or [])
        if not sel and not include_empty:
            skipped.append(shot)
            continue
        dsts = []
        for f in files:
            if f in sel:
                dst = os.path.join(d_pick, rank_name(f, shot, sel.index(f) + 1))
            else:
                base = os.path.basename(f)
                name, n = base, 1
                while name.lower() in used and used[name.lower()] != norm(f):
                    n += 1
                    stem, ext = os.path.splitext(base)
                    name = "%s_%d%s" % (stem, n, ext)
                used[name.lower()] = norm(f)
                dst = os.path.join(d_no, name)
            plan.append((f, dst))
            dsts.append(dst)
        for d in (d_pick, d_no):  # 순위가 바뀌었거나 고르기를 바꾼 예전 복사본
            removals += _stale(d, shot, files, dsts, parts)
    return plan, removals, skipped


def plan_video(groups, picks, videos, out_root, parts=3):
    """04_video 계획: 고른 게 있는 샷마다 out_root/샷/ 에 원본 영상 + 고른 이미지(순위 이름)
    -> (복사 목록, 지울 예전 복사본, 만든 샷, 원본 영상이 없는 샷)"""
    plan, removals, done, no_video = [], [], [], []
    for shot, files in groups.items():
        sel = list(picks.get(shot) or [])
        if not sel:
            continue
        d = os.path.join(out_root, shot)
        dsts = []
        v = videos.get(shot)
        if v:
            plan.append((v, os.path.join(d, os.path.basename(v))))
        else:
            no_video.append(shot)
        for i, f in enumerate(sel):
            dst = os.path.join(d, rank_name(f, shot, i + 1))
            plan.append((f, dst))
            dsts.append(dst)
        removals += _stale(d, shot, files, dsts, parts)
        done.append(shot)
    return plan, removals, done, no_video


def same_file(a, b):
    try:
        sa, sb = os.stat(a), os.stat(b)
        return sa.st_size == sb.st_size and int(sa.st_mtime) == int(sb.st_mtime)
    except Exception:
        return False


def run_save(plan, removals=(), progress=None):
    """계획대로 복사 (이미 같은 파일이 있으면 건너뜀) + 예전 복사본 정리 (원본은 건드리지 않음)"""
    copied = removed = 0
    for i, (src, dst) in enumerate(plan):
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        if not same_file(src, dst):
            tmp = dst + ".part"
            shutil.copy2(src, tmp)
            os.replace(tmp, dst)
            copied += 1
        if progress:
            progress(i + 1, len(plan))
    for fp in removals:
        try:
            os.remove(fp)
            removed += 1
        except Exception:
            pass
    return copied, removed


def load_thumb(path, w, h):
    im = Image.open(path)
    try:
        im.draft("RGB", (w * 2, h * 2))  # jpg 는 작게 읽어서 빠르게
    except Exception:
        pass
    im = ImageOps.exif_transpose(im)
    if im.mode not in ("RGB", "RGBA"):
        im = im.convert("RGBA" if "A" in im.getbands() else "RGB")
    im.thumbnail((w, h), Image.BILINEAR if max(im.size) > 4 * max(w, h) else Image.LANCZOS)
    if im.mode == "RGBA":
        bg = Image.new("RGB", im.size, (40, 40, 40))
        bg.paste(im, mask=im.split()[3])
        im = bg
    return im


def image_size(path):
    try:
        with Image.open(path) as im:
            return im.size
    except Exception:
        return None


def open_folder(path):
    try:
        if IS_WIN:
            os.startfile(path)
        elif IS_MAC:
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
    except Exception:
        pass


# ============================================================================
# 업데이트 (GitHub Releases)
# ============================================================================
MAC_UPDATE_SCRIPT = """#!/bin/bash
PID=__PID__; DMG="__DMG__"; APP="__APP__"
while kill -0 $PID 2>/dev/null; do sleep 0.5; done
MNT=$(mktemp -d)
hdiutil attach -nobrowse -quiet -mountpoint "$MNT" "$DMG" || { open "$DMG"; exit 1; }
NEW=$(ls -d "$MNT"/*.app | head -1)
if [ -n "$NEW" ]; then
  rm -rf "$APP.old"; mv "$APP" "$APP.old" && cp -R "$NEW" "$APP" && rm -rf "$APP.old"
  xattr -cr "$APP" 2>/dev/null
fi
hdiutil detach -quiet "$MNT"
open "$APP"
"""


def _ver_tuple(v):
    nums = [int(x) for x in re.findall(r"\d+", str(v or ""))[:3]]
    return tuple(nums + [0] * (3 - len(nums)))


def _proxy_reachable(proxy, timeout=1.5):
    try:
        host, port = proxy.rsplit(":", 1)
        socket.create_connection((host, int(port)), timeout=timeout).close()
        return True
    except Exception:
        return False


def _ssl_handler():
    import ssl
    import urllib.request
    try:
        cafile = os.environ.get("SSL_CERT_FILE")
        if not (cafile and os.path.isfile(cafile)):
            import certifi  # 맥 앱 안의 파이썬은 인증서 목록이 없을 수 있어서 같이 넣음
            cafile = certifi.where()
        return urllib.request.HTTPSHandler(context=ssl.create_default_context(cafile=cafile))
    except Exception:
        return None


def _routes():
    """인터넷에 나가는 길 후보: 회사 프록시 -> 컴퓨터 기본 설정 -> 프록시 없이 바로"""
    out = []
    if STUDIO_PROXY and _proxy_reachable(STUDIO_PROXY):
        out.append("studio")
    out += ["system", "direct"]
    if _route_ok and _route_ok[0] in out:  # 지난번에 된 길을 먼저
        out.remove(_route_ok[0])
        out.insert(0, _route_ok[0])
    return out


_route_ok = []


def _http_opener(route="system"):
    import urllib.request
    handlers = []
    if route == "studio":
        handlers.append(urllib.request.ProxyHandler({"http": "http://" + STUDIO_PROXY,
                                                     "https": "http://" + STUDIO_PROXY}))
    elif route == "direct":
        handlers.append(urllib.request.ProxyHandler({}))  # 프록시 설정 무시
    h = _ssl_handler()
    if h is not None:
        handlers.append(h)
    return urllib.request.build_opener(*handlers)


def _get(url, accept="application/vnd.github+json", timeout=10):
    """회사 프록시가 GitHub 을 막는 경우(403 등)가 있어서, 안 되면 다른 길로 다시 시도"""
    import urllib.request
    import urllib.error
    errors = []
    for route in _routes():
        # 요청은 매번 새로 (프록시를 한 번 거친 요청 객체는 그 프록시 정보가 남음)
        req = urllib.request.Request(url, headers={"Accept": accept, "User-Agent": "ShotPicker/" + APP_VERSION})
        try:
            resp = _http_opener(route).open(req, timeout=timeout)
            _route_ok[:] = [route]
            return resp
        except urllib.error.HTTPError:
            raise  # GitHub 까지는 닿았고 GitHub 이 답한 오류 (다른 길로 가도 같음)
        except Exception as e:  # 프록시 거부(Tunnel ... 403), 연결 시간 초과 등: 다음 길로
            errors.append("%s: %s" % (route, e))
    raise RuntimeError("GitHub 에 연결할 수 없습니다.\n" + "\n".join(errors))


def pick_release_asset(assets, is_win=None, is_mac=None, arch=None):
    """이 컴퓨터에 맞는 설치 파일 하나를 고른다."""
    is_win = IS_WIN if is_win is None else is_win
    is_mac = IS_MAC if is_mac is None else is_mac
    names = [(a.get("name") or "", a) for a in assets or []]
    if is_win:
        for n, a in names:
            if re.search(r"setup.*\.exe$", n, re.I):
                return a
    elif is_mac:
        arch = (arch or platform.machine() or "").lower()
        arch = "arm64" if arch in ("arm64", "aarch64") else "x86_64"
        dmgs = [(n, a) for n, a in names if n.lower().endswith(".dmg")]
        for n, a in dmgs:
            if arch in n.lower():
                return a
        if len(dmgs) == 1:
            return dmgs[0][1]
    return None


def latest_release(releases, prefix=TAG_PREFIX):
    """여러 툴이 같이 있는 저장소에서 이 툴의 가장 높은 버전 릴리스"""
    best = None
    for r in releases or []:
        tag = r.get("tag_name") or ""
        if r.get("draft") or r.get("prerelease") or not tag.startswith(prefix):
            continue
        ver = tag[len(prefix):]
        if best is None or _ver_tuple(ver) > _ver_tuple(best[0]):
            best = (ver, r)
    return best


def check_update(timeout=10):
    """새 버전이 있으면 {"version","asset","notes","url"} 를, 없으면 None 을 돌려준다."""
    with _get("https://api.github.com/repos/%s/releases?per_page=50" % GH_REPO, timeout=timeout) as r:
        data = json.loads(r.read().decode("utf-8"))
    best = latest_release(data)
    if not best or _ver_tuple(best[0]) <= _ver_tuple(APP_VERSION):
        return None
    ver, rel = best
    return {"version": ver, "asset": pick_release_asset(rel.get("assets")),
            "notes": rel.get("body") or "", "url": rel.get("html_url") or ""}


def download_asset(asset, dest, progress=None, timeout=60):
    resp = _get(asset["browser_download_url"], accept="application/octet-stream", timeout=timeout)
    total = int(resp.headers.get("Content-Length") or asset.get("size") or 0)
    done = 0
    tmp = dest + ".part"
    with resp, open(tmp, "wb") as f:
        while True:
            chunk = resp.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            if progress:
                progress(done, total)
    if total and done != total:
        raise RuntimeError("다운로드가 중간에 끊겼습니다 (%d / %d)" % (done, total))
    os.replace(tmp, dest)
    return dest


def _running_app_bundle():
    m = re.match(r"(.*?\.app)/Contents/MacOS/", os.path.abspath(sys.executable))
    return m.group(1) if m else None


def _clean_env():
    """새로 켜질 앱이 지금 앱(PyInstaller)의 내부 환경변수를 물려받지 않게 정리"""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("_PYI", "_MEIPASS"))}
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    for k in ("TCL_LIBRARY", "TK_LIBRARY"):
        if k in env and BUNDLE and str(env[k]).startswith(str(BUNDLE)):
            env.pop(k)
    return env


def apply_update(installer):
    """받은 설치 파일을 실행한다. True 면 호출한 쪽에서 앱을 바로 종료해야 함."""
    if IS_WIN:
        # 조용히 설치하고 끝나면 앱을 다시 켬 (설치 프로그램이 /RELAUNCH 를 처리)
        subprocess.Popen([installer, "/S", "/RELAUNCH"], close_fds=True, env=_clean_env(),
                         creationflags=0x00000008 | 0x00000200)  # DETACHED_PROCESS | NEW_PROCESS_GROUP
        return True
    if IS_MAC:
        app = _running_app_bundle()
        if not app:
            subprocess.Popen(["open", installer])
            return False
        script = os.path.join(tempfile.mkdtemp(prefix="sp_update_"), "update.sh")
        with open(script, "w") as f:
            f.write(MAC_UPDATE_SCRIPT.replace("__PID__", str(os.getpid()))
                    .replace("__DMG__", installer).replace("__APP__", app))
        subprocess.Popen(["/bin/bash", script], start_new_session=True, close_fds=True, env=_clean_env())
        return True
    return False



# ============================================================================
# 단축키 (한글 입력 상태에서도 되게: 영문 글자가 안 오면 한글 자모나 키 위치로 알아봄)
# ============================================================================
_HANGUL_KEYS = {"ㅐ": "o", "ㄹ": "f", "ㄴ": "s", "ㅁ": "a", "ㅔ": "p", "ㅎ": "g"}
_WIN_VK = {79: "o", 70: "f", 83: "s", 65: "a", 80: "p", 71: "g"}
_MAC_VK = {31: "o", 3: "f", 1: "s", 0: "a", 35: "p", 5: "g"}


def shortcut_letter(keysym, char, keycode, mac=None):
    mac = IS_MAC if mac is None else mac
    ks = keysym or ""
    if len(ks) == 1 and ks.isascii() and ks.isalpha():
        return ks.lower()
    for c in (char or "", ks):
        if c in _HANGUL_KEYS:
            return _HANGUL_KEYS[c]
    kc = keycode or 0
    if mac:
        for v in ((kc >> 24) & 0xFF, kc):
            if v in _MAC_VK:
                return _MAC_VK[v]
    elif kc in _WIN_VK:
        return _WIN_VK[kc]
    return ""


# ============================================================================
# 설명서 (앱 안 [?] 창, 설치 파일의 사용법 txt 가 모두 여기서 만들어짐 - 기능이 바뀌면 여기를 고칠 것)
# ============================================================================
def help_text(mac=None):
    mac = IS_MAC if mac is None else mac
    m = "Cmd" if mac else "Ctrl"
    return """Shot Picker v{ver} 사용법
========================
AI 로 뽑은 이미지가 많을 때, 같은 샷끼리 크게 보면서 마음에 드는 것만 골라 따로 모으는 툴입니다.

준비
  - 이미지 파일 이름 맨 앞에 샷 이름을 넣어 주세요.   예) EP01_S048_9040_v003_0012.png
  - 샷 이름 = 파일 이름을 '_' 로 나눈 앞의 조각 몇 개 (기본 3개: EP01 / S048 / 9040)
    샷 이름 모양이 다르면 위쪽 "샷 이름 = 앞 [3] 조각" 숫자를 바꾸세요.

순서
 1. 이미지 폴더: "폴더 추가..." 로 여러 개 넣을 수 있음 (창에 폴더 여러 개를 끌어다 놓아도 됨)
      이미지가 여러 폴더에 흩어져 있어도 샷 이름이 같으면 한 샷으로 모아서 보여 줍니다 (하위 폴더까지 찾음)
 2. (선택) 원본 플레이트 폴더: 같은 샷 이름으로 시작하는 원본 이미지가 있는 폴더
      Plate Exporter 로 받은 폴더를 그대로 쓰면 됩니다 (start_frame / first 이미지를 먼저 씀)
 3. 왼쪽에서 샷을 고르면 이미지가 크게 나오고, 아래 줄에 같은 샷 이미지들이 보입니다
      ← → (또는 휠) 로 넘기면서 이미지를 클릭(또는 스페이스)하면 고르기 (초록 테두리 + PICK)
 4. "저장" -> 첫 번째 이미지 폴더 안에 pick (고른 것) , not_pick (안 고른 것) 폴더를 만들어 복사
      - 원본 이미지는 그대로 둡니다 (복사만). 다른 폴더에 있던 이미지도 같은 pick / not_pick 으로 모음
      - 고른 게 하나도 없는 샷은 아직 안 본 샷으로 보고 건너뜁니다
        ("고른 게 없는 샷도 not_pick 으로" 를 켜면 같이 넣음)
      - 다시 저장하면 바뀐 것만 반영 (고르기를 취소한 이미지는 pick 에서 not_pick 으로 옮겨짐)
  고른 내용은 자동으로 기억해서, 껐다 켜도 그대로입니다.

크게 보기 (기본 화면)
  ← → / 휠                이전 / 다음 이미지 (아래 줄을 클릭해도 됨)
  ↓ / ↑                   다음 / 이전 샷
  클릭 / 스페이스 / P     고르기 켜고 끄기
  O                       원본 나란히 보기 켜고 끄기 (왼쪽 원본, 오른쪽 AI)
  F                       원본으로 바꿔 보기 (같은 자리에서 원본 <-> AI 깜빡이며 비교)
  [ / ]  (PageUp / Down)  이전 / 다음 샷 (↑ ↓ 와 같음)
  G                       썸네일 모아 보기 <-> 크게 보기
  {m}+S                     저장

썸네일 모아 보기 (G)
  클릭 / 스페이스         고르기 켜고 끄기
  더블클릭 / Enter        그 이미지 크게 보기
  ← → ↑ ↓               이동,   {m} + 휠 / 크기 막대  썸네일 크기
""".format(ver=APP_VERSION, m=m)


# ============================================================================
# 화면
# ============================================================================
def main():
    if IS_WIN:
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox
    from PIL import ImageTk

    settings = load_settings()
    try:
        from tkinterdnd2 import TkinterDnD, DND_FILES
        root = TkinterDnD.Tk()
        has_dnd = True
    except Exception:
        root = tk.Tk()
        has_dnd = False
    root.title("%s  v%s" % (APP_NAME, APP_VERSION))
    root.geometry(settings.get("geometry") or "1400x880")
    root.minsize(900, 560)
    try:
        for d in (HERE, BUNDLE):
            ico = os.path.join(d, "ShotPicker.ico")
            if IS_WIN and os.path.isfile(ico):
                root.iconbitmap(ico)
                break
    except Exception:
        pass
    BG, TILE_BG, PICK, FOCUS, ORIG = "#1e1e1e", "#2b2b2b", "#2bd94a", "#4aa3ff", "#ff9d2e"

    st = {
        "groups": {}, "shots": [], "shot": None, "picks": {}, "plates": {},
        "tiles": [], "focus": 0, "mode": settings.get("mode", "view"), "flip": False,
        "thumbs": {}, "wanted": set(), "busy": False, "folder": None, "folders": [], "loaded": set(), "videos": {},
    }
    q = queue.Queue()
    thumb_q = queue.LifoQueue()

    # ---- 위: 폴더 ----
    top = ttk.Frame(root, padding=(8, 6, 8, 2))
    top.pack(fill="x")
    srcs = [d for d in (settings.get("srcs") or ([settings["src"]] if settings.get("src") else [])) if d]
    plate_var = tk.StringVar(value=settings.get("plate", ""))
    parts_var = tk.IntVar(value=int(settings.get("parts", 3)))

    ttk.Label(top, text="이미지 폴더").grid(row=0, column=0, sticky="nw", pady=2)
    src_list = tk.Listbox(top, height=3, activestyle="none", exportselection=False, selectmode="extended")
    src_list.grid(row=0, column=1, sticky="we", padx=6, pady=2)
    for d in srcs:
        src_list.insert("end", d)
    sbtn = ttk.Frame(top)
    sbtn.grid(row=0, column=2, sticky="n")
    ttk.Button(sbtn, text="폴더 추가...", command=lambda: add_folder()).pack(fill="x")
    ttk.Button(sbtn, text="선택한 폴더 빼기", command=lambda: remove_folders()).pack(fill="x", pady=2)
    ttk.Label(top, text="여러 폴더 가능 (끌어다 놓아도 됨)\n저장은 첫 번째 폴더 안 pick / not_pick",
              foreground="#888888", justify="left").grid(row=0, column=3, sticky="nw", padx=6)

    def folder_list():
        return [src_list.get(i) for i in range(src_list.size())]

    def add_folders(paths):
        have = {norm(d) for d in folder_list()}
        added = False
        for d in paths:
            if d and os.path.isdir(d) and norm(d) not in have:
                src_list.insert("end", d)
                have.add(norm(d))
                added = True
        if added:
            load_folder()

    def add_folder():
        cur = folder_list()
        d = filedialog.askdirectory(parent=root, initialdir=(os.path.dirname(cur[-1]) if cur else os.path.expanduser("~")))
        if d:
            add_folders([d])

    def remove_folders():
        for i in reversed(src_list.curselection()):
            src_list.delete(i)
        load_folder()

    ttk.Label(top, text="원본 플레이트").grid(row=1, column=0, sticky="w", pady=2)
    ttk.Entry(top, textvariable=plate_var).grid(row=1, column=1, sticky="we", padx=6)

    def browse_plate():
        d = filedialog.askdirectory(parent=root, initialdir=plate_var.get() or os.path.expanduser("~"))
        if d:
            plate_var.set(d)
            load_folder()
    ttk.Button(top, text="찾기...", command=browse_plate).grid(row=1, column=2, sticky="we")
    ttk.Label(top, text="(선택) 같은 샷 이름의 원본 이미지 폴더", foreground="#888888").grid(row=1, column=3, sticky="w", padx=6)
    top.columnconfigure(1, weight=1)

    # ---- 작업 폴더: 정해 두면 03_ai_images / 01_plate / 04_video 를 알아서 씀 ----
    wsf = ttk.Frame(root, padding=(8, 6, 8, 0))
    wsf.pack(fill="x", before=top)
    ws_var = tk.StringVar(value=settings.get("workspace", ""))
    ttk.Label(wsf, text="작업 폴더").pack(side="left")
    ttk.Entry(wsf, textvariable=ws_var).pack(side="left", fill="x", expand=True, padx=6)

    def browse_ws():
        d = filedialog.askdirectory(parent=root, initialdir=ws_var.get() or os.path.expanduser("~"))
        if d:
            ws_var.set(d)
            apply_ws()
    ttk.Button(wsf, text="찾기...", command=browse_ws).pack(side="left")
    ttk.Button(wsf, text="이 작업 폴더로 맞추기", command=lambda: apply_ws()).pack(side="left", padx=4)
    ttk.Label(wsf, text="이미지 = 03_ai_images, 원본 = 01_plate, 영상 폴더 = 04_video",
              foreground="#888888").pack(side="left", padx=6)

    def ws_path(sub):
        w = ws_var.get().strip().strip('"')
        return os.path.join(w, sub) if w else ""

    def apply_ws():
        w = ws_var.get().strip().strip('"')
        if not w or not os.path.isdir(w):
            messagebox.showwarning(APP_NAME, "작업 폴더가 없습니다:\n%s" % w, parent=root)
            return
        ai, plate = ws_path(WS_AI), ws_path(WS_PLATE)
        os.makedirs(ai, exist_ok=True)
        src_list.delete(0, "end")
        src_list.insert("end", ai)
        plate_var.set(plate if os.path.isdir(plate) else "")
        load_folder()
    r4 = ttk.Frame(top)
    r4.grid(row=2, column=0, columnspan=4, sticky="we", pady=(4, 0))
    ttk.Button(r4, text="불러오기 / 새로고침", command=lambda: load_folder()).pack(side="left")
    ttk.Label(r4, text="   샷 이름 = 파일 이름 앞").pack(side="left")
    sp = ttk.Spinbox(r4, from_=1, to=8, width=3, textvariable=parts_var, command=lambda: load_folder())
    sp.pack(side="left", padx=2)
    ttk.Label(r4, text="조각 ('_' 로 나눔)").pack(side="left")
    parts_hint = ttk.Label(r4, text="", foreground="#888888")
    parts_hint.pack(side="left", padx=8)
    help_btn = ttk.Button(r4, text="?", width=3, command=lambda: show_help())
    help_btn.pack(side="right")
    upd_btn = ttk.Button(r4, text="업데이트 확인", command=lambda: check_updates(True))
    upd_btn.pack(side="right", padx=6)
    ttk.Label(r4, text="v" + APP_VERSION, foreground="#888888").pack(side="right")

    # ---- 가운데: 샷 목록 | 썸네일 ----
    pane = ttk.Panedwindow(root, orient="horizontal")
    pane.pack(fill="both", expand=True, padx=8, pady=4)
    left = ttk.Frame(pane)
    shot_tree = ttk.Treeview(left, columns=("n", "p"), show="tree headings", selectmode="browse", height=20)
    shot_tree.heading("#0", text="샷")
    shot_tree.heading("n", text="장수")
    shot_tree.heading("p", text="고름")
    shot_tree.column("#0", width=170, stretch=True)
    shot_tree.column("n", width=46, anchor="e", stretch=False)
    shot_tree.column("p", width=46, anchor="e", stretch=False)
    shot_tree.tag_configure("has", foreground="#1a8f2e")
    ssb = ttk.Scrollbar(left, orient="vertical", command=shot_tree.yview)
    shot_tree.configure(yscrollcommand=ssb.set)
    shot_tree.pack(side="left", fill="both", expand=True)
    ssb.pack(side="left", fill="y")
    pane.add(left, weight=0)

    right = ttk.Frame(pane)
    bar = ttk.Frame(right)
    bar.pack(fill="x")
    shot_lbl = ttk.Label(bar, text="", font=("TkDefaultFont", 11, "bold"))
    shot_lbl.pack(side="left")
    orig_var = tk.BooleanVar(value=bool(settings.get("show_orig2", False)))  # 처음엔 AI 이미지만 크게
    ttk.Checkbutton(bar, text="원본 보기 (O)", variable=orig_var, command=lambda: on_orig()).pack(side="left", padx=14)
    ttk.Button(bar, text="이 샷 전부 고르기", command=lambda: pick_all(True)).pack(side="left")
    ttk.Button(bar, text="이 샷 고르기 해제", command=lambda: pick_all(False)).pack(side="left", padx=4)
    size_var = tk.IntVar(value=int(settings.get("thumb", 260)))
    ttk.Label(bar, text="크기").pack(side="right")
    size_scale = ttk.Scale(bar, from_=140, to=520, variable=size_var, length=160,
                           command=lambda v: root.after_idle(on_size))
    size_scale.pack(side="right", padx=4)
    view_btn = ttk.Button(bar, text="썸네일 모아 보기 (G)", command=lambda: toggle_view())
    view_btn.pack(side="right", padx=8)
    # 크게 보기 아래 썸네일 줄 (같은 샷 이미지들)
    SW, SH = 150, 84
    stripf = ttk.Frame(right)
    strip = tk.Canvas(stripf, bg="#151515", height=SH + 30, highlightthickness=0)
    strip_sb = ttk.Scrollbar(stripf, orient="horizontal", command=strip.xview)
    strip.configure(xscrollcommand=strip_sb.set)
    strip.pack(fill="x")
    strip_sb.pack(fill="x")
    stripf.pack(side="bottom", fill="x")
    cwrap = ttk.Frame(right)
    cwrap.pack(fill="both", expand=True)
    canvas = tk.Canvas(cwrap, bg=BG, highlightthickness=0, takefocus=1)
    csb = ttk.Scrollbar(cwrap, orient="vertical", command=canvas.yview)
    canvas.configure(yscrollcommand=csb.set)
    canvas.pack(side="left", fill="both", expand=True)
    csb.pack(side="left", fill="y")
    pane.add(right, weight=1)

    # ---- 아래: 저장 ----
    bot = ttk.Frame(root, padding=(8, 2, 8, 8))
    bot.pack(fill="x")
    save_btn = ttk.Button(bot, text="저장  (pick / not_pick 로 복사)", command=lambda: do_save())
    save_btn.pack(side="left")
    ttk.Button(bot, text="영상 폴더 만들기 (04_video)", command=lambda: do_video()).pack(side="left", padx=(6, 0))
    empty_var = tk.BooleanVar(value=bool(settings.get("include_empty", False)))
    ttk.Checkbutton(bot, text="고른 게 없는 샷도 not_pick 으로", variable=empty_var).pack(side="left", padx=12)
    ttk.Button(bot, text="이미지 폴더 열기", command=lambda: open_folder(out_dir()) if os.path.isdir(out_dir()) else None
               ).pack(side="left")
    prog = ttk.Progressbar(bot, length=180, mode="determinate")
    prog.pack(side="left", padx=10)
    status = ttk.Label(bot, text="이미지 폴더를 고르세요. (창에 폴더를 끌어다 놓아도 됨)")
    status.pack(side="left", fill="x", expand=True)

    def set_status(t):
        status.config(text=t)

    def out_dir():
        """저장은 첫 번째 이미지 폴더 안 (pick / not_pick)"""
        f = folder_list()
        return st["folder"] or (f[0] if f else "")

    # =====================================================================
    # 불러오기 / 기억
    # =====================================================================
    def picked_store():
        """고른 이미지 = 전체 경로 목록 (폴더가 여러 개여도 한 곳에 기억)"""
        if "picked" not in settings:
            old = []
            for folder, rels in (settings.get("picks") or {}).items():  # v1.0.0 기억 옮겨 오기
                old += [norm(os.path.join(folder, r)) for r in rels]
            settings["picked"] = sorted(set(old))
        return settings["picked"]

    def remember_picks():
        keep = [p for p in picked_store() if p not in st["loaded"]]
        cur = [norm(p) for v in st["picks"].values() for p in v]  # 샷마다 1위, 2위, ... 순서
        ks = set(keep)
        settings["picked"] = keep + [c for c in cur if c not in ks]
        settings.update(srcs=folder_list(), workspace=ws_var.get().strip(), plate=plate_var.get(), parts=parts_var.get(),
                        show_orig2=orig_var.get(), include_empty=empty_var.get())
        settings.pop("src", None)
        save_settings(settings)

    save_job = [None]

    def remember_soon():
        if save_job[0]:
            root.after_cancel(save_job[0])
        save_job[0] = root.after(800, remember_picks)

    def load_folder():
        folders = [d for d in folder_list() if d]
        missing = [d for d in folders if not os.path.isdir(d)]
        if missing:
            messagebox.showwarning(APP_NAME, "없는 폴더가 있어서 빼고 불러옵니다:\n%s" % "\n".join(missing), parent=root)
            folders = [d for d in folders if os.path.isdir(d)]
        try:
            parts = max(1, int(parts_var.get()))
        except Exception:
            parts = 3
        if st["loaded"]:
            remember_picks()
        dest = folders[0] if folders else None
        skip = []
        for d in folders:  # 저장한 결과(pick / not_pick)는 다시 안 읽음
            skip += [os.path.join(d, PICK_DIR), os.path.join(d, NOPICK_DIR)]
        set_status("이미지 찾는 중…")
        root.update_idletasks()
        seen, paths = set(), []
        for d in folders:
            for p in scan_images(d, skip):
                if norm(p) not in seen:  # 폴더가 겹쳐도 한 번만
                    seen.add(norm(p))
                    paths.append(p)
        st["folder"], st["folders"], st["loaded"] = dest, folders, seen
        st["groups"] = group_by_shot(paths, parts)
        st["shots"] = list(st["groups"])
        st["plates"] = find_plates(plate_var.get().strip().strip('"'), parts)
        bypath = {norm(p): p for p in paths}
        st["picks"] = {}
        for n in picked_store():  # 저장된 순서 = 순위
            p = bypath.get(n)
            if p:
                lst = st["picks"].setdefault(shot_of(p, parts), [])
                if p not in lst:
                    lst.append(p)
        st["videos"] = find_videos(plate_var.get().strip().strip('"'), parts)
        if paths:
            ex = os.path.basename(paths[0])
            parts_hint.config(text="예) %s  ->  %s" % (ex, shot_of(ex, parts)))
        fill_shots()
        n_plate = sum(1 for x in st["shots"] if x in st["plates"])
        set_status("폴더 %d개, 샷 %d개, 이미지 %d장%s" % (len(folders), len(st["shots"]), len(paths),
                   (", 원본 있는 샷 %d개" % n_plate) if plate_var.get().strip() else ""))
        want = st["shot"] if st["shot"] in st["groups"] else (st["shots"][0] if st["shots"] else None)
        if want:
            select_shot(want, keep=True)
        else:
            st["shot"] = None
            st["tiles"] = []
            redraw()
        remember_soon()

    def fill_shots():
        shot_tree.delete(*shot_tree.get_children())
        for s in st["shots"]:
            n = len(st["groups"][s])
            p = len(st["picks"].get(s, ()))
            shot_tree.insert("", "end", iid=s, text=s, values=(n, p or ""), tags=("has",) if p else ())

    def update_shot_row(s):
        if shot_tree.exists(s):
            p = len(st["picks"].get(s, ()))
            shot_tree.item(s, values=(len(st["groups"][s]), p or ""), tags=("has",) if p else ())
        total = sum(len(v) for v in st["picks"].values())
        shot_lbl.config(text="%s   %d장 중 %d장 고름   (전체 %d장)" % (
            s, len(st["groups"].get(s, ())), len(st["picks"].get(s, ())), total))

    def select_shot(s, keep=False):
        if s not in st["groups"]:
            return
        cur = st["tiles"][st["focus"]] if (keep and st["tiles"] and st["focus"] < len(st["tiles"])) else None
        st["shot"] = s
        st["flip"] = False
        st["tiles"] = tile_list()
        # 크게 보기는 원본 칸이 아니라 첫 AI 이미지부터
        st["focus"] = st["tiles"].index(cur) if cur in st["tiles"] else next(
            (i for i, t in enumerate(st["tiles"]) if t[0] == "ai"), 0)
        if shot_tree.selection() != (s,):
            shot_tree.selection_set(s)
        shot_tree.see(s)
        canvas.yview_moveto(0)
        update_shot_row(s)
        redraw()
        canvas.focus_set()

    shot_tree.bind("<<TreeviewSelect>>", lambda e: (shot_tree.selection() and shot_tree.selection()[0] != st["shot"]
                                                    and select_shot(shot_tree.selection()[0])))

    def go_shot(d):
        if not st["shots"]:
            return
        i = st["shots"].index(st["shot"]) if st["shot"] in st["shots"] else 0
        select_shot(st["shots"][max(0, min(len(st["shots"]) - 1, i + d))])

    # =====================================================================
    # 고르기
    # =====================================================================
    def is_picked(p):
        return p in (st["picks"].get(st["shot"]) or ())

    def pick_rank(p):
        lst = st["picks"].get(st["shot"]) or []
        return lst.index(p) + 1 if p in lst else 0

    def toggle_pick(p):
        """고르면 맨 뒤 순위로, 다시 누르면 빠지고 뒤 순위가 하나씩 올라감"""
        lst = st["picks"].setdefault(st["shot"], [])
        if p in lst:
            lst.remove(p)
        else:
            lst.append(p)
        update_shot_row(st["shot"])
        remember_soon()

    def set_rank(p, n):
        """숫자 키: 이 이미지를 n위로 (안 고른 이미지면 고르면서 n위)"""
        lst = st["picks"].setdefault(st["shot"], [])
        if p in lst:
            lst.remove(p)
        lst.insert(max(0, min(len(lst), n - 1)), p)
        update_shot_row(st["shot"])
        remember_soon()

    def pick_all(on):
        if not st["shot"]:
            return
        st["picks"][st["shot"]] = list(st["groups"][st["shot"]]) if on else []
        update_shot_row(st["shot"])
        redraw()
        remember_soon()

    # =====================================================================
    # 썸네일 그리드
    # =====================================================================
    def tile_list():
        """[("orig", 경로) 또는 ("ai", 경로)]"""
        s = st["shot"]
        if not s:
            return []
        tiles = []
        if orig_var.get() and s in st["plates"]:
            tiles.append(("orig", st["plates"][s]))
        tiles += [("ai", p) for p in st["groups"][s]]
        return tiles

    def geom():
        ts = int(size_var.get())
        ih = int(ts * 0.62)
        gap, lab = 10, 20
        W = max(canvas.winfo_width(), ts + 2 * gap)
        cols = max(1, (W - gap) // (ts + gap))
        return ts, ih, gap, lab, cols

    def x_off():
        ts, ih, gap, lab, cols = geom()
        return max(gap, (canvas.winfo_width() - cols * (ts + gap) + gap) // 2)  # 가운데 정렬

    def tile_xy(i):
        ts, ih, gap, lab, cols = geom()
        r, c = divmod(i, cols)
        return x_off() + c * (ts + gap), gap + r * (ih + lab + gap + 6)

    def tile_at(x, y):
        ts, ih, gap, lab, cols = geom()
        x, y = canvas.canvasx(x), canvas.canvasy(y)
        c = int((x - x_off()) // (ts + gap))
        r = int((y - gap) // (ih + lab + gap + 6))
        if c < 0 or c >= cols or r < 0:
            return None
        x0, y0 = tile_xy(r * cols + c)
        if not (x0 <= x <= x0 + ts and y0 <= y <= y0 + ih + lab + 6):
            return None
        i = r * cols + c
        return i if i < len(st["tiles"]) else None

    from tkinter import font as tkfont
    label_font = tkfont.nametofont("TkDefaultFont")

    def short(name, ts):
        """칸 너비에 맞게 앞쪽을 줄임 (뒤쪽 번호가 보이게)"""
        room = ts - 12
        if label_font.measure(name) <= room:
            return name
        lo, hi = 1, len(name)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if label_font.measure("…" + name[-mid:]) <= room:
                lo = mid
            else:
                hi = mid - 1
        return "…" + name[-lo:]

    def draw_tile(i):
        kind, p = st["tiles"][i]
        ts, ih, gap, lab, cols = geom()
        x, y = tile_xy(i)
        canvas.delete("t%d" % i)
        tg = ("t%d" % i,)
        picked = kind == "ai" and is_picked(p)
        focus = (i == st["focus"])
        outline = ORIG if kind == "orig" else (PICK if picked else (FOCUS if focus else "#3a3a3a"))
        width = 4 if (picked or kind == "orig") else (2 if focus else 1)
        canvas.create_rectangle(x, y, x + ts, y + ih + lab + 6, fill=TILE_BG, outline="", tags=tg)
        ph = st["thumbs"].get((p, ts, ih))
        if ph is not None:
            canvas.create_image(x + ts // 2, y + 3 + ih // 2, image=ph, tags=tg)
        else:
            canvas.create_text(x + ts // 2, y + ih // 2, text="…", fill="#777777", tags=tg)
            want_thumb(p, ts, ih)
        canvas.create_rectangle(x, y, x + ts, y + ih + lab + 6, outline=outline, width=width, tags=tg)
        if focus and (picked or kind == "orig"):  # 고른 칸에서도 지금 위치가 보이게 안쪽에 파란 선
            canvas.create_rectangle(x + 5, y + 5, x + ts - 5, y + ih + lab + 1, outline=FOCUS, width=2, tags=tg)
        if kind == "orig":
            canvas.create_text(x + 8, y + 8, anchor="nw", text="ORIGINAL", fill=ORIG,
                               font=("TkDefaultFont", 10, "bold"), tags=tg)
        elif picked:
            canvas.create_rectangle(x + 6, y + 6, x + 76, y + 28, fill=PICK, outline="", tags=tg)
            canvas.create_text(x + 41, y + 17, text="PICK %d" % pick_rank(p), fill="#000000",
                               font=("TkDefaultFont", 11, "bold"), tags=tg)
        canvas.create_text(x + 6, y + ih + 6 + lab // 2, anchor="w", text=short(os.path.basename(p), ts),
                           fill="#dddddd" if kind == "ai" else ORIG, tags=tg)

    def draw_grid():
        canvas.delete("all")
        st["tiles"] = tile_list()
        ts, ih, gap, lab, cols = geom()
        st["wanted"] = set()
        if not st["tiles"]:
            canvas.create_text(20, 20, anchor="nw", fill="#888888",
                               text="이미지 폴더를 고르세요." if not st["shots"] else "", tags=("msg",))
            canvas.configure(scrollregion=(0, 0, 1, 1))
            return
        st["focus"] = max(0, min(st["focus"], len(st["tiles"]) - 1))
        for i in range(len(st["tiles"])):
            draw_tile(i)
        rows = (len(st["tiles"]) + cols - 1) // cols
        canvas.configure(scrollregion=(0, 0, canvas.winfo_width(), gap + rows * (ih + lab + gap + 6) + gap))

    def want_thumb(p, ts, ih):
        key = (p, ts, ih)
        st["wanted"].add(key)
        thumb_q.put(key)

    def thumb_worker():
        while True:
            key = thumb_q.get()
            if key not in st["wanted"] or key in st["thumbs"]:
                continue
            p, w, h = key
            try:
                im = load_thumb(p, w, h)
            except Exception:
                im = None
            q.put(("thumb", (key, im)))

    for _ in range(3):
        threading.Thread(target=thumb_worker, daemon=True).start()

    def see_focus():
        if st["mode"] != "grid" or not st["tiles"]:
            return
        ts, ih, gap, lab, cols = geom()
        x, y = tile_xy(st["focus"])
        total = float(canvas.bbox("all")[3] if canvas.bbox("all") else 1) + gap
        top_y, h = canvas.canvasy(0), canvas.winfo_height()
        if y < top_y:
            canvas.yview_moveto(max(0, (y - gap) / total))
        elif y + ih + lab + 6 > top_y + h:
            canvas.yview_moveto(max(0, (y + ih + lab + 6 + gap - h) / total))

    def set_focus(i, redraw_old=True):
        if not st["tiles"]:
            return
        i = max(0, min(len(st["tiles"]) - 1, i))
        old, st["focus"] = st["focus"], i
        if st["mode"] == "grid":
            if redraw_old and old < len(st["tiles"]):
                draw_tile(old)
            draw_tile(i)
            see_focus()

    # =====================================================================
    # 크게 보기
    # =====================================================================
    full_cache = {}

    def fit_image(p, w, h):
        key = (p, w, h)
        if key in full_cache:
            return full_cache[key]
        try:
            im = load_thumb(p, w, h)
        except Exception:
            return None
        if len(full_cache) > 12:
            full_cache.pop(next(iter(full_cache)))
        full_cache[key] = ImageTk.PhotoImage(im)
        return full_cache[key]

    def draw_view():
        canvas.delete("all")
        canvas.configure(scrollregion=(0, 0, 1, 1))
        canvas.yview_moveto(0)
        st["tiles"] = tile_list()
        ais = [i for i, t in enumerate(st["tiles"]) if t[0] == "ai"]
        if not ais:  # 아직 불러온 이미지가 없음 (모드는 그대로 둠)
            canvas.create_text(20, 20, anchor="nw", fill="#888888",
                               text="이미지 폴더를 추가하세요. (폴더 추가... 또는 창에 끌어다 놓기)" if not st["shots"] else "")
            return
        if st["tiles"][st["focus"]][0] != "ai":
            st["focus"] = ais[0]
        p = st["tiles"][st["focus"]][1]
        W, H = canvas.winfo_width(), canvas.winfo_height()
        plate = st["plates"].get(st["shot"])
        side = orig_var.get() and plate and not st["flip"]
        top_h = 30
        picked = is_picked(p)
        if side:
            w2 = (W - 30) // 2
            for x0, path, label, col in ((10, plate, "ORIGINAL", ORIG), (20 + w2, p, "AI", PICK if picked else "#dddddd")):
                ph = fit_image(path, w2, H - top_h - 16)
                if ph:
                    canvas.create_image(x0 + w2 // 2, top_h + (H - top_h) // 2, image=ph)
                canvas.create_text(x0 + 4, top_h + 4, anchor="nw", text=label, fill=col, font=("TkDefaultFont", 11, "bold"))
            if picked:
                canvas.create_rectangle(20 + w2 - 2, top_h - 2, W - 8, H - 6, outline=PICK, width=4)
        else:
            path = plate if (st["flip"] and plate) else p
            ph = fit_image(path, W - 20, H - top_h - 16)
            if ph:
                canvas.create_image(W // 2, top_h + (H - top_h) // 2, image=ph)
            if st["flip"] and plate:
                canvas.create_text(14, top_h + 4, anchor="nw", text="ORIGINAL  (F 로 돌아가기)", fill=ORIG,
                                   font=("TkDefaultFont", 13, "bold"))
            if picked and not st["flip"]:
                canvas.create_rectangle(8, top_h - 2, W - 8, H - 6, outline=PICK, width=4)
        n = ais.index(st["focus"]) + 1
        canvas.create_text(10, 6, anchor="nw", fill=PICK if picked else "#dddddd", font=("TkDefaultFont", 11, "bold"),
                           text="%s%d / %d   %s" % (("✓ PICK %d위   " % pick_rank(p)) if picked else "", n, len(ais),
                                                      os.path.basename(p)))
        canvas.create_text(W - 10, 6, anchor="ne", fill="#888888",
                           text="← → 이미지   ↓ ↑ 샷   클릭·스페이스 고르기   O 원본 나란히   F 원본으로 바꿔 보기   G 모아 보기")

    def toggle_view(on=None):
        if not st["tiles"]:
            return
        on = (st["mode"] != "view") if on is None else on
        st["mode"] = "view" if on else "grid"
        st["flip"] = False
        settings["mode"] = st["mode"]
        redraw()
        if not on:
            root.after_idle(see_focus)

    def view_step(d):
        ais = [i for i, t in enumerate(st["tiles"]) if t[0] == "ai"]
        if not ais:
            return
        k = ais.index(st["focus"]) if st["focus"] in ais else 0
        st["focus"] = ais[max(0, min(len(ais) - 1, k + d))]
        st["flip"] = False
        draw_view()
        draw_strip()

    def on_orig():
        """원본 칸이 생기거나 빠져도 보고 있던 이미지가 그대로 보이게"""
        cur = st["tiles"][st["focus"]] if st["tiles"] and st["focus"] < len(st["tiles"]) else None
        st["tiles"] = tile_list()
        if cur in st["tiles"]:
            st["focus"] = st["tiles"].index(cur)
        settings["show_orig2"] = orig_var.get()
        redraw()
        canvas.focus_set()

    def redraw():
        if st["mode"] == "view":
            if not stripf.winfo_ismapped():
                stripf.pack(side="bottom", fill="x", before=cwrap)
            csb.pack_forget()
            draw_view()
            draw_strip()
        else:
            stripf.pack_forget()
            if not csb.winfo_ismapped():
                csb.pack(side="left", fill="y")
            draw_grid()
        view_btn.config(text="크게 보기 (Enter)" if st["mode"] == "grid" else "썸네일 모아 보기 (G)")

    # ---- 썸네일 줄 ----
    def strip_x(i):
        return 8 + i * (SW + 8)

    def draw_strip_tile(i):
        if i >= len(st["tiles"]):
            return
        kind, p = st["tiles"][i]
        x, y = strip_x(i), 6
        tg = ("s%d" % i,)
        strip.delete("s%d" % i)
        picked = kind == "ai" and is_picked(p)
        focus = i == st["focus"]
        strip.create_rectangle(x, y, x + SW, y + SH, fill=TILE_BG, outline="", tags=tg)
        ph = st["thumbs"].get((p, SW, SH))
        if ph is not None:
            strip.create_image(x + SW // 2, y + SH // 2, image=ph, tags=tg)
        else:
            want_thumb(p, SW, SH)
        col = ORIG if kind == "orig" else (PICK if picked else (FOCUS if focus else "#3a3a3a"))
        strip.create_rectangle(x, y, x + SW, y + SH, outline=col, width=4 if (picked or kind == "orig" or focus) else 1,
                               tags=tg)
        if focus and (picked or kind == "orig"):
            strip.create_rectangle(x + 5, y + 5, x + SW - 5, y + SH - 5, outline=FOCUS, width=2, tags=tg)
        if kind == "orig":
            strip.create_text(x + 6, y + 4, anchor="nw", text="ORIGINAL", fill=ORIG, font=("TkDefaultFont", 9, "bold"),
                              tags=tg)
        elif picked:
            strip.create_rectangle(x + 4, y + 4, x + 58, y + 22, fill=PICK, outline="", tags=tg)
            strip.create_text(x + 31, y + 13, text="PICK %d" % pick_rank(p), fill="#000000",
                              font=("TkDefaultFont", 10, "bold"), tags=tg)
        n = sum(1 for t in st["tiles"][:i + 1] if t[0] == "ai")
        strip.create_text(x + SW // 2, y + SH + 11, text=("원본" if kind == "orig" else str(n)),
                          fill=ORIG if kind == "orig" else ("#ffffff" if focus else "#999999"), tags=tg)

    def draw_strip():
        strip.delete("all")
        for i in range(len(st["tiles"])):
            draw_strip_tile(i)
        total = strip_x(len(st["tiles"])) + 8
        strip.configure(scrollregion=(0, 0, max(total, strip.winfo_width()), SH + 30))
        # 지금 보는 이미지가 줄 가운데쯤 보이게
        if st["tiles"] and total > strip.winfo_width():
            cx = strip_x(st["focus"]) + SW / 2.0 - strip.winfo_width() / 2.0
            strip.xview_moveto(max(0.0, cx / total))

    def on_strip_click(e):
        i = int((strip.canvasx(e.x) - 8) // (SW + 8))
        if 0 <= i < len(st["tiles"]) and st["tiles"][i][0] == "ai":
            st["focus"] = i
            st["flip"] = False
            draw_view()
            draw_strip()
        canvas.focus_set()

    strip.bind("<ButtonPress-1>", on_strip_click)

    # =====================================================================
    # 마우스 / 키보드
    # =====================================================================
    def on_click(e):
        canvas.focus_set()
        if st["mode"] == "view":
            if st["tiles"] and st["tiles"][st["focus"]][0] == "ai" and not st["flip"]:
                toggle_pick(st["tiles"][st["focus"]][1])
                draw_view()
                draw_strip_tile(st["focus"])
            return
        if st["mode"] != "grid":
            return
        i = tile_at(e.x, e.y)
        if i is None:
            return
        kind, p = st["tiles"][i]
        old, st["focus"] = st["focus"], i
        if kind == "ai":
            toggle_pick(p)
        if old != i and old < len(st["tiles"]):
            draw_tile(old)
        draw_tile(i)

    def on_double(e):
        if st["mode"] == "view":
            if st["tiles"] and st["tiles"][st["focus"]][0] == "ai" and not st["flip"]:
                toggle_pick(st["tiles"][st["focus"]][1])  # 첫 클릭에서 바뀐 고르기를 되돌림
            toggle_view(False)
            return
        i = tile_at(e.x, e.y)
        if i is None:
            return
        kind, p = st["tiles"][i]
        if kind == "ai":
            toggle_pick(p)  # 더블클릭의 첫 클릭에서 바뀐 고르기를 되돌림
            st["focus"] = i
            toggle_view(True)

    def on_wheel(e):
        delta = e.delta if e.delta else (120 if getattr(e, "num", 0) == 4 else -120)
        if st["mode"] == "view":  # 크게 보기: 휠 = 이전 / 다음 이미지
            view_step(-1 if delta > 0 else 1)
            return "break"
        if e.state & (0x0008 if IS_MAC else 0x0004):  # Ctrl/Cmd + 휠 = 크기
            size_var.set(max(140, min(520, size_var.get() + (20 if delta > 0 else -20))))
            on_size()
            return "break"
        step = -1 if delta > 0 else 1
        if IS_MAC:
            step = -delta if abs(delta) < 10 else step
        canvas.yview_scroll(int(step), "units")

    def on_size():
        st["thumbs"].clear()
        if st["mode"] == "grid":
            draw_grid()
            see_focus()

    def typing(e):
        return isinstance(e.widget, (tk.Entry, ttk.Entry, ttk.Spinbox))

    def on_key(e):
        if typing(e):
            return None
        ks = e.keysym
        mod = bool(e.state & (0x0008 if IS_MAC else 0x0004))
        letter = shortcut_letter(e.keysym, e.char, e.keycode)
        if mod:
            if letter == "s":
                do_save()
                return "break"
            if letter == "a" and st["shot"]:
                pick_all(True)
                return "break"
            return None
        if e.widget is shot_tree and ks in ("Up", "Down", "Prior", "Next", "Home", "End"):
            return None  # 샷 목록 안에서는 목록이 직접
        if ks in ("bracketleft", "Prior"):
            go_shot(-1)
            return "break"
        if ks in ("bracketright", "Next"):
            go_shot(1)
            return "break"
        if letter == "o":
            orig_var.set(not orig_var.get())
            on_orig()
            return "break"
        if not st["tiles"]:
            return None
        if letter == "g":
            toggle_view()
            return "break"
        if e.char and e.char in "123456789" and st["tiles"] and st["tiles"][st["focus"]][0] == "ai":
            set_rank(st["tiles"][st["focus"]][1], int(e.char))  # 숫자 = 그 순위로 고르기
            redraw()
            return "break"
        if st["mode"] == "view":
            if ks == "Left":
                view_step(-1)
            elif ks == "Right":
                view_step(1)
            elif ks == "Down":      # ↓ = 다음 샷
                go_shot(1)
            elif ks == "Up":        # ↑ = 이전 샷
                go_shot(-1)
            elif ks in ("space",) or letter == "p":
                toggle_pick(st["tiles"][st["focus"]][1])
                draw_view()
            elif letter == "f":
                st["flip"] = not st["flip"]
                draw_view()
            elif ks in ("Escape", "Return", "KP_Enter"):
                toggle_view(False)
            else:
                return None
            return "break"
        ts, ih, gap, lab, cols = geom()
        if ks == "Left":
            set_focus(st["focus"] - 1)
        elif ks == "Right":
            set_focus(st["focus"] + 1)
        elif ks == "Up":
            set_focus(st["focus"] - cols)
        elif ks == "Down":
            set_focus(st["focus"] + cols)
        elif ks == "Home":
            set_focus(0)
        elif ks == "End":
            set_focus(len(st["tiles"]) - 1)
        elif ks == "space" or letter == "p":
            kind, p = st["tiles"][st["focus"]]
            if kind == "ai":
                toggle_pick(p)
                draw_tile(st["focus"])
        elif ks in ("Return", "KP_Enter"):
            toggle_view(True)
        else:
            return None
        return "break"

    canvas.bind("<ButtonPress-1>", on_click)
    canvas.bind("<Double-Button-1>", on_double)
    canvas.bind("<MouseWheel>", on_wheel)
    canvas.bind("<Button-4>", on_wheel)
    canvas.bind("<Button-5>", on_wheel)
    canvas.bind("<Configure>", lambda e: root.after_idle(redraw))
    root.bind("<KeyPress>", on_key)

    # =====================================================================
    # 저장
    # =====================================================================
    def cur_parts():
        try:
            return max(1, int(parts_var.get()))
        except Exception:
            return 3

    def do_save():
        if st["busy"] or not st["groups"]:
            return
        od = out_dir()
        if not od:
            return
        plan, removals, skipped = plan_save(st["groups"], st["picks"], od, empty_var.get(), cur_parts())
        if not plan:
            messagebox.showinfo(APP_NAME, "고른 이미지가 없습니다.\n썸네일을 클릭해서 고른 뒤 저장하세요.", parent=root)
            return
        npick = sum(1 for _, d in plan if os.path.basename(os.path.dirname(d)) == PICK_DIR)
        msg = ("저장할까요?\n\n  pick      %d장\n  not_pick  %d장\n\n저장 위치: %s 안의 pick / not_pick"
               % (npick, len(plan) - npick, od))
        if skipped:
            msg += "\n\n고른 게 없어서 건너뛰는 샷 %d개: %s%s" % (
                len(skipped), ", ".join(skipped[:6]), " …" if len(skipped) > 6 else "")
        if not messagebox.askyesno(APP_NAME, msg, parent=root):
            return
        remember_picks()
        st["busy"] = True
        save_btn.config(state="disabled")

        def work():
            try:
                c, r = run_save(plan, removals, lambda d, t: q.put(("prog", (d, t))))
                q.put(("saved", (c, r, npick, len(plan) - npick, od)))
            except Exception as e:
                q.put(("save_err", str(e)))
        threading.Thread(target=work, daemon=True).start()

    def do_video():
        """고른 게 있는 샷마다 04_video/샷/ 에 원본 영상 + 고른 이미지(순위 이름) 를 모음"""
        if st["busy"] or not st["groups"]:
            return
        out_root = ws_path(WS_VIDEO)
        if not out_root:
            d = filedialog.askdirectory(parent=root, title="영상 폴더(04_video)를 만들 곳",
                                        initialdir=os.path.dirname(out_dir() or "") or os.path.expanduser("~"))
            if not d:
                return
            out_root = d if os.path.basename(d) == WS_VIDEO else os.path.join(d, WS_VIDEO)
        plan, removals, done, no_video = plan_video(st["groups"], st["picks"], st.get("videos") or {},
                                                    out_root, cur_parts())
        if not done:
            messagebox.showinfo(APP_NAME, "고른 이미지가 있는 샷이 없습니다.", parent=root)
            return
        msg = "영상 폴더를 만들까요?\n\n  샷 %d개 -> %s\n  (샷마다 원본 영상 + 고른 이미지, 이름에 순위 _r01_ ...)" % (
            len(done), out_root)
        if no_video:
            msg += ("\n\n원본 영상을 못 찾은 샷 %d개 (이미지만 넣음): %s%s\n원본 플레이트 폴더를 확인하세요."
                    % (len(no_video), ", ".join(no_video[:6]), " …" if len(no_video) > 6 else ""))
        if not messagebox.askyesno(APP_NAME, msg, parent=root):
            return
        remember_picks()
        st["busy"] = True
        save_btn.config(state="disabled")

        def work():
            try:
                c, r = run_save(plan, removals, lambda d, t: q.put(("prog", (d, t))))
                q.put(("video_done", (len(done), len(no_video), c, out_root)))
            except Exception as e:
                q.put(("save_err", str(e)))
        threading.Thread(target=work, daemon=True).start()

    # =====================================================================
    # 업데이트
    # =====================================================================
    upd = {"busy": False, "info": None}

    def check_updates(manual=False):
        if upd["busy"]:
            return
        if upd["info"] and manual:
            ask_update(upd["info"])
            return

        def work():
            try:
                q.put(("upd_found", (manual, check_update())))
            except Exception as e:
                q.put(("upd_err", (manual, str(e)[:300])))
        threading.Thread(target=work, daemon=True).start()

    def ask_update(info):
        if not info.get("asset"):
            messagebox.showinfo("업데이트", "새 버전 v%s 이 있지만 이 컴퓨터용 설치 파일이 아직 없습니다.\n%s"
                                % (info["version"], info.get("url", "")), parent=root)
            return
        notes = "\n".join((info.get("notes") or "").strip().splitlines()[:12])
        if not messagebox.askyesno("업데이트", "새 버전 v%s 이 있습니다. (지금 v%s)\n\n%s\n\n지금 업데이트할까요?\n"
                                   "프로그램이 잠시 닫혔다가 새 버전으로 다시 켜집니다. (고른 내용은 그대로)"
                                   % (info["version"], APP_VERSION, notes), parent=root):
            return
        if not getattr(sys, "frozen", False):
            import webbrowser
            webbrowser.open(info.get("url") or "https://github.com/%s/releases" % GH_REPO)
            return
        upd["busy"] = True
        upd_btn.config(state="disabled")
        asset = info["asset"]

        def work():
            dest = os.path.join(tempfile.mkdtemp(prefix="sp_update_"), asset.get("name") or "update.bin")
            try:
                download_asset(asset, dest, lambda d, t: q.put(("upd_prog", (d, t))))
            except Exception as e:
                q.put(("upd_fail", "다운로드 실패: %s" % str(e)[:300]))
                return
            q.put(("upd_ready", dest))
        threading.Thread(target=work, daemon=True).start()

    # =====================================================================
    # 메시지 처리
    # =====================================================================
    def poll():
        try:
            for _ in range(60):
                kind, data = q.get_nowait()
                if kind == "thumb":
                    key, im = data
                    if im is not None and key in st["wanted"]:
                        st["thumbs"][key] = ImageTk.PhotoImage(im)
                        for i, (k, p) in enumerate(st["tiles"]):
                            if p == key[0]:
                                if st["mode"] == "grid" and key[1:] != (SW, SH):
                                    draw_tile(i)
                                elif st["mode"] == "view" and key[1:] == (SW, SH):
                                    draw_strip_tile(i)
                elif kind == "prog":
                    d, t = data
                    prog.config(maximum=t, value=d)
                    set_status("저장 중… %d / %d" % (d, t))
                elif kind == "saved":
                    c, r, npk, nno, od = data
                    st["busy"] = False
                    save_btn.config(state="normal")
                    set_status("저장 완료: pick %d장, not_pick %d장 (새로 복사 %d, 정리 %d)" % (npk, nno, c, r))
                    if messagebox.askyesno(APP_NAME, "저장했습니다.\n\npick %d장 / not_pick %d장\n\n폴더를 열까요?"
                                           % (npk, nno), parent=root):
                        open_folder(od)
                elif kind == "video_done":
                    n, nv, c, out_root = data
                    st["busy"] = False
                    save_btn.config(state="normal")
                    set_status("영상 폴더 %d개 완료 (새로 복사 %d)%s" % (n, c, (", 원본 영상 없음 %d" % nv) if nv else ""))
                    if messagebox.askyesno(APP_NAME, "영상 폴더를 만들었습니다. (샷 %d개)\n\n%s\n\n"
                                           "다음: 이 폴더를 Claude 에게 주면 샷마다 prompt.txt 를 만들어 줍니다.\n\n"
                                           "폴더를 열까요?" % (n, out_root), parent=root):
                        open_folder(out_root)
                elif kind == "save_err":
                    st["busy"] = False
                    save_btn.config(state="normal")
                    messagebox.showerror(APP_NAME, "저장하다 문제가 생겼습니다.\n\n%s" % data, parent=root)
                elif kind == "upd_found":
                    manual, info = data
                    if not info:
                        if manual:
                            messagebox.showinfo("업데이트", "최신 버전입니다. (v%s)" % APP_VERSION, parent=root)
                    else:
                        upd["info"] = info
                        upd_btn.config(text="새 버전 v%s" % info["version"])
                        ask_update(info)
                elif kind == "upd_err":
                    manual, msg = data
                    if manual:
                        messagebox.showwarning("업데이트", "업데이트를 확인할 수 없습니다.\n\n%s" % msg, parent=root)
                elif kind == "upd_prog":
                    d, t = data
                    set_status("업데이트 받는 중… %.1f / %.1f MB" % (d / 1e6, t / 1e6) if t else
                               "업데이트 받는 중… %.1f MB" % (d / 1e6))
                elif kind == "upd_fail":
                    upd["busy"] = False
                    upd_btn.config(state="normal")
                    messagebox.showerror("업데이트", data, parent=root)
                elif kind == "upd_ready":
                    set_status("업데이트 설치 중… 프로그램이 다시 켜집니다.")
                    try:
                        if apply_update(data):
                            remember_picks()
                            root.after(300, lambda: (root.destroy(), os._exit(0)))
                        else:
                            upd["busy"] = False
                            upd_btn.config(state="normal")
                            messagebox.showinfo("업데이트", "설치 파일을 열었습니다. 안내에 따라 설치하세요.", parent=root)
                    except Exception as e:
                        upd["busy"] = False
                        upd_btn.config(state="normal")
                        messagebox.showerror("업데이트", "설치를 시작하지 못했습니다.\n%s" % e, parent=root)
        except queue.Empty:
            pass
        root.after(50, poll)

    # =====================================================================
    # 설명서 / 끌어다 놓기 / 닫기
    # =====================================================================
    def show_help():
        w = tk.Toplevel(root)
        w.title("%s 사용법" % APP_NAME)
        w.geometry("640x680")
        t = tk.Text(w, wrap="word", padx=12, pady=10)
        t.insert("1.0", help_text())
        t.config(state="disabled")
        t.pack(fill="both", expand=True)

    def drop_paths(paths):
        """끌어다 놓은 폴더(여러 개 가능)를 이미지 폴더 목록에 추가. 파일이면 그 파일이 있는 폴더"""
        add_folders([p if os.path.isdir(p) else os.path.dirname(p) for p in paths if os.path.exists(p)])

    if has_dnd:
        def on_drop(ev):
            try:
                drop_paths(list(root.tk.splitlist(ev.data)))
            except Exception as ex:
                set_status("끌어다 놓기 실패: %s" % ex)
            return getattr(ev, "action", "copy")
        for w in (root, canvas, src_list):
            try:
                w.drop_target_register(DND_FILES)
                w.dnd_bind("<<Drop>>", on_drop)
            except Exception:
                pass

    def on_close():
        remember_picks()
        settings["geometry"] = root.geometry()
        save_settings(settings)
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.after(50, poll)
    if any(os.path.isdir(d) for d in folder_list()):
        root.after(300, load_folder)
    else:
        root.after(300, redraw)
    if getattr(sys, "frozen", False) or os.environ.get("SP_CHECK_UPDATE"):
        root.after(1500, lambda: check_updates(False))
    root.mainloop()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        err = traceback.format_exc()
        try:
            with open(os.path.join(os.path.expanduser("~"), "shot_picker_error.txt"), "w", encoding="utf-8") as f:
                f.write(err)
        except Exception:
            pass
        raise
