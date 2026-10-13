# -*- coding: utf-8 -*-
"""
Guide Maker
-----------
이미지를 불러와서 왼쪽에는 원본, 오른쪽에는 같은 이미지 위에 가이드(펜/박스/직선/화살표/글자)를 그리고
원본 해상도로 "<원본이름>_guide.<확장자>" 로 내보낸다.
이미지 파일/폴더를 창에 끌어다 놓아도 열린다. 켤 때 GitHub 에 새 버전이 있는지 확인한다.

단축키
  Ctrl+O 불러오기   Ctrl+Z 실행 취소   Ctrl+Y / Ctrl+Shift+Z 다시 실행
  ← / → 이전/다음 이미지   1~8 도구 선택   [ / ] 굵기   Ctrl+E 현재 이미지 내보내기
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
APP_VERSION = "1.1.2"
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
COMPARE_SUFFIX = "_compare"   # 원본 + 가이드를 붙인 이미지

TOOLS = [  # (키, 이름)
    ("pen", "펜"),
    ("rect", "박스"),
    ("fillrect", "채운 박스"),
    ("line", "직선"),
    ("arrow", "화살표"),
    ("grid", "그리드"),
    ("text", "글자"),
    ("eraser", "지우개"),
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

    def out_name(self, suffix=SUFFIX):
        stem, ext = os.path.splitext(self.name)
        if ext.lower() not in IMAGE_EXTS:
            ext = ".png"
        return stem + suffix + ext


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


def _seg_dist(p, a, b):
    (px, py), (ax, ay), (bx, by) = p, a, b
    dx, dy = bx - ax, by - ay
    L = dx * dx + dy * dy
    t = 0.0 if L == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def shape_hit(sh, p, tol):
    """원본 좌표 p 가 도형 sh 위(선은 tol 거리 안)에 있는지"""
    t = sh["type"]
    pts = sh["pts"]
    half = sh.get("width", 1.0) / 2.0 + tol
    if t == "text":
        x0, y0, x1, y1 = text_bbox(sh, 1.0)
        return x0 - tol <= p[0] <= x1 + tol and y0 - tol <= p[1] <= y1 + tol
    if t == "grid":
        return point_in_poly(p, pts) or any(_seg_dist(p, pts[i], pts[(i + 1) % 4]) <= half for i in range(4))
    if t in ("rect", "fillrect"):
        (x0, y0), (x1, y1) = pts[0], pts[-1]
        l, r, tp, b = min(x0, x1), max(x0, x1), min(y0, y1), max(y0, y1)
        if t == "fillrect":
            return l - tol <= p[0] <= r + tol and tp - tol <= p[1] <= b + tol
        corners = [(l, tp), (r, tp), (r, b), (l, b)]
        return any(_seg_dist(p, corners[i], corners[(i + 1) % 4]) <= half for i in range(4))
    if len(pts) == 1:
        return math.hypot(p[0] - pts[0][0], p[1] - pts[0][1]) <= half
    if t in ("line", "arrow"):
        return _seg_dist(p, pts[0], pts[-1]) <= half + (sh.get("width", 1.0) * 2 if t == "arrow" else 0)
    return any(_seg_dist(p, a, b) <= half for a, b in zip(pts, pts[1:]))


def scale_pts(pts, origin, f):
    ox, oy = origin
    return [(ox + (x - ox) * f, oy + (y - oy) * f) for x, y in pts]


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


def save_image(img, path, src_mode):
    """확장자에 맞게 저장 (원본에 투명이 있으면 유지)"""
    ext = os.path.splitext(path)[1].lower()
    if ext in (".jpg", ".jpeg"):
        img.convert("RGB").save(path, quality=95, subsampling=0)
    elif ext == ".bmp":
        img.convert("RGB").save(path)
    elif ext in (".tif", ".tiff"):
        (img if "A" in src_mode else img.convert("RGB")).save(path, compression="tiff_lzw")
    elif ext == ".webp":
        img.save(path, quality=95)
    else:
        (img if ("A" in src_mode or src_mode == "P") else img.convert("RGB")).save(path)
    return path


def _label(img, text, size):
    """이미지 왼쪽 위에 이름표 (흰 글씨 + 검은 테두리 + 반투명 바탕)"""
    font = get_font(size)
    pad = max(4, size // 3)
    d = ImageDraw.Draw(img)
    l, t, r, b = d.textbbox((0, 0), text, font=font, stroke_width=max(1, size // 14))
    box = Image.new("RGBA", (r - l + pad * 2, b - t + pad * 2), (0, 0, 0, 140))
    img.alpha_composite(box, dest=(pad, pad))
    d.text((pad * 2 - l, pad * 2 - t), text, font=font, fill=(255, 255, 255, 255),
           stroke_width=max(1, size // 14), stroke_fill=(0, 0, 0, 255))


def compare_image(orig, guide, layout="h", divider=True, labels=False):
    """원본과 가이드를 붙인 한 장. layout: "h" 좌우 / "v" 위아래"""
    W, H = orig.size
    gap = max(2, int(round(min(W, H) * 0.004))) if divider else 0
    if layout == "v":
        out = Image.new("RGBA", (W, H * 2 + gap), (255, 255, 255, 255))
        pos2 = (0, H + gap)
    else:
        out = Image.new("RGBA", (W * 2 + gap, H), (255, 255, 255, 255))
        pos2 = (W + gap, 0)
    a, b = orig.copy(), guide.copy()
    if labels:
        size = max(14, int(round(min(W, H) * 0.035)))
        _label(a, "원본", size)
        _label(b, "가이드", size)
    out.alpha_composite(a, dest=(0, 0))
    out.alpha_composite(b, dest=pos2)
    return out


def export_doc(doc, out_dir, mode="guide", layout="h", divider=True, labels=False):
    """원본 해상도로 그려서 저장. mode: "guide" 가이드만 / "compare" 붙인 것만 / "both" 둘 다.
    저장한 경로 목록을 돌려준다."""
    guide = composite(doc.image, doc.shapes, 1.0)
    paths = []
    if mode in ("guide", "both"):
        paths.append(save_image(guide, os.path.join(out_dir, doc.out_name()), doc.src_mode))
    if mode in ("compare", "both"):
        img = compare_image(doc.image, guide, layout, divider, labels)
        paths.append(save_image(img, os.path.join(out_dir, doc.out_name(COMPARE_SUFFIX)), doc.src_mode))
    return paths


def out_names(doc, mode):
    return ([doc.out_name()] if mode in ("guide", "both") else []) + \
           ([doc.out_name(COMPARE_SUFFIX)] if mode in ("compare", "both") else [])


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
    from tkinter import font as tkfont
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
        "tedit": None,        # 글자 입력 중
        "tsel": None,         # 선택한 글자
        "tdrag": None,        # 글자 이동/크기 조절 중
        "hover": None,        # 지우개가 가리키는 도형
        "wheel": None,        # 휠로 크기 조절 중인 도형 (연속 휠은 되돌리기 한 번으로)
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
    cols_var = tk.IntVar(value=int(settings.get("grid_cols", 10)))
    rows_var = tk.IntVar(value=int(settings.get("grid_rows", 10)))
    cols_sp = ttk.Spinbox(bar, from_=1, to=64, width=4, textvariable=cols_var, command=lambda: grid_count_changed())
    cols_sp.pack(side="left", padx=(4, 6))
    ttk.Label(bar, text="세로").pack(side="left")
    rows_sp = ttk.Spinbox(bar, from_=1, to=64, width=4, textvariable=rows_var, command=lambda: grid_count_changed())
    rows_sp.pack(side="left", padx=4)
    ttk.Label(bar, text="선 굵기").pack(side="left", padx=(6, 0))
    gw_var = tk.IntVar(value=int(settings.get("grid_width", 2)))
    gw_sp = ttk.Spinbox(bar, from_=1, to=50, width=4, textvariable=gw_var, command=lambda: apply_style_to_grid())
    gw_sp.pack(side="left", padx=(4, 0))
    ttk.Label(bar, text="px").pack(side="left", padx=(2, 0))
    gw_sp.bind("<Return>", lambda e: apply_style_to_grid())
    gw_sp.bind("<FocusOut>", lambda e: apply_style_to_grid())
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
    alpha_scale = ttk.Scale(bar2, from_=5, to=100, variable=alpha_var, length=120,
                            command=lambda v: alpha_lbl.config(text="%d%%" % float(v)))
    alpha_scale.pack(side="left", padx=4)
    alpha_scale.bind("<ButtonRelease-1>", lambda e: root.after_idle(apply_style_to_grid))
    alpha_lbl.pack(side="left")
    ttk.Label(bar2, text="  굵기").pack(side="left")
    # 굵기는 도구마다 따로 기억 (그리드는 기본 얇게)
    tool_widths = dict(settings.get("tool_widths") or {})
    TOOL_DEFAULT_WIDTH = {"grid": 2}

    def tool_width(t):
        return int(tool_widths.get(t, TOOL_DEFAULT_WIDTH.get(t, settings.get("width", 6))))

    width_var = tk.IntVar(value=tool_width(st["tool"]))
    width_lbl = ttk.Label(bar2, width=5)
    width_scale = ttk.Scale(bar2, from_=1, to=60, variable=width_var, length=120,
                            command=lambda v: width_lbl.config(text="%dpx" % float(v)))
    width_scale.pack(side="left", padx=4)
    width_scale.bind("<ButtonRelease-1>", lambda e: root.after_idle(apply_style_to_grid))
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
    opt = ttk.Frame(root, padding=(8, 6, 8, 0))
    opt.pack(fill="x")
    ttk.Label(opt, text="내보내기").pack(side="left")
    mode_var = tk.StringVar(value=settings.get("export_mode", "guide"))
    for val, label in (("guide", "가이드만"), ("compare", "원본 + 가이드 붙이기"), ("both", "둘 다")):
        ttk.Radiobutton(opt, text=label, value=val, variable=mode_var,
                        command=lambda: update_export_opts()).pack(side="left", padx=(8, 0))
    ttk.Separator(opt, orient="vertical").pack(side="left", fill="y", padx=10)
    layout_lbl = ttk.Label(opt, text="배치")
    layout_lbl.pack(side="left")
    layout_var = tk.StringVar(value=settings.get("export_layout", "h"))
    layout_rbs = [ttk.Radiobutton(opt, text=t, value=v, variable=layout_var)
                  for v, t in (("h", "좌우"), ("v", "위아래"))]
    for rb in layout_rbs:
        rb.pack(side="left", padx=(6, 0))
    divider_var = tk.BooleanVar(value=bool(settings.get("export_divider", True)))
    labels_var = tk.BooleanVar(value=bool(settings.get("export_labels", False)))
    divider_cb = ttk.Checkbutton(opt, text="구분선", variable=divider_var)
    divider_cb.pack(side="left", padx=(12, 0))
    labels_cb = ttk.Checkbutton(opt, text="이름표 (원본 / 가이드)", variable=labels_var)
    labels_cb.pack(side="left", padx=(8, 0))
    name_lbl = ttk.Label(opt, foreground="#888")
    name_lbl.pack(side="left", padx=(12, 0))

    def update_export_opts():
        on = mode_var.get() != "guide"
        for w in layout_rbs + [divider_cb, labels_cb]:
            w.state(["!disabled"] if on else ["disabled"])
        name_lbl.config(text={"guide": "→ 이름_guide", "compare": "→ 이름_compare",
                              "both": "→ 이름_guide + 이름_compare"}[mode_var.get()])

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
        if t != "text":
            finish_text_edit()
        if t != st["tool"]:
            tool_widths[st["tool"]] = int(width_var.get())
            width_var.set(tool_width(t))
            width_lbl.config(text="%dpx" % width_var.get())
        st["tool"] = t
        st["hover"] = None
        tool_var.set(t)
        if cur():
            refresh()

    def set_color(c):
        st["color"] = c
        color_btn.config(bg=c, activebackground=c)
        ted = st.get("tedit")
        if ted:
            ted["style"]["color"] = list(hex_to_rgb(c))
            ted["w"].config(fg=c, insertbackground=c, highlightcolor=c, highlightbackground=c)
            return
        if st.get("tool") == "grid":
            apply_style_to_grid()
            return
        doc, sel = cur(), st.get("tsel")
        if doc and sel is not None and st.get("tool") == "text" and any(x is sel for x in visible(doc)) \
                and list(sel["color"]) != list(hex_to_rgb(c)):
            st["tsel"] = replace_shape(doc, sel, dict(sel, color=list(hex_to_rgb(c))))
            refresh()

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
        finish_text_edit()
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
        ted = st["tedit"]
        shapes = doc.shapes if not (ted and ted["old"] is not None) else [x for x in doc.shapes if x is not ted["old"]]
        st["photo_r"] = ImageTk.PhotoImage(composite(base, shapes, s))
        canvas_r.create_image(off[0], off[1], anchor="nw", image=st["photo_r"])
        if ted and ted["doc"] is doc:
            ted["font"].configure(size=-editor_px(ted["style"]["size"]))
            cx, cy = to_canvas(ted["p"])
            ted["win"] = canvas_r.create_window(cx, cy, anchor="nw", window=ted["w"])
            fit_editor()
        sel = st["tsel"]
        if st["tool"] == "text" and not ted and sel is not None and any(x is sel for x in visible(doc)):
            x0, y0, x1, y1 = text_canvas_bbox(sel)
            canvas_r.create_rectangle(x0, y0, x1, y1, outline="#ffffff", dash=(4, 3))
            canvas_r.create_rectangle(x1 - 6, y1 - 6, x1 + 6, y1 + 6, outline="#000000", fill="#ffffff")
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
                if sel:  # 변 가운데 손잡이 (동그라미)
                    for k in range(4):
                        (ax, ay), (bx, by) = sh["pts"][k], sh["pts"][(k + 1) % 4]
                        cx, cy = to_canvas(((ax + bx) / 2.0, (ay + by) / 2.0))
                        canvas_r.create_oval(cx - 5, cy - 5, cx + 5, cy + 5, outline="#000000", fill="#ffffff")
        hv = st["hover"]
        if st["tool"] == "eraser" and hv is not None and any(x is hv for x in visible(doc)):
            if hv["type"] == "text":
                x0, y0, x1, y1 = text_canvas_bbox(hv)
            else:
                x0, y0, x1, y1 = shape_bbox(hv, s)
                x0, y0, x1, y1 = x0 + off[0] + 8, y0 + off[1] + 8, x1 + off[0] - 8, y1 + off[1] - 8
            # 점선 + 굵은 선은 윈도우에서 안 그려질 수 있어서 실선
            canvas_r.create_rectangle(x0, y0, x1, y1, outline="#ff5050", width=2)
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

    # ---------- 글자: 이미지 위에서 바로 입력 ----------
    fams = set(tkfont.families(root))
    ui_family = next((f for f in ("Malgun Gothic", "맑은 고딕", "Apple SD Gothic Neo", "AppleGothic",
                                  "Noto Sans CJK KR", "NanumGothic") if f in fams), "TkDefaultFont")

    def text_canvas_bbox(sh):
        x0, y0, x1, y1 = text_bbox(sh, st["scale"])
        ox, oy = st["off"]
        return x0 + ox, y0 + oy, x1 + ox, y1 + oy

    def text_hit(doc, ev):
        """누른 곳: ("size", 도형) 크기 손잡이 / ("move", 도형) 글자 위 / None"""
        sel = st["tsel"]
        if sel is not None and any(x is sel for x in visible(doc)):
            x0, y0, x1, y1 = text_canvas_bbox(sel)
            if abs(ev.x - x1) <= 9 and abs(ev.y - y1) <= 9:
                return "size", sel
        for sh in reversed(visible(doc)):
            if sh["type"] == "text":
                x0, y0, x1, y1 = text_canvas_bbox(sh)
                if x0 <= ev.x <= x1 and y0 <= ev.y <= y1:
                    return "move", sh
        return None

    def editor_px(size):
        return max(6, int(round(size * st["scale"])))

    def fit_editor(*_):
        ted = st["tedit"]
        if not ted or ted.get("win") is None:
            return
        f = ted["font"]
        lines = ted["w"].get("1.0", "end-1c").split("\n")
        wpx = max([f.measure(l) for l in lines] + [f.measure("W")]) + f.measure("WW")
        hpx = f.metrics("linespace") * len(lines) + 6
        canvas_r.itemconfig(ted["win"], width=wpx, height=hpx)

    def start_text_edit(doc, p, old=None):
        finish_text_edit()
        if old is not None:
            style = {k: old[k] for k in ("color", "alpha", "size", "stroke")}
        else:
            style = {"color": list(hex_to_rgb(st["color"])), "alpha": int(round(alpha_var.get() * 2.55)),
                     "size": max(4.0, float(tsize_var.get()) / st["scale"]), "stroke": bool(stroke_var.get())}
        fg = "#%02x%02x%02x" % tuple(style["color"])
        try:  # 입력칸 배경은 밑에 있는 이미지 색으로 (눈에 덜 띄게)
            px = doc.image.getpixel((min(int(p[0]), doc.size[0] - 1), min(int(p[1]), doc.size[1] - 1)))
            bgc = "#%02x%02x%02x" % tuple(px[:3])
        except Exception:
            bgc = "#2a2d33"
        f = tkfont.Font(root, family=ui_family, size=-editor_px(style["size"]), weight="bold")
        w = tk.Text(canvas_r, bd=0, relief="flat", padx=2, pady=0, wrap="none", undo=True, font=f,
                    fg=fg, insertbackground=fg, insertwidth=2, bg=bgc, highlightthickness=1,
                    highlightcolor=fg, highlightbackground=fg, selectbackground="#3b6fd8")
        if old is not None:
            w.insert("1.0", old["text"])
            w.tag_add("sel", "1.0", "end-1c")
            w.mark_set("insert", "end-1c")
        st["tedit"] = {"w": w, "p": tuple(p), "old": old, "style": style, "font": f, "doc": doc, "win": None}
        st["tsel"] = None
        mod_ = "Command" if IS_MAC else "Control"
        w.bind("<KeyRelease>", fit_editor)
        w.bind("<Escape>", lambda e: (finish_text_edit(), "break")[1])
        w.bind("<%s-Return>" % mod_, lambda e: (finish_text_edit(), "break")[1])
        refresh()
        w.focus_set()
        set_status("글자 입력 중…  다른 곳을 클릭하거나 Esc / %s+Enter 로 끝내기" % ("Cmd" if IS_MAC else "Ctrl"))

    def finish_text_edit():
        ted = st["tedit"]
        if not ted:
            return
        st["tedit"] = None
        text = ted["w"].get("1.0", "end-1c").rstrip("\n")
        try:
            ted["w"].destroy()
        except Exception:
            pass
        doc, old = ted["doc"], ted["old"]
        if old is not None:
            if not any(x is old for x in doc.shapes):
                pass
            elif not text.strip():
                replace_shape(doc, old, dict(old, _deleted=True))  # 되돌리기용 기록 (그리지 않음)
                set_status("글자 삭제")
            elif text != old["text"]:
                st["tsel"] = replace_shape(doc, old, dict(old, text=text))
                set_status("글자 고침")
            else:
                st["tsel"] = old
        elif text.strip():
            sh = dict(ted["style"], type="text", text=text, pts=[ted["p"]], width=1.0)
            doc.shapes.append(sh)
            doc.redo.clear()
            st["tsel"] = sh
            set_status("글자 추가 (가이드 %d개)  끌어서 이동 / 오른쪽 아래 손잡이로 크기 / 두 번 클릭해서 고치기"
                       % len(visible(doc)))
        refresh()
        try:
            canvas_r.focus_set()
        except Exception:
            pass

    def on_double(ev):
        doc = cur()
        if not doc or st["tool"] != "text":
            return
        hit = text_hit(doc, ev)
        if hit:
            st["tdrag"] = None
            start_text_edit(doc, hit[1]["pts"][0], old=hit[1])

    def eraser_target(doc, ev):
        """지우개로 가리킨 맨 위 도형"""
        p = ((ev.x - st["off"][0]) / st["scale"], (ev.y - st["off"][1]) / st["scale"])
        tol = 6.0 / st["scale"]
        for sh in reversed(visible(doc)):
            if shape_hit(sh, p, tol):
                return sh
        return None

    def delete_shape(doc, sh):
        replace_shape(doc, sh, dict(sh, _deleted=True))  # 되돌리기로 살릴 수 있게 기록만 남김
        if st["sel"] is sh:
            st["sel"] = None
        if st["tsel"] is sh:
            st["tsel"] = None
        st["hover"] = None
        refresh()
        set_status("%s 지움 (되돌리기: %s+Z)" % (dict(TOOLS).get(sh["type"], "가이드"), "Cmd" if IS_MAC else "Ctrl"))

    def delete_selected(*_):
        doc = cur()
        if not doc or st["tedit"]:
            return
        for sh in (st["sel"] if st["tool"] == "grid" else None, st["tsel"] if st["tool"] == "text" else None):
            if sh is not None and any(x is sh for x in visible(doc)):
                delete_shape(doc, sh)
                return

    def on_wheel(ev):
        """그리드 / 글자 위에서 휠: 가운데 기준 크기 조절"""
        doc = cur()
        if not doc or st["edit"] or st["tdrag"] or st["tedit"]:
            return
        if getattr(ev, "num", None) in (4, 5):
            up = ev.num == 4
        else:
            up = ev.delta > 0
        f = 1.06 if up else 1 / 1.06
        target = None
        if st["tool"] == "grid":
            hit = grid_hit(doc, ev)
            target = hit[1] if hit else None
        elif st["tool"] == "text":
            hit = text_hit(doc, ev)
            target = hit[1] if hit else None
        if target is None:
            return
        import time
        w = st["wheel"]
        now = time.time()
        if w and w["obj"] is target and now - w["t"] < 1.0 and doc.shapes and doc.shapes[-1] is target:
            sh = target  # 이어서 굴리면 같은 기록 안에서 계속 바꿈
        else:
            sh = replace_shape(doc, target, dict(target, pts=[tuple(x) for x in target["pts"]]))
        if sh["type"] == "grid":
            c = (sum(x for x, _ in sh["pts"]) / 4.0, sum(y for _, y in sh["pts"]) / 4.0)
            sh["pts"] = scale_pts(sh["pts"], c, f)
            st["sel"] = sh
        else:
            sh["size"] = max(4.0 / st["scale"], sh["size"] * f)
            st["tsel"] = sh
        st["wheel"] = {"obj": sh, "t": now}
        refresh_soon()
        set_status("%s 크기 조절 (휠)" % ("그리드" if sh["type"] == "grid" else "글자"))
        return "break"

    def on_motion(ev):
        doc = cur()
        cursor = "crosshair"
        if doc and st["tool"] == "text" and not st["tdrag"]:
            hit = text_hit(doc, ev)
            cursor = "xterm" if not hit else ("fleur" if hit[0] == "move" else
                                               ("size_nw_se" if IS_WIN else "bottom_right_corner"))
        elif doc and st["tool"] == "grid" and not st["edit"]:
            hit = grid_hit(doc, ev)
            if not hit:
                cursor = "crosshair"
            elif hit[0] == "corner":
                cursor = ("size_nw_se" if IS_WIN else "bottom_right_corner") if (ev.state & 0x0001) else "crosshair"
            elif hit[0] == "edge":
                (ax, ay), (bx, by) = hit[1]["pts"][hit[2]], hit[1]["pts"][(hit[2] + 1) % 4]
                horiz = abs(bx - ax) >= abs(by - ay)
                cursor = ("size_ns" if horiz else "size_we") if IS_WIN else \
                         ("sb_v_double_arrow" if horiz else "sb_h_double_arrow")
            else:
                cursor = "fleur"
        elif doc and st["tool"] == "eraser":
            hv = eraser_target(doc, ev)
            cursor = "X_cursor" if hv is not None else "arrow"
            if hv is not st["hover"]:
                st["hover"] = hv
                refresh_soon()
        if str(canvas_r.cget("cursor")) != cursor:
            try:
                canvas_r.config(cursor=cursor)
            except Exception:
                canvas_r.config(cursor="crosshair")

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
        """누른 곳: ("corner"/"edge", 도형, 번호) / ("move", 도형, None) / None"""
        grids = [x for x in visible(doc) if x["type"] == "grid"]
        for sh in reversed(grids):
            for k, pt in enumerate(sh["pts"]):
                cx, cy = to_canvas(pt)
                if abs(cx - ev.x) <= 9 and abs(cy - ev.y) <= 9:
                    return "corner", sh, k
        sel = st["sel"]
        if sel is not None and any(x is sel for x in grids):
            for k in range(4):
                (ax, ay), (bx, by) = sel["pts"][k], sel["pts"][(k + 1) % 4]
                cx, cy = to_canvas(((ax + bx) / 2.0, (ay + by) / 2.0))
                if abs(cx - ev.x) <= 8 and abs(cy - ev.y) <= 8:
                    return "edge", sel, k
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
            gw_var.set(max(1, int(round(sh.get("width", 2)))))

    def on_press(ev):
        canvas_r.focus_set()  # 입력칸에 있던 키보드 입력을 화면으로 (단축키가 다시 동작하게)
        doc = cur()
        if not doc:
            return
        p = to_img(ev)
        if st["tool"] == "text":
            st["drawing"] = None
            if st["tedit"]:  # 입력 중에 다른 곳을 누르면 입력 끝
                finish_text_edit()
                return
            hit = text_hit(doc, ev)
            if hit:
                kind, sh = hit
                st["tsel"] = sh
                work = dict(sh, pts=[tuple(sh["pts"][0])])
                i = next(n for n, x in enumerate(doc.shapes) if x is sh)
                doc.shapes[i] = work
                st["tdrag"] = {"kind": kind, "orig": sh, "work": work, "start": (ev.x, ev.y),
                               "p0": tuple(sh["pts"][0]), "size0": sh["size"], "bbox0": text_canvas_bbox(sh)}
                refresh()
                return
            st["tsel"] = None
            start_text_edit(doc, p)
            return
        if st["tool"] == "eraser":
            st["drawing"] = None
            sh = eraser_target(doc, ev)
            if sh is not None:
                delete_shape(doc, sh)
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
                if kind == "corner" and (ev.state & 0x0001):  # Shift + 코너 = 전체 크기 조절
                    kind = "scale"
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
        td = st["tdrag"]
        if td:
            s_ = st["scale"]
            if td["kind"] == "move":
                dx, dy = (ev.x - td["start"][0]) / s_, (ev.y - td["start"][1]) / s_
                td["work"]["pts"] = [(td["p0"][0] + dx, td["p0"][1] + dy)]
            else:  # 왼쪽 위는 그대로 두고 오른쪽 아래 손잡이까지의 거리로 크기 조절
                x0, y0 = td["bbox0"][0], td["bbox0"][1]
                d0 = math.hypot(td["start"][0] - x0, td["start"][1] - y0) or 1.0
                d = math.hypot(ev.x - x0, ev.y - y0)
                td["work"]["size"] = max(4.0 / s_, td["size0"] * d / d0)
            st["tsel"] = td["work"]
            refresh_soon()
            return
        ed = st["edit"]
        if ed:
            # 코너는 이미지 밖으로도 조금 나갈 수 있게 (원근 맞출 때 필요)
            p = ((ev.x - st["off"][0]) / st["scale"], (ev.y - st["off"][1]) / st["scale"])
            if ed["kind"] == "corner":
                ed["work"]["pts"][ed["k"]] = p
            elif ed["kind"] == "scale":  # 반대쪽 코너 기준으로 모양 그대로 키우고 줄이기
                k = ed["k"]
                opp = ed["pts0"][(k + 2) % 4]
                c0 = ed["pts0"][k]
                vx, vy = c0[0] - opp[0], c0[1] - opp[1]
                L = vx * vx + vy * vy or 1.0
                f = max(0.03, ((p[0] - opp[0]) * vx + (p[1] - opp[1]) * vy) / L)
                ed["work"]["pts"] = scale_pts(ed["pts0"], opp, f)
            elif ed["kind"] == "edge":  # 변 하나만 밀고 당기기 (그 변의 두 코너가 같이 움직임)
                k = ed["k"]
                dx, dy = p[0] - ed["start"][0], p[1] - ed["start"][1]
                pts = list(ed["pts0"])
                for j in (k, (k + 1) % 4):
                    pts[j] = (pts[j][0] + dx, pts[j][1] + dy)
                ed["work"]["pts"] = pts
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
                w = max(1, int(round(grid_width() * st["scale"])))
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
        td = st["tdrag"]
        if td:
            st["tdrag"] = None
            doc = cur()
            work, orig = td["work"], td["orig"]
            i = next((n for n, x in enumerate(doc.shapes) if x is work), None)
            if i is None:
                return
            doc.shapes[i] = orig
            if work["pts"] == [tuple(orig["pts"][0])] and work["size"] == orig["size"]:
                st["tsel"] = orig
            else:
                st["tsel"] = replace_shape(doc, orig, work)
                tsize_var.set(int(round(work["size"] * st["scale"])))
                tsize_lbl.config(text="%dpx" % tsize_var.get())
                set_status("글자 %s" % ("이동" if td["kind"] == "move" else "크기 조절"))
            refresh()
            return
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
                set_status("그리드 %s" % {"corner": "코너 이동", "scale": "크기 조절", "edge": "변 이동"}.get(ed["kind"], "이동"))
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
            sh["width"] = float(grid_width())  # 그리드 선 굵기는 실제 이미지 픽셀 기준
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
        finish_text_edit()
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
        mode = mode_var.get()
        exist = [n for d in docs for n in out_names(d, mode) if os.path.exists(os.path.join(out, n))]
        if exist and not messagebox.askyesno(
                APP_NAME, "같은 이름의 파일이 %d개 있습니다. 덮어쓸까요?\n\n%s"
                % (len(exist), "\n".join(exist[:8])), parent=root):
            return
        root.config(cursor="watch")
        root.update_idletasks()
        ok, fail = [], []
        for i, d in enumerate(docs):
            set_status("내보내는 중… %d / %d  %s" % (i + 1, len(docs), d.name))
            root.update_idletasks()
            try:
                ok += export_doc(d, out, mode, layout_var.get(), bool(divider_var.get()), bool(labels_var.get()))
            except Exception as e:
                fail.append("%s: %s" % (d.name, e))
        root.config(cursor="")
        settings["out_dir"] = out
        save_settings(settings)
        set_status("내보내기 완료: %d개 파일 -> %s" % (len(ok), out) + ("  (실패 %d)" % len(fail) if fail else ""))
        msg = "%d개 파일을 저장했습니다.\n%s" % (len(ok), out)
        if len(ok) == 1:
            msg = "저장했습니다.\n%s" % ok[0]
        if fail:
            msg += "\n\n실패:\n" + "\n".join(fail[:10])
        if messagebox.askyesno(APP_NAME, msg + "\n\n저장 폴더를 열까요?", parent=root):
            open_out_folder()

    def save_ui_settings():
        settings.update(tool=st["tool"], color=st["color"], opacity=int(alpha_var.get()),
                        width=int(tool_widths.get("pen", width_var.get()) if st["tool"] != "pen" else width_var.get()),
                        tool_widths=dict(tool_widths, **{st["tool"]: int(width_var.get())}),
                        text_size=int(tsize_var.get()),
                        text_stroke=bool(stroke_var.get()), out_dir=out_var.get().strip(),
                        export_mode=mode_var.get(), export_layout=layout_var.get(),
                        export_divider=bool(divider_var.get()), export_labels=bool(labels_var.get()),
                        grid_cols=get_counts()[0], grid_rows=get_counts()[1], grid_width=grid_width(),
                        geometry=root.geometry())
        save_settings(settings)

    def on_close():
        finish_text_edit()
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
        apply_style_to_grid()

    last_gw = [max(1, min(50, int(gw_var.get())))]

    def grid_width():
        """그리드 선 굵기 입력칸 (잘못 입력하면 마지막 올바른 값)"""
        try:
            v = int(str(gw_var.get()).strip())
            if not 1 <= v <= 50:
                raise ValueError
            last_gw[0] = v
        except Exception:
            gw_var.set(last_gw[0])
        return last_gw[0]

    def apply_style_to_grid():
        """그리드 도구에서 선택한 그리드가 있으면 지금 색/굵기/투명도를 그 그리드에 적용"""
        doc, sh = cur(), st["sel"]
        if not doc or st["tool"] != "grid" or sh is None or not any(x is sh for x in visible(doc)):
            return
        new = dict(sh, color=list(hex_to_rgb(st["color"])), width=float(grid_width()),
                   alpha=int(round(alpha_var.get() * 2.55)))
        if (new["color"], round(new["width"], 3), new["alpha"]) != (list(sh["color"]), round(sh["width"], 3), sh["alpha"]):
            st["sel"] = replace_shape(doc, sh, new)
            refresh()
            set_status("선택한 그리드 색/굵기 바꿈")

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
    canvas_r.bind("<Double-Button-1>", on_double)
    canvas_r.bind("<Motion>", on_motion)
    canvas_r.bind("<MouseWheel>", on_wheel)       # 윈도우 / 맥
    canvas_r.bind("<Button-4>", on_wheel)         # 리눅스
    canvas_r.bind("<Button-5>", on_wheel)
    root.bind("<Delete>", key(delete_selected))
    root.bind("<BackSpace>", key(delete_selected))
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
    update_export_opts()
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
