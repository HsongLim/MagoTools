# -*- coding: utf-8 -*-
"""
Guide Maker
-----------
이미지를 불러와서 왼쪽에는 원본, 오른쪽에는 같은 이미지 위에 가이드(펜/박스/직선/화살표/글자)를 그리고
원본 해상도로 "<원본이름>_guide.<확장자>" 로 내보낸다.
이미지 파일/폴더를 창에 끌어다 놓아도 열린다. 켤 때 GitHub 에 새 버전이 있는지 확인한다.

단축키
  Ctrl+O 불러오기   Ctrl+Z 실행 취소   Ctrl+Y / Ctrl+Shift+Z 다시 실행
  ← / → 이전/다음 이미지   1~7 도구 선택   [ / ] 굵기   Ctrl+E 현재 이미지 내보내기
"""
import os
import re
import sys
import json
import math
import queue
import socket
import platform
import tempfile
import threading
import traceback
import subprocess

from PIL import Image, ImageDraw, ImageFont, ImageOps

APP_NAME = "Guide Maker"
APP_VERSION = "1.1.0"
# 업데이트 확인: 이 저장소의 Releases 중 태그가 TAG_PREFIX 로 시작하는 것
GH_REPO = "HsongLim/MagoTools"
TAG_PREFIX = "guidemaker-v"
STUDIO_PROXY = "192.168.50.200:3128"   # 회사 안에서는 이 프록시로 인터넷에 나감 (없으면 바로 연결)
IS_WIN = platform.system() == "Windows"
IS_MAC = platform.system() == "Darwin"
HERE = os.path.dirname(sys.executable if getattr(sys, "frozen", False) else os.path.abspath(__file__))
BUNDLE = getattr(sys, "_MEIPASS", HERE)
SETTINGS_FILE = os.path.join(os.path.expanduser("~"), ".guide_maker.json")

IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp", ".tga")
SUFFIX = "_guide"

TOOLS = [  # (키, 이름)
    ("pen", "펜"),
    ("rect", "박스"),
    ("fillrect", "채운 박스"),
    ("line", "직선"),
    ("arrow", "화살표"),
    ("grid", "그리드"),
    ("text", "글자"),
]
QUICK_COLORS = ["#ff2a2a", "#ffd400", "#2bd94a", "#22d3ee", "#2b6cff", "#ff3df5", "#ffffff", "#000000"]


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
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def hex_to_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


# ============================================================================
# 문서 (이미지 한 장 + 그 위의 가이드)
# ============================================================================
class Doc(object):
    def __init__(self, path):
        self.path = path
        img = Image.open(path)
        try:
            img = ImageOps.exif_transpose(img)
        except Exception:
            pass
        self.src_mode = img.mode
        self.src_format = img.format
        self.image = img.convert("RGBA")
        self.shapes = []   # 그린 순서대로. 좌표는 원본 이미지 픽셀 기준
        self.redo = []

    @property
    def size(self):
        return self.image.size

    @property
    def name(self):
        return os.path.basename(self.path)

    def out_name(self):
        stem, ext = os.path.splitext(self.name)
        if ext.lower() not in IMAGE_EXTS:
            ext = ".png"
        return stem + SUFFIX + ext


def _arrow_head(p0, p1, width):
    """p0 -> p1 화살표 머리 삼각형"""
    (x0, y0), (x1, y1) = p0, p1
    ang = math.atan2(y1 - y0, x1 - x0)
    length = max(width * 5.0, 18.0)
    spread = math.radians(26)
    left = (x1 - length * math.cos(ang - spread), y1 - length * math.sin(ang - spread))
    right = (x1 - length * math.cos(ang + spread), y1 - length * math.sin(ang + spread))
    # 선 끝이 머리 밖으로 튀어나오지 않게 선은 머리 밑동까지만
    base = (x1 - length * 0.8 * math.cos(ang), y1 - length * 0.8 * math.sin(ang))
    return [p1, left, right], base


def quad_mapper(q):
    """단위 사각형 (u, v) -> 네 점 q [왼위, 오른위, 오른아래, 왼아래] 로 보내는 원근 변환.
    코너핀처럼 모양을 바꿔도 칸 선이 원근에 맞게 곧은 선으로 나뉜다."""
    (x0, y0), (x1, y1), (x2, y2), (x3, y3) = q
    sx, sy = x0 - x1 + x2 - x3, y0 - y1 + y2 - y3
    g = h = 0.0
    if abs(sx) > 1e-9 or abs(sy) > 1e-9:
        dx1, dx2, dy1, dy2 = x1 - x2, x3 - x2, y1 - y2, y3 - y2
        den = dx1 * dy2 - dx2 * dy1
        if abs(den) > 1e-9:
            g = (sx * dy2 - dx2 * sy) / den
            h = (dx1 * sy - sx * dy1) / den
        else:  # 찌그러진 모양이면 원근 없이 고르게 나눔
            def bil(u, v):
                return ((1 - u) * (1 - v) * x0 + u * (1 - v) * x1 + u * v * x2 + (1 - u) * v * x3,
                        (1 - u) * (1 - v) * y0 + u * (1 - v) * y1 + u * v * y2 + (1 - u) * v * y3)
            return bil
    a, b, c = x1 - x0 + g * x1, x3 - x0 + h * x3, x0
    d, e, f = y1 - y0 + g * y1, y3 - y0 + h * y3, y0

    def m(u, v):
        w = g * u + h * v + 1.0
        if abs(w) < 1e-9:
            w = 1e-9
        return ((a * u + b * v + c) / w, (d * u + e * v + f) / w)
    return m


def grid_lines(q, cols, rows):
    """그리드 선 목록 [(p, p), ...] (테두리 포함)"""
    m = quad_mapper(q)
    cols, rows = max(1, int(cols)), max(1, int(rows))
    out = []
    for i in range(cols + 1):
        u = i / float(cols)
        out.append((m(u, 0.0), m(u, 1.0)))
    for j in range(rows + 1):
        v = j / float(rows)
        out.append((m(0.0, v), m(1.0, v)))
    return out


def point_in_poly(p, poly):
    x, y = p
    inside = False
    n = len(poly)
    for i in range(n):
        (xa, ya), (xb, yb) = poly[i], poly[(i + 1) % n]
        if (ya > y) != (yb > y) and x < (xb - xa) * (y - ya) / ((yb - ya) or 1e-12) + xa:
            inside = not inside
    return inside


def draw_shape(draw, shape, scale, offset=(0, 0)):
    """shape 를 draw 에 불투명하게 그린다 (투명도는 레이어 단위로 따로 적용)"""
    ox, oy = offset
    w = max(1, int(round(shape["width"] * scale)))
    col = tuple(shape["color"]) + (255,)
    pts = [(x * scale - ox, y * scale - oy) for x, y in shape["pts"]]
    t = shape["type"]
    if t == "pen":
        if len(pts) == 1:
            pts = pts * 2
        draw.line(pts, fill=col, width=w, joint="curve")
        r = w / 2.0
        for x, y in (pts[0], pts[-1]):  # 끝을 둥글게
            draw.ellipse([x - r, y - r, x + r, y + r], fill=col)
    elif t in ("rect", "fillrect"):
        (x0, y0), (x1, y1) = pts[0], pts[-1]
        box = [min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)]
        if t == "fillrect":
            draw.rectangle(box, fill=col)
        else:
            draw.rectangle(box, outline=col, width=w)
    elif t == "line":
        draw.line([pts[0], pts[-1]], fill=col, width=w)
        r = w / 2.0
        for x, y in (pts[0], pts[-1]):
            draw.ellipse([x - r, y - r, x + r, y + r], fill=col)
    elif t == "grid":
        r = w / 2.0
        for a, b in grid_lines(pts, shape.get("cols", 4), shape.get("rows", 4)):
            draw.line([a, b], fill=col, width=w)
        for x, y in pts:  # 모서리를 깔끔하게
            draw.ellipse([x - r, y - r, x + r, y + r], fill=col)
    elif t == "arrow":
        head, base = _arrow_head(pts[0], pts[-1], w)
        draw.line([pts[0], base], fill=col, width=w)
        r = w / 2.0
        x, y = pts[0]
        draw.ellipse([x - r, y - r, x + r, y + r], fill=col)
        draw.polygon(head, fill=col)


def shape_bbox(shape, scale):
    """그릴 영역 (여유 포함)"""
    xs = [x * scale for x, _ in shape["pts"]]
    ys = [y * scale for _, y in shape["pts"]]
    w = max(1.0, shape["width"] * scale)
    pad = w * (5 if shape["type"] == "arrow" else 1) + 16
    return (int(math.floor(min(xs) - pad)), int(math.floor(min(ys) - pad)),
            int(math.ceil(max(xs) + pad)), int(math.ceil(max(ys) + pad)))


# ---------------------------------------------------------------------------
# 글자
# ---------------------------------------------------------------------------
FONT_CANDIDATES = [
    # 윈도우 (맑은 고딕 굵게 > 보통)
    r"C:\Windows\Fonts\malgunbd.ttf", r"C:\Windows\Fonts\malgun.ttf", r"C:\Windows\Fonts\gulim.ttc",
    # 맥
    "/System/Library/Fonts/AppleSDGothicNeo.ttc", "/System/Library/Fonts/Supplemental/AppleGothic.ttf",
    "/Library/Fonts/AppleGothic.ttf",
    # 리눅스
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc", "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]
_font_path = []
_font_cache = {}


def get_font(size):
    size = max(4, int(size))
    f = _font_cache.get(size)
    if f is not None:
        return f
    if not _font_path:
        windir = os.environ.get("WINDIR")
        cands = ([os.path.join(windir, "Fonts", n) for n in ("malgunbd.ttf", "malgun.ttf")] if windir else [])
        path = next((p for p in cands + FONT_CANDIDATES if os.path.isfile(p)), None)
        index = 0
        if path and path.lower().endswith(".ttc"):
            # 여러 굵기가 들어 있는 글꼴 묶음: 굵은 것을 고름
            for i in range(20):
                try:
                    style = ImageFont.truetype(path, 12, index=i).getname()[1] or ""
                except Exception:
                    break
                if style.lower() in ("bold", "semibold", "extrabold"):
                    index = i
                    break
        _font_path.append((path, index))
    path, index = _font_path[0]
    try:
        f = ImageFont.truetype(path, size, index=index)
    except Exception:
        try:
            f = ImageFont.load_default(size)
        except Exception:
            f = ImageFont.load_default()
    if len(_font_cache) > 40:
        _font_cache.clear()
    _font_cache[size] = f
    return f


def _text_style(shape, scale):
    size = max(4, int(round(shape["size"] * scale)))
    font = get_font(size)
    stroke = max(1, int(round(size / 14.0))) if shape.get("stroke", True) else 0
    r, g, b = shape["color"]
    light = (r * 299 + g * 587 + b * 114) / 1000.0 > 140
    sfill = (0, 0, 0, 255) if light else (255, 255, 255, 255)
    return font, stroke, sfill, max(1, int(round(size * 0.15)))


def text_bbox(shape, scale):
    """글자가 차지하는 영역 (scale 좌표). 글자의 기준점은 왼쪽 위."""
    font, stroke, _, spacing = _text_style(shape, scale)
    x, y = shape["pts"][0]
    x, y = x * scale, y * scale
    d = ImageDraw.Draw(Image.new("L", (1, 1)))
    l, t, r, b = d.multiline_textbbox((x, y), shape["text"], font=font, stroke_width=stroke, spacing=spacing)
    return int(math.floor(l)) - 2, int(math.floor(t)) - 2, int(math.ceil(r)) + 2, int(math.ceil(b)) + 2


def visible(doc):
    """그려지는 가이드 (고치기/지우기로 숨긴 글자 기록은 뺌)"""
    return [s for s in doc.shapes if not s.get("_deleted")]


def render_layer(sh, scale, W, H):
    """도형 하나를 투명 레이어에 그려 (layer, x0, y0) 로 돌려준다. 화면 밖이면 None."""
    if sh.get("_deleted"):
        return None
    if sh["type"] == "text":
        x0, y0, x1, y1 = text_bbox(sh, scale)
    else:
        x0, y0, x1, y1 = shape_bbox(sh, scale)
    cx0, cy0 = max(0, x0), max(0, y0)
    cx1, cy1 = min(W, x1), min(H, y1)
    if cx1 <= cx0 or cy1 <= cy0:
        return None
    layer = Image.new("RGBA", (cx1 - cx0, cy1 - cy0), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    if sh["type"] == "text":
        font, stroke, sfill, spacing = _text_style(sh, scale)
        x, y = sh["pts"][0]
        d.multiline_text((x * scale - cx0, y * scale - cy0), sh["text"], font=font,
                         fill=tuple(sh["color"]) + (255,), spacing=spacing,
                         stroke_width=stroke, stroke_fill=sfill)
    else:
        draw_shape(d, sh, scale, (cx0, cy0))
    return layer, cx0, cy0


def composite(base_rgba, shapes, scale):
    """base_rgba(이미 scale 에 맞춘 크기) 위에 가이드를 그려서 돌려준다.
    도형마다 자기 투명도로 겹치고, 같은 도형 안에서 겹친 부분은 진해지지 않는다."""
    out = base_rgba.copy()
    W, H = out.size
    for sh in shapes:
        res = render_layer(sh, scale, W, H)
        if not res:
            continue
        layer, x0, y0 = res
        a = max(0, min(255, int(sh.get("alpha", 255))))
        if a < 255:
            alpha = layer.getchannel("A").point(lambda v, a=a: v * a // 255)
            layer.putalpha(alpha)
        out.alpha_composite(layer, dest=(x0, y0))
    return out


def export_doc(doc, out_dir):
    """원본 해상도로 그려서 저장. 저장한 경로를 돌려준다."""
    img = composite(doc.image, doc.shapes, 1.0)
    path = os.path.join(out_dir, doc.out_name())
    ext = os.path.splitext(path)[1].lower()
    if ext in (".jpg", ".jpeg"):
        img.convert("RGB").save(path, quality=95, subsampling=0)
    elif ext == ".bmp":
        img.convert("RGB").save(path)
    elif ext in (".tif", ".tiff"):
        (img if "A" in doc.src_mode else img.convert("RGB")).save(path, compression="tiff_lzw")
    elif ext == ".webp":
        img.save(path, quality=95)
    else:
        (img if ("A" in doc.src_mode or doc.src_mode == "P") else img.convert("RGB")).save(path)
    return path


def list_images(folder):
    try:
        names = sorted(os.listdir(folder))
    except Exception:
        return []
    return [os.path.join(folder, n) for n in names
            if n.lower().endswith(IMAGE_EXTS) and not os.path.splitext(n)[0].endswith(SUFFIX)]


def expand_paths(paths):
    """파일/폴더 목록 -> 이미지 파일 목록 (폴더는 안의 이미지)"""
    out = []
    for p in paths:
        if os.path.isdir(p):
            out += list_images(p)
        elif os.path.isfile(p) and p.lower().endswith(IMAGE_EXTS):
            out.append(p)
    return out


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


def _http_opener():
    import ssl
    import urllib.request
    handlers = []
    if STUDIO_PROXY and _proxy_reachable(STUDIO_PROXY):
        handlers.append(urllib.request.ProxyHandler({"http": "http://" + STUDIO_PROXY,
                                                     "https": "http://" + STUDIO_PROXY}))
    ctx = None
    try:
        cafile = os.environ.get("SSL_CERT_FILE")
        if not (cafile and os.path.isfile(cafile)):
            import certifi  # 맥 앱 안의 파이썬은 인증서 목록이 없을 수 있어서 같이 넣음
            cafile = certifi.where()
        ctx = ssl.create_default_context(cafile=cafile)
    except Exception:
        pass
    if ctx is not None:
        handlers.append(urllib.request.HTTPSHandler(context=ctx))
    return urllib.request.build_opener(*handlers)


def _get(url, accept="application/vnd.github+json", timeout=10):
    import urllib.request
    req = urllib.request.Request(url, headers={"Accept": accept, "User-Agent": "GuideMaker/" + APP_VERSION})
    return _http_opener().open(req, timeout=timeout)


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
        script = os.path.join(tempfile.mkdtemp(prefix="gm_update_"), "update.sh")
        with open(script, "w") as f:
            f.write(MAC_UPDATE_SCRIPT.replace("__PID__", str(os.getpid()))
                    .replace("__DMG__", installer).replace("__APP__", app))
        subprocess.Popen(["/bin/bash", script], start_new_session=True, close_fds=True, env=_clean_env())
        return True
    return False


# ============================================================================
# 화면
# ============================================================================
def main():
    if IS_WIN:
        try:  # 고해상도 모니터에서 흐리지 않게
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox, colorchooser
    from PIL import ImageTk

    settings = load_settings()
    # 파일 끌어다 놓기: tkinterdnd2 가 있으면 사용 (없으면 끌어다 놓기만 빠지고 나머지는 그대로)
    try:
        from tkinterdnd2 import TkinterDnD, DND_FILES
        root = TkinterDnD.Tk()
        has_dnd = True
    except Exception:
        root = tk.Tk()
        has_dnd = False
    root.title("%s  v%s" % (APP_NAME, APP_VERSION))
    root.geometry(settings.get("geometry") or "1400x860")
    root.minsize(980, 600)
    try:
        for d in (HERE, BUNDLE):
            ico = os.path.join(d, "GuideMaker.ico")
            if IS_WIN and os.path.isfile(ico):
                root.iconbitmap(ico)
                break
    except Exception:
        pass

    st = {
        "docs": [], "idx": -1,
        "tool": settings.get("tool", "pen"),
        "color": settings.get("color", "#ff2a2a"),
        "drawing": None,      # 그리는 중인 도형
        "temp_items": [],     # 그리는 중 미리보기용 캔버스 항목
        "scale": 1.0, "off": (0, 0),
        "cache": {},          # (doc id, 크기) -> 축소한 원본
        "photo_l": None, "photo_r": None,
        "edit": None,         # 그리드 코너/이동 편집 중
        "sel": None,          # 선택한 그리드 (도형 객체)
        "pending": False,
    }

    # ---------- 도구 막대 ----------
    bar = ttk.Frame(root, padding=(8, 6))
    bar.pack(fill="x")
    ttk.Button(bar, text="이미지 불러오기", command=lambda: open_files()).pack(side="left")
    ttk.Button(bar, text="폴더 불러오기", command=lambda: open_folder()).pack(side="left", padx=(4, 0))
    ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=8)
    ttk.Button(bar, text="◀", width=3, command=lambda: go(-1)).pack(side="left")
    nav_lbl = ttk.Label(bar, text="이미지 없음", width=34, anchor="center")
    nav_lbl.pack(side="left", padx=4)
    ttk.Button(bar, text="▶", width=3, command=lambda: go(1)).pack(side="left")
    ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=8)

    tool_var = tk.StringVar(value=st["tool"])
    for i, (key, label) in enumerate(TOOLS):
        ttk.Radiobutton(bar, text="%s" % label, value=key, variable=tool_var,
                        command=lambda: set_tool(tool_var.get())).pack(side="left", padx=1)
    ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=8)
    ttk.Label(bar, text="그리드 칸  가로").pack(side="left")
    cols_var = tk.IntVar(value=int(settings.get("grid_cols", 4)))
    rows_var = tk.IntVar(value=int(settings.get("grid_rows", 4)))
    cols_sp = ttk.Spinbox(bar, from_=1, to=64, width=4, textvariable=cols_var, command=lambda: grid_count_changed())
    cols_sp.pack(side="left", padx=(4, 6))
    ttk.Label(bar, text="세로").pack(side="left")
    rows_sp = ttk.Spinbox(bar, from_=1, to=64, width=4, textvariable=rows_var, command=lambda: grid_count_changed())
    rows_sp.pack(side="left", padx=4)
    for sp in (cols_sp, rows_sp):
        sp.bind("<Return>", lambda e: grid_count_changed())
        sp.bind("<FocusOut>", lambda e: grid_count_changed())
    upd_btn = ttk.Button(bar, text="업데이트 확인", command=lambda: check_updates(True))
    upd_btn.pack(side="right")
    ttk.Label(bar, text="v" + APP_VERSION, foreground="#888").pack(side="right", padx=6)

    bar2 = ttk.Frame(root, padding=(8, 0, 8, 6))
    bar2.pack(fill="x")
    ttk.Label(bar2, text="색").pack(side="left")
    color_btn = tk.Button(bar2, width=3, relief="solid", bd=1, command=lambda: pick_color())
    color_btn.pack(side="left", padx=(4, 6))
    for c in QUICK_COLORS:
        b = tk.Button(bar2, width=2, bg=c, activebackground=c, relief="flat", bd=0, highlightthickness=1,
                      command=lambda c=c: set_color(c))
        b.pack(side="left", padx=1, ipady=1)
    ttk.Label(bar2, text="    투명도").pack(side="left")
    alpha_var = tk.IntVar(value=int(settings.get("opacity", 100)))
    alpha_lbl = ttk.Label(bar2, width=5)
    ttk.Scale(bar2, from_=5, to=100, variable=alpha_var, length=120,
              command=lambda v: alpha_lbl.config(text="%d%%" % float(v))).pack(side="left", padx=4)
    alpha_lbl.pack(side="left")
    ttk.Label(bar2, text="  굵기").pack(side="left")
    width_var = tk.IntVar(value=int(settings.get("width", 6)))
    width_lbl = ttk.Label(bar2, width=5)
    ttk.Scale(bar2, from_=1, to=60, variable=width_var, length=120,
              command=lambda v: width_lbl.config(text="%dpx" % float(v))).pack(side="left", padx=4)
    width_lbl.pack(side="left")
    ttk.Label(bar2, text="  글자 크기").pack(side="left")
    tsize_var = tk.IntVar(value=int(settings.get("text_size", 32)))
    tsize_lbl = ttk.Label(bar2, width=5)
    ttk.Scale(bar2, from_=10, to=150, variable=tsize_var, length=110,
              command=lambda v: tsize_lbl.config(text="%dpx" % float(v))).pack(side="left", padx=4)
    tsize_lbl.pack(side="left")
    stroke_var = tk.BooleanVar(value=bool(settings.get("text_stroke", True)))
    ttk.Checkbutton(bar2, text="글자 테두리", variable=stroke_var).pack(side="left", padx=(4, 0))
    ttk.Separator(bar2, orient="vertical").pack(side="left", fill="y", padx=8)
    ttk.Button(bar2, text="실행 취소", command=lambda: undo()).pack(side="left")
    ttk.Button(bar2, text="다시 실행", command=lambda: redo()).pack(side="left", padx=4)
    ttk.Button(bar2, text="이 이미지 가이드 모두 지우기", command=lambda: clear_doc()).pack(side="left")

    # ---------- 좌우 화면 ----------
    panes = ttk.PanedWindow(root, orient="horizontal")
    panes.pack(fill="both", expand=True, padx=8)
    bg = "#2a2d33"

    def make_panel(title):
        f = ttk.Frame(panes)
        ttk.Label(f, text=title, anchor="center").pack(fill="x")
        c = tk.Canvas(f, bg=bg, highlightthickness=0, cursor="arrow")
        c.pack(fill="both", expand=True)
        panes.add(f, weight=1)
        return c

    canvas_l = make_panel("원본")
    canvas_r = make_panel("가이드 (여기에 그리세요)")
    canvas_r.config(cursor="crosshair")

    # ---------- 내보내기 ----------
    bot = ttk.Frame(root, padding=8)
    bot.pack(fill="x")
    ttk.Label(bot, text="저장 폴더").pack(side="left")
    out_var = tk.StringVar(value=settings.get("out_dir", ""))
    ttk.Entry(bot, textvariable=out_var).pack(side="left", fill="x", expand=True, padx=6)
    ttk.Button(bot, text="찾기…", command=lambda: out_var.set(
        filedialog.askdirectory(initialdir=out_var.get() or None, parent=root) or out_var.get())).pack(side="left")
    ttk.Button(bot, text="열기", command=lambda: open_out_folder()).pack(side="left", padx=(4, 10))
    ttk.Button(bot, text="현재 이미지 내보내기", command=lambda: export(False)).pack(side="left")
    ttk.Button(bot, text="전체 내보내기", command=lambda: export(True)).pack(side="left", padx=(4, 0))
    status = ttk.Label(root, text="이미지를 불러오거나 창에 끌어다 놓으세요.  (%s+O)" % ("Cmd" if IS_MAC else "Ctrl"),
                       anchor="w", padding=(10, 0, 10, 6))
    status.pack(fill="x")

    # ---------- 동작 ----------
    def cur():
        return st["docs"][st["idx"]] if 0 <= st["idx"] < len(st["docs"]) else None

    def set_status(t):
        status.config(text=t)

    def set_tool(t):
        st["tool"] = t
        tool_var.set(t)
        if cur():
            refresh()

    def set_color(c):
        st["color"] = c
        color_btn.config(bg=c, activebackground=c)

    def pick_color():
        c = colorchooser.askcolor(color=st["color"], parent=root, title="가이드 색")
        if c and c[1]:
            set_color(c[1])

    def add_docs(paths):
        added, bad = 0, []
        known = {d.path for d in st["docs"]}
        for p in paths:
            if p in known:
                continue
            try:
                st["docs"].append(Doc(p))
                added += 1
            except Exception as e:
                bad.append("%s (%s)" % (os.path.basename(p), e))
        if added and st["idx"] < 0:
            st["idx"] = 0
        elif added:
            st["idx"] = len(st["docs"]) - added
        refresh()
        msg = "%d장 불러옴 (전체 %d장)" % (added, len(st["docs"]))
        if bad:
            msg += " / 열 수 없는 파일 %d개" % len(bad)
            messagebox.showwarning(APP_NAME, "열 수 없는 파일:\n" + "\n".join(bad[:15]), parent=root)
        set_status(msg)
        if paths:
            settings["last_dir"] = os.path.dirname(paths[0])

    def open_files():
        types = [("이미지", " ".join("*" + e for e in IMAGE_EXTS)), ("모든 파일", "*.*")]
        paths = filedialog.askopenfilenames(parent=root, title="이미지 불러오기 (여러 장 선택 가능)",
                                            initialdir=settings.get("last_dir") or None, filetypes=types)
        if paths:
            add_docs(list(paths))

    def open_folder():
        d = filedialog.askdirectory(parent=root, title="이미지 폴더", initialdir=settings.get("last_dir") or None)
        if d:
            paths = list_images(d)
            if not paths:
                messagebox.showinfo(APP_NAME, "이 폴더에 이미지가 없습니다.", parent=root)
                return
            add_docs(paths)

    def go(step):
        if st["docs"]:
            st["idx"] = (st["idx"] + step) % len(st["docs"])
            refresh()

    def fit(canvas, doc):
        cw = max(10, canvas.winfo_width())
        ch = max(10, canvas.winfo_height())
        iw, ih = doc.size
        s = min((cw - 16) / iw, (ch - 16) / ih)
        s = max(s, 0.01)
        dw, dh = max(1, int(iw * s)), max(1, int(ih * s))
        return s, ((cw - dw) // 2, (ch - dh) // 2), (dw, dh)

    def base_scaled(doc, size):
        key = (id(doc), size)
        img = st["cache"].get(key)
        if img is None:
            if len(st["cache"]) > 12:
                st["cache"].clear()
            img = doc.image.resize(size, Image.LANCZOS if size[0] < doc.size[0] else Image.BILINEAR)
            st["cache"][key] = img
        return img

    def refresh(*_):
        doc = cur()
        for c in (canvas_l, canvas_r):
            c.delete("all")
        if not doc:
            nav_lbl.config(text="이미지 없음")
            for c in (canvas_l, canvas_r):
                c.create_text(c.winfo_width() // 2, c.winfo_height() // 2, fill="#8a9099",
                              text="'이미지 불러오기'로 이미지를 열거나\n이미지 파일/폴더를 여기에 끌어다 놓으세요", justify="center")
            return
        name = doc.name if len(doc.name) <= 30 else doc.name[:27] + "…"
        mark = " ●" if visible(doc) else ""
        nav_lbl.config(text="%d / %d   %s%s" % (st["idx"] + 1, len(st["docs"]), name, mark))
        s, off, size = fit(canvas_r, doc)
        st["scale"], st["off"] = s, off
        base = base_scaled(doc, size)
        sl, offl, sizel = fit(canvas_l, doc)
        basel = base if sizel == size else base_scaled(doc, sizel)
        st["photo_l"] = ImageTk.PhotoImage(basel)
        canvas_l.create_image(offl[0], offl[1], anchor="nw", image=st["photo_l"])
        st["photo_r"] = ImageTk.PhotoImage(composite(base, doc.shapes, s))
        canvas_r.create_image(off[0], off[1], anchor="nw", image=st["photo_r"])
        if st["tool"] == "grid":  # 그리드 코너 손잡이
            for sh in visible(doc):
                if sh["type"] != "grid":
                    continue
                sel = sh is st["sel"]
                for x, y in sh["pts"]:
                    cx, cy = to_canvas((x, y))
                    r = 6 if sel else 5
                    canvas_r.create_rectangle(cx - r, cy - r, cx + r, cy + r, outline="#000000",
                                              fill="#ffffff" if sel else "#9aa3ad", width=1)
        canvas_r.create_text(8, 8, anchor="nw", fill="#8a9099",
                             text="%d×%d  가이드 %d개" % (doc.size[0], doc.size[1], len(visible(doc))))

    def to_img(ev):
        s, (ox, oy) = st["scale"], st["off"]
        doc = cur()
        x = (ev.x - ox) / s
        y = (ev.y - oy) / s
        if doc:
            x = min(max(x, 0), doc.size[0])
            y = min(max(y, 0), doc.size[1])
        return (x, y)

    def to_canvas(p):
        s, (ox, oy) = st["scale"], st["off"]
        return (p[0] * s + ox, p[1] * s + oy)

    def ask_text(initial="", title="글자 넣기"):
        """여러 줄 입력 창. 넣기 -> 문자열, 취소 -> None"""
        dlg = tk.Toplevel(root)
        dlg.title(title)
        dlg.transient(root)
        dlg.resizable(True, True)
        res = {"v": None}
        frm = ttk.Frame(dlg, padding=10)
        frm.pack(fill="both", expand=True)
        ttk.Label(frm, text="넣을 글자 (여러 줄 가능)").pack(anchor="w")
        txt = tk.Text(frm, width=40, height=4, wrap="word", undo=True)
        txt.pack(fill="both", expand=True, pady=6)
        txt.insert("1.0", initial)
        txt.tag_add("sel", "1.0", "end-1c")
        btns = ttk.Frame(frm)
        btns.pack(fill="x")

        def ok(*_):
            res["v"] = txt.get("1.0", "end-1c")
            dlg.destroy()
            return "break"

        def cancel(*_):
            dlg.destroy()
            return "break"

        okmod = "Command" if IS_MAC else "Control"
        ttk.Label(btns, text="%s+Enter: 넣기   Esc: 취소" % ("Cmd" if IS_MAC else "Ctrl"),
                  foreground="#888").pack(side="left")
        ttk.Button(btns, text="취소", command=cancel).pack(side="right")
        ttk.Button(btns, text="넣기", command=ok).pack(side="right", padx=6)
        txt.bind("<%s-Return>" % okmod, ok)
        dlg.bind("<Escape>", cancel)
        dlg.protocol("WM_DELETE_WINDOW", cancel)
        dlg.update_idletasks()
        dw, dh = dlg.winfo_reqwidth(), dlg.winfo_reqheight()
        sw, sh_ = root.winfo_screenwidth(), root.winfo_screenheight()
        x = root.winfo_pointerx() - dw // 2
        y = root.winfo_pointery() + 20
        if y + dh > sh_ - 60:  # 화면 아래로 넘치면 마우스 위쪽에
            y = root.winfo_pointery() - dh - 40
        dlg.geometry("+%d+%d" % (max(0, min(x, sw - dw - 10)), max(0, y)))
        txt.focus_set()
        try:
            dlg.grab_set()
        except Exception:
            pass
        root.wait_window(dlg)
        return res["v"]

    def text_at(doc, p):
        """p(원본 좌표) 위에 있는 글자 도형 번호 (맨 위 것), 없으면 None"""
        for i in range(len(doc.shapes) - 1, -1, -1):
            sh = doc.shapes[i]
            if sh["type"] == "text" and not sh.get("_deleted"):
                x0, y0, x1, y1 = text_bbox(sh, 1.0)
                if x0 <= p[0] <= x1 and y0 <= p[1] <= y1:
                    return i
        return None

    def place_text(doc, p):
        i = text_at(doc, p)
        if i is not None:  # 이미 있는 글자를 누르면 고치기
            old = doc.shapes[i]
            s = ask_text(old["text"], "글자 고치기 (모두 지우면 삭제)")
            if s is None or s == old["text"]:
                return
            new = dict(old, text=s, _replaced=(i, old))
            del doc.shapes[i]
            if s.strip():
                doc.shapes.append(new)
                set_status("글자 고침")
            else:
                new["_deleted"] = True  # 되돌리기용 표시만 (그리지 않음)
                doc.shapes.append(new)
                set_status("글자 삭제")
        else:
            s = ask_text()
            if not s or not s.strip():
                return
            doc.shapes.append({"type": "text", "text": s, "pts": [p], "color": list(hex_to_rgb(st["color"])),
                               "alpha": int(round(alpha_var.get() * 2.55)),
                               "size": max(4.0, float(tsize_var.get()) / st["scale"]),
                               "stroke": bool(stroke_var.get()), "width": 1.0})
            set_status("글자 추가 (가이드 %d개)" % len(visible(doc)))
        doc.redo.clear()
        refresh()

    last_counts = [max(1, min(64, int(cols_var.get()))), max(1, min(64, int(rows_var.get())))]

    def get_counts():
        """입력칸 값 (잘못 입력하면 마지막으로 올바른 값으로 되돌림)"""
        for n, var in enumerate((cols_var, rows_var)):
            try:
                v = int(str(var.get()).strip())
                if not 1 <= v <= 64:
                    raise ValueError
                last_counts[n] = v
            except Exception:
                var.set(last_counts[n])
        return tuple(last_counts)

    def replace_shape(doc, sh, new):
        """sh 를 new 로 바꾸고 되돌리기 기록을 남긴다 (바뀐 도형은 맨 위로)"""
        i = next(k for k, x in enumerate(doc.shapes) if x is sh)
        del doc.shapes[i]
        new["_replaced"] = (i, sh)
        doc.shapes.append(new)
        doc.redo.clear()
        return new

    def grid_count_changed():
        c, r = get_counts()
        doc = cur()
        sh = st["sel"]
        if doc and sh is not None and any(x is sh for x in doc.shapes) \
                and (sh.get("cols"), sh.get("rows")) != (c, r):
            st["sel"] = replace_shape(doc, sh, dict(sh, cols=c, rows=r))
            refresh()
            set_status("그리드 칸: 가로 %d × 세로 %d" % (c, r))

    def grid_hit(doc, ev):
        """누른 곳: ("corner", 도형, 번호) / ("move", 도형, None) / None"""
        grids = [x for x in visible(doc) if x["type"] == "grid"]
        for sh in reversed(grids):
            for k, pt in enumerate(sh["pts"]):
                cx, cy = to_canvas(pt)
                if abs(cx - ev.x) <= 9 and abs(cy - ev.y) <= 9:
                    return "corner", sh, k
        p = ((ev.x - st["off"][0]) / st["scale"], (ev.y - st["off"][1]) / st["scale"])
        for sh in reversed(grids):
            if point_in_poly(p, sh["pts"]):
                return "move", sh, None
        return None

    def refresh_soon():
        if not st["pending"]:
            st["pending"] = True

            def run():
                st["pending"] = False
                refresh()
            root.after(15, run)

    def select_grid(sh):
        st["sel"] = sh
        if sh is not None:
            cols_var.set(sh.get("cols", 4))
            rows_var.set(sh.get("rows", 4))

    def on_press(ev):
        canvas_r.focus_set()  # 입력칸에 있던 키보드 입력을 화면으로 (단축키가 다시 동작하게)
        doc = cur()
        if not doc:
            return
        p = to_img(ev)
        if st["tool"] == "text":
            st["drawing"] = None
            place_text(doc, p)
            return
        if st["tool"] == "grid":
            hit = grid_hit(doc, ev)
            if hit:
                kind, sh, k = hit
                select_grid(sh)
                # 편집은 복사본에서: 놓을 때 되돌리기 기록과 함께 바꿈
                work = dict(sh, pts=[tuple(x) for x in sh["pts"]])
                i = next(n for n, x in enumerate(doc.shapes) if x is sh)
                doc.shapes[i] = work
                st["edit"] = {"kind": kind, "k": k, "orig": sh, "work": work,
                              "start": ((ev.x - st["off"][0]) / st["scale"], (ev.y - st["off"][1]) / st["scale"]),
                              "pts0": list(work["pts"])}
                st["drawing"] = None
                refresh()
                return
            select_grid(None)
        st["drawing"] = {"type": st["tool"], "pts": [p], "color": list(hex_to_rgb(st["color"])),
                         "alpha": int(round(alpha_var.get() * 2.55)),
                         "width": max(1.0, float(width_var.get()) / st["scale"])}
        st["temp_items"] = []

    def on_drag(ev):
        ed = st["edit"]
        if ed:
            # 코너는 이미지 밖으로도 조금 나갈 수 있게 (원근 맞출 때 필요)
            p = ((ev.x - st["off"][0]) / st["scale"], (ev.y - st["off"][1]) / st["scale"])
            if ed["kind"] == "corner":
                ed["work"]["pts"][ed["k"]] = p
            else:
                dx, dy = p[0] - ed["start"][0], p[1] - ed["start"][1]
                ed["work"]["pts"] = [(x + dx, y + dy) for x, y in ed["pts0"]]
            st["sel"] = ed["work"]
            refresh_soon()
            return
        sh = st["drawing"]
        if not sh:
            return
        p = to_img(ev)
        w = max(1, int(round(width_var.get())))
        col = st["color"]
        if sh["type"] == "pen":
            a = to_canvas(sh["pts"][-1])
            b = to_canvas(p)
            if abs(a[0] - b[0]) + abs(a[1] - b[1]) < 1.5:
                return
            sh["pts"].append(p)
            st["temp_items"].append(canvas_r.create_line(a[0], a[1], b[0], b[1], fill=col, width=w,
                                                         capstyle="round", joinstyle="round"))
        else:
            sh["pts"] = [sh["pts"][0], p]
            for it in st["temp_items"]:
                canvas_r.delete(it)
            a, b = to_canvas(sh["pts"][0]), to_canvas(p)
            if sh["type"] == "rect":
                items = [canvas_r.create_rectangle(a[0], a[1], b[0], b[1], outline=col, width=w)]
            elif sh["type"] == "fillrect":
                items = [canvas_r.create_rectangle(a[0], a[1], b[0], b[1], outline=col, fill=col,
                                                   stipple="gray50" if sh["alpha"] < 200 else "")]
            elif sh["type"] == "line":
                items = [canvas_r.create_line(a[0], a[1], b[0], b[1], fill=col, width=w, capstyle="round")]
            elif sh["type"] == "grid":
                c, r = get_counts()
                items = [canvas_r.create_rectangle(a[0], a[1], b[0], b[1], outline=col, width=w)]
                for i in range(1, c):
                    x = a[0] + (b[0] - a[0]) * i / c
                    items.append(canvas_r.create_line(x, a[1], x, b[1], fill=col, width=max(1, w)))
                for j in range(1, r):
                    y = a[1] + (b[1] - a[1]) * j / r
                    items.append(canvas_r.create_line(a[0], y, b[0], y, fill=col, width=max(1, w)))
            else:
                items = [canvas_r.create_line(a[0], a[1], b[0], b[1], fill=col, width=w,
                                              arrow="last", arrowshape=(w * 4 + 8, w * 4 + 8, w * 1.6 + 4))]
            st["temp_items"] = items

    def on_release(ev):
        ed = st["edit"]
        if ed:
            st["edit"] = None
            doc = cur()
            work, orig = ed["work"], ed["orig"]
            i = next(n for n, x in enumerate(doc.shapes) if x is work)
            if [tuple(x) for x in work["pts"]] == [tuple(x) for x in orig["pts"]]:
                doc.shapes[i] = orig  # 안 움직였으면 그대로
                st["sel"] = orig
            else:
                doc.shapes[i] = orig
                st["sel"] = replace_shape(doc, orig, work)
                set_status("그리드 %s" % ("코너 이동" if ed["kind"] == "corner" else "이동"))
            refresh()
            return
        sh = st["drawing"]
        st["drawing"] = None
        doc = cur()
        if not sh or not doc:
            return
        if sh["type"] != "pen":
            if len(sh["pts"]) < 2:
                sh["pts"].append(to_img(ev))
            (x0, y0), (x1, y1) = sh["pts"][0], sh["pts"][-1]
            if abs(x1 - x0) * st["scale"] < 2 and abs(y1 - y0) * st["scale"] < 2:
                refresh()
                return  # 너무 작으면 무시
        if sh["type"] == "grid":
            (x0, y0), (x1, y1) = sh["pts"][0], sh["pts"][-1]
            l, r_, t, b = min(x0, x1), max(x0, x1), min(y0, y1), max(y0, y1)
            sh["pts"] = [(l, t), (r_, t), (r_, b), (l, b)]
            sh["cols"], sh["rows"] = get_counts()
            st["sel"] = sh
        doc.shapes.append(sh)
        doc.redo.clear()
        refresh()
        set_status("%s 추가 (가이드 %d개)" % (dict(TOOLS)[sh["type"]], len(visible(doc))))

    def undo(*_):
        doc = cur()
        if doc and doc.shapes:
            sh = doc.shapes.pop()
            rep = sh.get("_replaced")
            if rep:  # 글자 고치기/지우기를 되돌림: 원래 글자를 제자리에
                doc.shapes.insert(min(rep[0], len(doc.shapes)), rep[1])
            doc.redo.append(sh)
            refresh()

    def redo(*_):
        doc = cur()
        if doc and doc.redo:
            sh = doc.redo.pop()
            rep = sh.get("_replaced")
            if rep:
                for i, x in enumerate(doc.shapes):
                    if x is rep[1]:
                        del doc.shapes[i]
                        break
            doc.shapes.append(sh)
            refresh()

    def clear_doc():
        doc = cur()
        if doc and visible(doc) and messagebox.askyesno(APP_NAME, "이 이미지의 가이드를 모두 지울까요?", parent=root):
            doc.redo = list(reversed(doc.shapes)) + doc.redo
            doc.shapes = []
            refresh()

    def open_out_folder():
        d = out_var.get().strip().strip('"')
        if d and os.path.isdir(d):
            try:
                if IS_WIN:
                    os.startfile(d)
                elif IS_MAC:
                    import subprocess
                    subprocess.Popen(["open", d])
                else:
                    import subprocess
                    subprocess.Popen(["xdg-open", d])
            except Exception:
                pass

    def export(all_docs):
        if not st["docs"]:
            messagebox.showinfo(APP_NAME, "불러온 이미지가 없습니다.", parent=root)
            return
        out = out_var.get().strip().strip('"')
        if not out:
            out = filedialog.askdirectory(parent=root, title="저장 폴더")
            if not out:
                return
            out_var.set(out)
        try:
            os.makedirs(out, exist_ok=True)
        except Exception as e:
            messagebox.showerror(APP_NAME, "저장 폴더를 만들 수 없습니다:\n%s\n\n%s" % (out, e), parent=root)
            return
        docs = st["docs"] if all_docs else [cur()]
        if all_docs:
            drawn = [d for d in docs if visible(d)]
            if not drawn:
                messagebox.showinfo(APP_NAME, "가이드를 그린 이미지가 없습니다.", parent=root)
                return
            if len(drawn) < len(docs):
                ans = messagebox.askyesnocancel(
                    APP_NAME, "가이드를 그린 이미지는 %d장입니다 (전체 %d장).\n\n"
                              "예: 그린 이미지만 내보내기\n아니요: 전체 내보내기" % (len(drawn), len(docs)), parent=root)
                if ans is None:
                    return
                if ans:
                    docs = drawn
        exist = [d for d in docs if os.path.exists(os.path.join(out, d.out_name()))]
        if exist and not messagebox.askyesno(
                APP_NAME, "같은 이름의 파일이 %d개 있습니다. 덮어쓸까요?\n\n%s"
                % (len(exist), "\n".join(d.out_name() for d in exist[:8])), parent=root):
            return
        root.config(cursor="watch")
        root.update_idletasks()
        ok, fail = [], []
        for i, d in enumerate(docs):
            set_status("내보내는 중… %d / %d  %s" % (i + 1, len(docs), d.name))
            root.update_idletasks()
            try:
                ok.append(export_doc(d, out))
            except Exception as e:
                fail.append("%s: %s" % (d.name, e))
        root.config(cursor="")
        settings["out_dir"] = out
        save_settings(settings)
        set_status("내보내기 완료: %d장 -> %s" % (len(ok), out) + ("  (실패 %d)" % len(fail) if fail else ""))
        msg = "%d장을 저장했습니다.\n%s" % (len(ok), out)
        if len(ok) == 1:
            msg = "저장했습니다.\n%s" % ok[0]
        if fail:
            msg += "\n\n실패:\n" + "\n".join(fail[:10])
        if messagebox.askyesno(APP_NAME, msg + "\n\n저장 폴더를 열까요?", parent=root):
            open_out_folder()

    def save_ui_settings():
        settings.update(tool=st["tool"], color=st["color"], opacity=int(alpha_var.get()),
                        width=int(width_var.get()), text_size=int(tsize_var.get()),
                        text_stroke=bool(stroke_var.get()), out_dir=out_var.get().strip(),
                        grid_cols=get_counts()[0], grid_rows=get_counts()[1],
                        geometry=root.geometry())
        save_settings(settings)

    def on_close():
        save_ui_settings()
        unsaved = [d for d in st["docs"] if visible(d)]
        if unsaved and not messagebox.askyesno(
                APP_NAME, "가이드를 그린 이미지가 %d장 있습니다.\n내보내지 않은 가이드는 사라집니다. 종료할까요?" % len(unsaved),
                parent=root):
            return
        root.destroy()

    def change_width(d):
        width_var.set(max(1, min(60, int(width_var.get()) + d)))
        width_lbl.config(text="%dpx" % width_var.get())

    # ---------- 업데이트 ----------
    q = queue.Queue()
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
        msg = ("새 버전 v%s 이 있습니다. (지금 v%s)\n\n%s\n\n지금 업데이트할까요?\n"
               "프로그램이 잠시 닫혔다가 새 버전으로 다시 켜집니다." % (info["version"], APP_VERSION, notes))
        unsaved = [d for d in st["docs"] if visible(d)]
        if unsaved:
            msg += "\n\n※ 내보내지 않은 가이드(%d장)는 사라집니다." % len(unsaved)
        if not messagebox.askyesno("업데이트", msg, parent=root):
            return
        if not getattr(sys, "frozen", False):
            import webbrowser
            webbrowser.open(info.get("url") or "https://github.com/%s/releases" % GH_REPO)
            return
        upd["busy"] = True
        upd_btn.config(state="disabled")
        asset = info["asset"]

        def work():
            dest = os.path.join(tempfile.mkdtemp(prefix="gm_update_"), asset.get("name") or "update.bin")
            try:
                download_asset(asset, dest, lambda d, t: q.put(("upd_prog", (d, t))))
            except Exception as e:
                q.put(("upd_fail", "다운로드 실패: %s" % str(e)[:300]))
                return
            q.put(("upd_ready", dest))
        threading.Thread(target=work, daemon=True).start()

    def poll():
        try:
            while True:
                kind, data = q.get_nowait()
                if kind == "upd_found":
                    manual, info = data
                    if not info:
                        if manual:
                            messagebox.showinfo("업데이트", "최신 버전입니다. (v%s)" % APP_VERSION, parent=root)
                    else:
                        upd["info"] = info
                        upd_btn.config(text="새 버전 v%s" % info["version"])
                        set_status("새 버전 v%s 이 있습니다. 오른쪽 위 버튼으로 업데이트할 수 있습니다." % info["version"])
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
                    set_status(data)
                    messagebox.showerror("업데이트", data, parent=root)
                elif kind == "upd_ready":
                    set_status("업데이트 설치 중… 프로그램이 다시 켜집니다.")
                    try:
                        if apply_update(data):
                            save_ui_settings()
                            # 설치 프로그램이 실행 파일을 바꿀 수 있게 바로 완전히 종료
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
        root.after(200, poll)

    # ---------- 파일 끌어다 놓기 ----------
    def drop_paths(paths):
        imgs = expand_paths(paths)
        if imgs:
            add_docs(imgs)
        elif paths:
            set_status("이미지 파일이 아닙니다. (지원: %s)" % ", ".join(e[1:] for e in IMAGE_EXTS))

    def on_drop(ev):
        try:
            drop_paths(list(root.tk.splitlist(ev.data)))
        except Exception as e:
            set_status("끌어다 놓기 실패: %s" % e)
        return getattr(ev, "action", "copy")

    if has_dnd:
        for w in (root, canvas_l, canvas_r):
            try:
                w.drop_target_register(DND_FILES)
                w.dnd_bind("<<Drop>>", on_drop)
            except Exception:
                pass
    if IS_MAC:  # 파인더에서 앱 아이콘에 끌어다 놓거나 "다음으로 열기"
        try:
            root.createcommand("::tk::mac::OpenDocument", lambda *a: drop_paths(list(a)))
        except Exception:
            pass

    # 글자를 입력하는 칸에서는 단축키(숫자, 화살표 등)가 동작하지 않게
    def key(fn):
        def h(e):
            if isinstance(e.widget, (tk.Entry, ttk.Entry, tk.Text)):
                return None
            return fn(e)
        return h

    canvas_r.bind("<ButtonPress-1>", on_press)
    canvas_r.bind("<B1-Motion>", on_drag)
    canvas_r.bind("<ButtonRelease-1>", on_release)
    for c in (canvas_l, canvas_r):
        c.bind("<Configure>", lambda e: root.after_idle(refresh))
    mod = "Command" if IS_MAC else "Control"
    root.bind("<%s-o>" % mod, lambda e: open_files())
    root.bind("<%s-z>" % mod, key(undo))
    root.bind("<%s-y>" % mod, key(redo))
    root.bind("<%s-Z>" % mod, key(redo))
    root.bind("<%s-e>" % mod, lambda e: export(False))
    root.bind("<Left>", key(lambda e: go(-1)))
    root.bind("<Right>", key(lambda e: go(1)))
    root.bind("<bracketleft>", key(lambda e: change_width(-1)))
    root.bind("<bracketright>", key(lambda e: change_width(1)))
    for i, (k, _) in enumerate(TOOLS):
        root.bind(str(i + 1), key(lambda e, k=k: set_tool(k)))
    root.protocol("WM_DELETE_WINDOW", on_close)

    set_color(st["color"])
    set_tool(st["tool"])
    alpha_lbl.config(text="%d%%" % alpha_var.get())
    width_lbl.config(text="%dpx" % width_var.get())
    tsize_lbl.config(text="%dpx" % tsize_var.get())
    # 명령줄/아이콘에 끌어다 놓기로 실행한 경우 그 이미지들 열기
    args = [a for a in sys.argv[1:] if os.path.isfile(a) or os.path.isdir(a)]
    if args:
        root.after(200, lambda: drop_paths(args))
    root.after(200, poll)
    if getattr(sys, "frozen", False) or os.environ.get("GM_CHECK_UPDATE"):
        root.after(1500, lambda: check_updates(False))
    root.after(100, refresh)
    root.mainloop()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        err = traceback.format_exc()
        try:
            with open(os.path.join(os.path.expanduser("~"), "guide_maker_error.txt"), "w", encoding="utf-8") as f:
                f.write(err)
        except Exception:
            pass
        raise
