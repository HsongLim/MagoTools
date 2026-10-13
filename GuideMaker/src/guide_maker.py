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
APP_VERSION = "1.1.4"
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

TOOLS = [  # (키, 이름)  숫자키 1~9 순서
    ("select", "선택"),
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


# ---------------------------------------------------------------------------
# 선택한 가이드 편집 (손잡이, 이동, 크기)
# ---------------------------------------------------------------------------
def shape_bounds(sh):
    """도형이 차지하는 영역 (원본 좌표, 여백 없음)"""
    if sh["type"] == "text":
        return text_bbox(sh, 1.0)
    xs = [x for x, _ in sh["pts"]]
    ys = [y for _, y in sh["pts"]]
    return min(xs), min(ys), max(xs), max(ys)


def _rect4(sh):
    (x0, y0), (x1, y1) = sh["pts"][0], sh["pts"][-1]
    l, r, t, b = min(x0, x1), max(x0, x1), min(y0, y1), max(y0, y1)
    return [(l, t), (r, t), (r, b), (l, b)]


def shape_handles(sh):
    """손잡이 목록 [(종류, 번호, (x, y))]  (원본 좌표)"""
    t = sh["type"]
    if t == "grid":
        pts = sh["pts"]
        out = [("corner", k, pts[k]) for k in range(4)]
        out += [("edge", k, ((pts[k][0] + pts[(k + 1) % 4][0]) / 2.0, (pts[k][1] + pts[(k + 1) % 4][1]) / 2.0))
                for k in range(4)]
        return out
    if t in ("rect", "fillrect"):
        return [("rcorner", k, c) for k, c in enumerate(_rect4(sh))]
    if t in ("line", "arrow"):
        return [("end", 0, sh["pts"][0]), ("end", 1, sh["pts"][-1])]
    if t == "pen":
        x0, y0, x1, y1 = shape_bounds(sh)
        return [("bcorner", k, c) for k, c in enumerate([(x0, y0), (x1, y0), (x1, y1), (x0, y1)])]
    if t == "text":
        x0, y0, x1, y1 = text_bbox(sh, 1.0)
        return [("tsize", 0, (x1, y1))]
    return []


def shape_center(sh):
    x0, y0, x1, y1 = shape_bounds(sh)
    return (x0 + x1) / 2.0, (y0 + y1) / 2.0


def scale_shape(sh, f, center=None):
    """가운데(또는 center) 기준으로 f 배"""
    if sh["type"] == "text":
        return dict(sh, size=max(2.0, sh["size"] * f))
    c = center or shape_center(sh)
    return dict(sh, pts=scale_pts(sh["pts"], c, f))


def edit_shape(orig, kind, k, p, start, shift=False):
    """손잡이/이동 끌기 결과 (orig 는 끌기 시작할 때 모양, 새 dict 를 돌려줌)"""
    dx, dy = p[0] - start[0], p[1] - start[1]
    pts = [tuple(x) for x in orig["pts"]]
    if kind == "move":
        return dict(orig, pts=[(x + dx, y + dy) for x, y in pts])
    if kind == "corner":  # 그리드 코너핀 (Shift: 반대쪽 코너 기준 크기)
        if shift:
            opp, c0 = pts[(k + 2) % 4], pts[k]
            vx, vy = c0[0] - opp[0], c0[1] - opp[1]
            L = vx * vx + vy * vy or 1.0
            f = max(0.03, ((p[0] - opp[0]) * vx + (p[1] - opp[1]) * vy) / L)
            return dict(orig, pts=scale_pts(pts, opp, f))
        pts[k] = tuple(p)
        return dict(orig, pts=pts)
    if kind == "edge":  # 그리드 변 하나 밀고 당기기
        for j in (k, (k + 1) % 4):
            pts[j] = (pts[j][0] + dx, pts[j][1] + dy)
        return dict(orig, pts=pts)
    if kind == "rcorner":  # 박스 모서리: 반대쪽 모서리는 그대로 (Shift: 비율 유지)
        r4 = _rect4(orig)
        opp = r4[(k + 2) % 4]
        if shift:
            c0 = r4[k]
            w0, h0 = (c0[0] - opp[0]) or 1e-6, (c0[1] - opp[1]) or 1e-6
            f = max(abs((p[0] - opp[0]) / w0), abs((p[1] - opp[1]) / h0))
            p = (opp[0] + w0 * f, opp[1] + h0 * f)
        return dict(orig, pts=[opp, tuple(p)])
    if kind == "end":  # 직선/화살표 끝점
        if k == 0:
            pts[0] = tuple(p)
        else:
            pts[-1] = tuple(p)
        return dict(orig, pts=pts)
    if kind == "bcorner":  # 펜: 반대쪽 모서리 기준으로 늘이고 줄이기 (Shift: 비율 유지)
        x0, y0, x1, y1 = shape_bounds(orig)
        corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
        opp, c0 = corners[(k + 2) % 4], corners[k]
        w0, h0 = (c0[0] - opp[0]) or 1e-6, (c0[1] - opp[1]) or 1e-6
        fx, fy = (p[0] - opp[0]) / w0, (p[1] - opp[1]) / h0
        if shift:
            fx = fy = max(abs(fx), abs(fy))
        return dict(orig, pts=[(opp[0] + (x - opp[0]) * fx, opp[1] + (y - opp[1]) * fy) for x, y in pts])
    if kind == "tsize":  # 글자: 왼쪽 위는 그대로, 오른쪽 아래 손잡이까지 거리로 크기
        x0, y0, x1, y1 = text_bbox(orig, 1.0)
        d0 = math.hypot(start[0] - x0, start[1] - y0) or 1.0
        d = math.hypot(p[0] - x0, p[1] - y0)
        return dict(orig, size=max(2.0, orig["size"] * d / d0))
    return dict(orig)


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
# 설명서 (앱 안 [?] 창, 설치 파일의 사용법 txt 가 모두 여기서 만들어짐 - 기능이 바뀌면 여기를 고칠 것)
# ============================================================================
# 한글 입력 상태에서도 Ctrl / Cmd 단축키가 되게: 영문 글자가 안 오면 한글 자모나 키 위치로 알아봄
_HANGUL_KEYS = {"ㅋ": "z", "ㅛ": "y", "ㅐ": "o", "ㄷ": "e", "ㅍ": "v"}
_WIN_VK = {90: "z", 89: "y", 79: "o", 69: "e", 86: "v"}
_MAC_VK = {6: "z", 16: "y", 31: "o", 14: "e", 9: "v"}


def shortcut_letter(keysym, char, keycode, mac=None):
    mac = IS_MAC if mac is None else mac
    ks = keysym or ""
    if len(ks) == 1 and ks.isascii() and ks.isalpha():
        return ks.lower()
    for c in (char or "", ks):
        if c in _HANGUL_KEYS:
            return _HANGUL_KEYS[c]
    kc = keycode or 0
    if mac:  # 맥 Tk 는 키 위치 번호를 위쪽 비트에 넣어 줌 (버전에 따라 그대로 주기도 함)
        for v in ((kc >> 24) & 0xFF, kc):
            if v in _MAC_VK:
                return _MAC_VK[v]
    elif kc in _WIN_VK:
        return _WIN_VK[kc]
    return ""


def help_text(mac=None):
    mac = IS_MAC if mac is None else mac
    m = "Cmd" if mac else "Ctrl"
    t = """Guide Maker v{ver} 사용법
========================

1. 이미지 불러오기
   - [이미지 불러오기] (한 장 / 여러 장),  [폴더 불러오기]
   - 이미지 파일이나 폴더를 창에 끌어다 놓아도 됩니다
   - 지원: jpg, png, tif, bmp, webp, tga  (EXR 은 지원 안 함)
   - ◀ ▶ 버튼이나 ← → 키로 이미지 넘기기

2. 화면
   - 왼쪽 = 원본,  오른쪽 = 가이드를 그리는 곳

3. 도구 (숫자 키 1~9)
   1 선택      클릭한 가이드를 선택 (옮기기 / 크기 / 색 바꾸기 / 지우기)
   2 펜        자유롭게 그리기
   3 박스      4 채운 박스      5 직선      6 화살표
   7 그리드    드래그로 만들기
   8 글자      이미지 위를 클릭한 자리에서 바로 입력
   9 지우개    클릭한 가이드 하나만 지우기 (마우스를 올리면 빨간 테두리)

   - 어느 도구에서든 {m} 를 누른 채 드래그하면 화살표가 그려집니다
   - 그리기 도구에서도 이미 있는 가이드를 "클릭"하면 선택, "드래그"하면 새로 그리기

4. 선택한 가이드 고치기  (모든 가이드 공통)
   - 끌기: 옮기기
   - 손잡이 끌기
       박스        네 모서리 (Shift: 비율 유지)
       직선/화살표  양 끝 점 -> 방향과 길이 바꾸기
       펜          네 모서리로 늘이기 / 줄이기 (Shift: 비율 유지)
       글자        오른쪽 아래 네모로 크기
       그리드      코너 = 코너핀 (원근에 맞게),  Shift + 코너 = 모양 그대로 크기,
                   변 가운데 동그라미 = 그 변만 밀고 당기기
   - 마우스 휠: 가이드 위에서 굴리면 가운데 기준으로 확대 / 축소
   - 선택한 채 색 / 투명도 / 굵기 / 글자 크기 / 글자 테두리 / 그리드 칸·선 굵기를 바꾸면 바로 적용
   - Delete 또는 Backspace: 선택한 가이드 지우기
   - Esc: 선택 풀기
   - 글자는 두 번 클릭하면 내용 고치기 (모두 지우면 삭제)

5. 설정
   - 색: 왼쪽 큰 칸을 누르면 색 고르기, 옆의 작은 칸은 빠른 색
   - 투명도 5~100%,  굵기 1~60 (도구마다 따로 기억)
   - 글자 크기, 글자 테두리 (기본 꺼짐)
   - 그리드 칸: 가로 / 세로 칸 수 (기본 10 x 10),  선 굵기 (실제 이미지 픽셀, 기본 2px)

6. 내보내기
   - 아래쪽 [저장 폴더] 지정 -> [현재 이미지 내보내기] / [전체 내보내기]
   - 방식
       가이드만              shot_0010_guide.png
       원본 + 가이드 붙이기  shot_0010_compare.png  (원본과 가이드를 한 장에)
       둘 다                 두 파일 모두
     붙일 때: 좌우 / 위아래,  구분선 켜기 / 끄기,  이름표(원본 / 가이드) 켜기 / 끄기
   - 모두 원본 해상도 그대로 저장
   - 화면의 손잡이, 선택 테두리 같은 표시는 저장되지 않습니다

7. 업데이트
   - 켤 때 새 버전이 있으면 창이 뜹니다. [예] 를 누르면 받아서 설치하고 다시 켜집니다
   - 오른쪽 위 [업데이트 확인] 으로 직접 확인할 수도 있습니다

단축키
   {m}+O  불러오기        {m}+E  현재 이미지 내보내기
   {m}+Z  되돌리기        {m}+Y / {m}+Shift+Z  다시 실행
   1~9    도구 선택       V  선택 도구
   ← / →  이전 / 다음 이미지
   [ / ]  굵기 줄이기 / 늘리기
   Delete / Backspace  선택한 가이드 지우기,  Esc  선택 풀기
   (한글 입력 상태에서도 {m} 단축키가 동작합니다)
""".format(ver=APP_VERSION, m=m)
    if mac:
        t += """
처음 설치 / 실행 (맥)
   - dmg 를 열고 "Guide Maker" 를 Applications 폴더로 끌어다 놓기
   - 처음 열 때 "열지 않음" 경고가 뜨면: [완료] -> 시스템 설정 -> 개인정보 보호 및 보안 -> [그래도 열기]
     또는 터미널에:  xattr -cr "/Applications/Guide Maker.app"
"""
    else:
        t += """
처음 실행 (윈도우)
   - "Windows의 PC 보호" 창이 뜨면 [추가 정보] -> [실행]
"""
    t += "\n문제가 생기면 홈 폴더의 guide_maker_error.txt 를 보내주세요.\n"
    return t


HELP_SHORT = """도움말 (클릭하면 설명서)

1 선택  2 펜  3 박스  4 채운 박스  5 직선
6 화살표  7 그리드  8 글자  9 지우개
{m} + 드래그 = 화살표
가이드를 클릭해서 선택 -> 끌기 / 손잡이 / 휠
Delete · Backspace = 지우기,  Esc = 선택 풀기"""


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
    root.minsize(760, 520)
    try:
        for d in (HERE, BUNDLE):
            ico = os.path.join(d, "GuideMaker.ico")
            if IS_WIN and os.path.isfile(ico):
                root.iconbitmap(ico)
                break
    except Exception:
        pass
    MOD = "Cmd" if IS_MAC else "Ctrl"

    tool_keys = [k for k, _ in TOOLS]
    st = {
        "docs": [], "idx": -1,
        "tool": settings.get("tool", "pen") if settings.get("tool") in tool_keys else "pen",
        "color": settings.get("color", "#ff2a2a"),
        "drawing": None,      # 그리는 중인 도형
        "temp_items": [],     # 그리는 중 미리보기용 캔버스 항목
        "scale": 1.0, "off": (0, 0),
        "cache": {},          # (doc id, 크기) -> 축소한 원본
        "photo_l": None, "photo_r": None,
        "sel": None,          # 선택한 가이드 (도형 객체)
        "op": None,           # 선택한 가이드 끌기 (이동 / 손잡이)
        "press": None,        # 누른 곳 (클릭인지 드래그인지 구분)
        "pending": False,
        "tedit": None,        # 글자 입력 중
        "hover": None,        # 지우개가 가리키는 도형
        "wheel": None,        # 휠로 크기 조절 중인 도형 (연속 휠은 되돌리기 한 번으로)
    }

    # =====================================================================
    # 창 크기에 맞게 줄이 바뀌는 막대 (좁아지면 다음 줄로)
    # =====================================================================
    class Flow(ttk.Frame):
        def __init__(self, master, gap=14, vgap=4, pad=(8, 6), **kw):
            ttk.Frame.__init__(self, master, **kw)
            self.items, self.gap, self.vgap, self.pad = [], gap, vgap, pad
            self._h = 0
            self.bind("<Configure>", lambda e: self.after_idle(self.layout))

        def group(self):
            f = ttk.Frame(self)
            self.items.append(f)
            return f

        def layout(self):
            W = self.winfo_width()
            if W <= 1:
                return
            px, py = self.pad
            x, y, rowh = px, py, 0
            for w in self.items:
                rw, rh = w.winfo_reqwidth(), w.winfo_reqheight()
                if x > px and x + rw > W - px:
                    x, y = px, y + rowh + self.vgap
                    rowh = 0
                w.place(x=x, y=y)
                x += rw + self.gap
                rowh = max(rowh, rh)
            h = y + rowh + py
            if h != self._h:
                self._h = h
                self.config(height=h)

    # =====================================================================
    # 위쪽 막대
    # =====================================================================
    top = Flow(root)
    top.pack(fill="x")

    g = top.group()
    ttk.Button(g, text="이미지 불러오기", command=lambda: open_files()).pack(side="left")
    ttk.Button(g, text="폴더 불러오기", command=lambda: open_folder()).pack(side="left", padx=(4, 0))

    g = top.group()
    ttk.Button(g, text="◀", width=3, command=lambda: go(-1)).pack(side="left")
    nav_lbl = ttk.Label(g, text="이미지 없음", width=26, anchor="center")
    nav_lbl.pack(side="left", padx=4)
    ttk.Button(g, text="▶", width=3, command=lambda: go(1)).pack(side="left")

    g = top.group()
    tool_var = tk.StringVar(value=st["tool"])
    for i, (k, label) in enumerate(TOOLS):
        ttk.Radiobutton(g, text=label, value=k, variable=tool_var,
                        command=lambda: set_tool(tool_var.get())).pack(side="left", padx=1)

    g = top.group()
    ttk.Label(g, text="그리드 칸  가로").pack(side="left")
    cols_var = tk.IntVar(value=int(settings.get("grid_cols", 10)))
    rows_var = tk.IntVar(value=int(settings.get("grid_rows", 10)))
    gw_var = tk.IntVar(value=int(settings.get("grid_width", 2)))
    cols_sp = ttk.Spinbox(g, from_=1, to=64, width=4, textvariable=cols_var, command=lambda: apply_style("grid"))
    cols_sp.pack(side="left", padx=(4, 6))
    ttk.Label(g, text="세로").pack(side="left")
    rows_sp = ttk.Spinbox(g, from_=1, to=64, width=4, textvariable=rows_var, command=lambda: apply_style("grid"))
    rows_sp.pack(side="left", padx=4)
    ttk.Label(g, text="선 굵기").pack(side="left", padx=(6, 0))
    gw_sp = ttk.Spinbox(g, from_=1, to=50, width=4, textvariable=gw_var, command=lambda: apply_style("grid"))
    gw_sp.pack(side="left", padx=(4, 0))
    ttk.Label(g, text="px").pack(side="left", padx=(2, 0))
    for sp in (cols_sp, rows_sp, gw_sp):
        sp.bind("<Return>", lambda e: apply_style("grid"))
        sp.bind("<FocusOut>", lambda e: apply_style("grid"))

    g = top.group()
    ttk.Label(g, text="색").pack(side="left")
    # 맥에서는 버튼 배경색이 안 보여서 색 칸은 캔버스로 그림
    cur_sw = tk.Canvas(g, width=30, height=20, highlightthickness=1, highlightbackground="#777", bd=0,
                       cursor="hand2")
    cur_sw.pack(side="left", padx=(4, 6))
    cur_sw.bind("<Button-1>", lambda e: pick_color())
    for c in QUICK_COLORS:
        sw = tk.Canvas(g, width=18, height=20, highlightthickness=1, highlightbackground="#777", bd=0,
                       bg=c, cursor="hand2")
        sw.create_rectangle(0, 0, 40, 40, fill=c, outline=c)
        sw.pack(side="left", padx=1)
        sw.bind("<Button-1>", lambda e, c=c: set_color(c, user=True))

    def slider(parent, label, var, lo, hi, length, fmt, on_release):
        ttk.Label(parent, text=label).pack(side="left")
        lbl = ttk.Label(parent, width=5)
        sc = ttk.Scale(parent, from_=lo, to=hi, variable=var, length=length,
                       command=lambda v: lbl.config(text=fmt % float(v)))
        sc.pack(side="left", padx=4)
        sc.bind("<ButtonRelease-1>", lambda e: root.after_idle(on_release))
        lbl.pack(side="left")
        lbl.config(text=fmt % var.get())
        return lbl

    g = top.group()
    alpha_var = tk.IntVar(value=int(settings.get("opacity", 100)))
    alpha_lbl = slider(g, "투명도", alpha_var, 5, 100, 110, "%d%%", lambda: apply_style("alpha"))

    # 굵기는 도구마다 따로 기억
    tool_widths = dict(settings.get("tool_widths") or {})

    def tool_width(t):
        return int(tool_widths.get(t, settings.get("width", 6)))

    g = top.group()
    width_var = tk.IntVar(value=tool_width(st["tool"]))
    width_lbl = slider(g, "굵기", width_var, 1, 60, 110, "%dpx", lambda: apply_style("width"))

    g = top.group()
    tsize_var = tk.IntVar(value=int(settings.get("text_size", 32)))
    tsize_lbl = slider(g, "글자 크기", tsize_var, 10, 150, 100, "%dpx", lambda: apply_style("tsize"))
    stroke_var = tk.BooleanVar(value=bool(settings.get("text_stroke_v2", False)))  # 기본은 테두리 없음
    ttk.Checkbutton(g, text="글자 테두리", variable=stroke_var,
                    command=lambda: apply_style("stroke")).pack(side="left", padx=(6, 0))

    g = top.group()
    ttk.Button(g, text="실행 취소", command=lambda: undo()).pack(side="left")
    ttk.Button(g, text="다시 실행", command=lambda: redo()).pack(side="left", padx=4)
    ttk.Button(g, text="선택 지우기", command=lambda: delete_selected()).pack(side="left")
    ttk.Button(g, text="이 이미지 가이드 모두 지우기", command=lambda: clear_doc()).pack(side="left", padx=(4, 0))

    g = top.group()
    ttk.Label(g, text="v" + APP_VERSION, foreground="#888").pack(side="left", padx=(0, 6))
    upd_btn = ttk.Button(g, text="업데이트 확인", command=lambda: check_updates(True))
    upd_btn.pack(side="left")

    # =====================================================================
    # 좌우 화면
    # =====================================================================
    panes = ttk.PanedWindow(root, orient="horizontal")
    bg = "#2a2d33"

    def make_panel(title):
        f = ttk.Frame(panes)
        ttk.Label(f, text=title, anchor="center").pack(fill="x")
        c = tk.Canvas(f, bg=bg, highlightthickness=0, cursor="arrow", width=200, height=150)
        c.pack(fill="both", expand=True)
        panes.add(f, weight=1)
        return c

    canvas_l = make_panel("원본")
    canvas_r = make_panel("가이드 (여기에 그리세요)")
    canvas_r.config(cursor="crosshair")

    # =====================================================================
    # 아래쪽: 내보내기 / 저장 폴더 / 상태 + 도움말
    # =====================================================================
    bottom = ttk.Frame(root)
    bottom.pack(side="bottom", fill="x")
    opt = Flow(bottom, pad=(8, 4))
    opt.pack(fill="x")
    g = opt.group()
    ttk.Label(g, text="내보내기").pack(side="left")
    mode_var = tk.StringVar(value=settings.get("export_mode", "guide"))
    for val, label in (("guide", "가이드만"), ("compare", "원본 + 가이드 붙이기"), ("both", "둘 다")):
        ttk.Radiobutton(g, text=label, value=val, variable=mode_var,
                        command=lambda: update_export_opts()).pack(side="left", padx=(8, 0))
    g = opt.group()
    ttk.Label(g, text="배치").pack(side="left")
    layout_var = tk.StringVar(value=settings.get("export_layout", "h"))
    layout_rbs = [ttk.Radiobutton(g, text=t, value=v, variable=layout_var) for v, t in (("h", "좌우"), ("v", "위아래"))]
    for rb in layout_rbs:
        rb.pack(side="left", padx=(6, 0))
    divider_var = tk.BooleanVar(value=bool(settings.get("export_divider", True)))
    labels_var = tk.BooleanVar(value=bool(settings.get("export_labels", False)))
    divider_cb = ttk.Checkbutton(g, text="구분선", variable=divider_var)
    divider_cb.pack(side="left", padx=(12, 0))
    labels_cb = ttk.Checkbutton(g, text="이름표 (원본 / 가이드)", variable=labels_var)
    labels_cb.pack(side="left", padx=(8, 0))
    name_lbl = ttk.Label(opt.group(), foreground="#888")
    name_lbl.pack(side="left")

    def update_export_opts():
        on = mode_var.get() != "guide"
        for w in layout_rbs + [divider_cb, labels_cb]:
            w.state(["!disabled"] if on else ["disabled"])
        name_lbl.config(text={"guide": "→ 이름_guide", "compare": "→ 이름_compare",
                              "both": "→ 이름_guide + 이름_compare"}[mode_var.get()])
        root.after_idle(opt.layout)

    bot = ttk.Frame(bottom, padding=(8, 4, 8, 4))
    bot.pack(fill="x")
    ttk.Label(bot, text="저장 폴더").pack(side="left")
    out_var = tk.StringVar(value=settings.get("out_dir", ""))
    btns = ttk.Frame(bot)
    btns.pack(side="right")
    ttk.Entry(bot, textvariable=out_var, width=10).pack(side="left", fill="x", expand=True, padx=6)
    ttk.Button(btns, text="찾기…", command=lambda: out_var.set(
        filedialog.askdirectory(initialdir=out_var.get() or None, parent=root) or out_var.get())).pack(side="left")
    ttk.Button(btns, text="열기", command=lambda: open_out_folder()).pack(side="left", padx=(4, 10))
    ttk.Button(btns, text="현재 이미지 내보내기", command=lambda: export(False)).pack(side="left")
    ttk.Button(btns, text="전체 내보내기", command=lambda: export(True)).pack(side="left", padx=(4, 0))

    sbar = ttk.Frame(bottom, padding=(10, 0, 8, 6))
    sbar.pack(fill="x")
    status = ttk.Label(sbar, text="이미지를 불러오거나 창에 끌어다 놓으세요.  (%s+O)" % MOD, anchor="w")
    status.pack(side="left", fill="x", expand=True)
    sbar.bind("<Configure>", lambda e: status.config(wraplength=max(200, e.width - 60)))
    help_btn = tk.Canvas(sbar, width=24, height=24, highlightthickness=0, bd=0, cursor="hand2",
                         bg=ttk.Style().lookup("TFrame", "background") or "#f0f0f0")
    help_btn.create_oval(2, 2, 22, 22, fill="#3b6fd8", outline="#3b6fd8")
    help_btn.create_text(12, 12, text="?", fill="#ffffff", font=("TkDefaultFont", 11, "bold"))
    help_btn.pack(side="right")
    panes.pack(fill="both", expand=True, padx=8)   # 아래 막대를 먼저 자리잡게 한 뒤 나머지를 화면에

    # ---------- 도움말 ----------
    tip = {"w": None}

    def show_tip(e=None):
        hide_tip()
        w = tk.Toplevel(root)
        w.wm_overrideredirect(True)
        try:
            w.attributes("-topmost", True)
        except Exception:
            pass
        ttk.Label(w, text=HELP_SHORT.format(m=MOD), justify="left", padding=10,
                  background="#fffbe6", relief="solid", borderwidth=1).pack()
        w.update_idletasks()
        x = help_btn.winfo_rootx() - w.winfo_reqwidth() + 20
        y = help_btn.winfo_rooty() - w.winfo_reqheight() - 6
        w.geometry("+%d+%d" % (max(0, x), max(0, y)))
        tip["w"] = w

    def hide_tip(e=None):
        if tip["w"] is not None:
            try:
                tip["w"].destroy()
            except Exception:
                pass
            tip["w"] = None

    help_win = {"w": None}

    def open_help(e=None):
        hide_tip()
        if help_win["w"] is not None and help_win["w"].winfo_exists():
            help_win["w"].lift()
            return
        w = tk.Toplevel(root)
        w.title("%s 설명서" % APP_NAME)
        w.geometry("680x720")
        frm = ttk.Frame(w, padding=8)
        frm.pack(fill="both", expand=True)
        txt = tk.Text(frm, wrap="word", padx=10, pady=8, relief="flat", font=(ui_family, 11))
        sb = ttk.Scrollbar(frm, orient="vertical", command=txt.yview)
        txt.config(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        txt.pack(side="left", fill="both", expand=True)
        txt.insert("1.0", help_text())
        txt.config(state="disabled")
        ttk.Button(w, text="닫기", command=w.destroy).pack(pady=(0, 8))
        help_win["w"] = w

    help_btn.bind("<Enter>", show_tip)
    help_btn.bind("<Leave>", hide_tip)
    help_btn.bind("<Button-1>", open_help)

    # =====================================================================
    # 기본 동작
    # =====================================================================
    def cur():
        return st["docs"][st["idx"]] if 0 <= st["idx"] < len(st["docs"]) else None

    def alive(sh):
        doc = cur()
        return doc is not None and sh is not None and any(x is sh for x in visible(doc))

    def set_status(t):
        status.config(text=t)

    def set_tool(t):
        if t not in tool_keys:
            return
        if t != "text":
            finish_text_edit()
        if t != st["tool"] and st["tool"] not in ("select", "eraser"):
            tool_widths[st["tool"]] = int(width_var.get())
        if t not in ("select", "eraser"):
            width_var.set(tool_width(t))
            width_lbl.config(text="%dpx" % width_var.get())
        st["tool"] = t
        st["hover"] = None
        tool_var.set(t)
        if cur():
            refresh()

    def draw_color_swatch():
        cur_sw.delete("all")
        cur_sw.create_rectangle(0, 0, 60, 40, fill=st["color"], outline=st["color"])

    def set_color(c, user=False):
        st["color"] = c
        draw_color_swatch()
        ted = st.get("tedit")
        if ted:
            ted["style"]["color"] = list(hex_to_rgb(c))
            ted["w"].config(fg=c, insertbackground=c, highlightcolor=c, highlightbackground=c)
            return
        if user:
            apply_style("color")

    def pick_color():
        c = colorchooser.askcolor(color=st["color"], parent=root, title="가이드 색")
        if c and c[1]:
            set_color(c[1], user=True)

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
        st["sel"] = None
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
            st["sel"] = None
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

    def to_img(ev, clamp=True):
        s, (ox, oy) = st["scale"], st["off"]
        x, y = (ev.x - ox) / s, (ev.y - oy) / s
        doc = cur()
        if clamp and doc:
            x = min(max(x, 0), doc.size[0])
            y = min(max(y, 0), doc.size[1])
        return (x, y)

    def to_canvas(p):
        s, (ox, oy) = st["scale"], st["off"]
        return (p[0] * s + ox, p[1] * s + oy)

    def canvas_bounds(sh, pad=0):
        x0, y0, x1, y1 = shape_bounds(sh)
        a, b = to_canvas((x0, y0)), to_canvas((x1, y1))
        return a[0] - pad, a[1] - pad, b[0] + pad, b[1] + pad

    def draw_selection(doc, sh):
        """선택 표시 + 손잡이 (화면에만, 저장되지 않음)"""
        t = sh["type"]
        if t != "grid":
            pad = max(4, sh.get("width", 1) * st["scale"] / 2 + 3) if t != "text" else 2
            x0, y0, x1, y1 = canvas_bounds(sh, pad)
            canvas_r.create_rectangle(x0, y0, x1, y1, outline="#ffffff", dash=(4, 3))
        for kind, k, pt in shape_handles(sh):
            cx, cy = to_canvas(pt)
            if kind in ("edge", "end"):
                canvas_r.create_oval(cx - 6, cy - 6, cx + 6, cy + 6, outline="#000000", fill="#ffffff")
            else:
                canvas_r.create_rectangle(cx - 6, cy - 6, cx + 6, cy + 6, outline="#000000", fill="#ffffff")

    def refresh(*_):
        doc = cur()
        for c in (canvas_l, canvas_r):
            c.delete("all")
        if not doc:
            nav_lbl.config(text="이미지 없음")
            for c in (canvas_l, canvas_r):
                c.create_text(c.winfo_width() // 2, c.winfo_height() // 2, fill="#8a9099",
                              text="'이미지 불러오기'로 이미지를 열거나\n이미지 파일/폴더를 여기에 끌어다 놓으세요",
                              justify="center")
            return
        name = doc.name if len(doc.name) <= 24 else doc.name[:21] + "…"
        nav_lbl.config(text="%d / %d   %s%s" % (st["idx"] + 1, len(st["docs"]), name, " ●" if visible(doc) else ""))
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
        if st["tool"] == "grid":  # 그리드 도구: 모든 그리드의 코너를 바로 잡을 수 있게
            for sh in visible(doc):
                if sh["type"] == "grid" and sh is not st["sel"]:
                    for x, y in sh["pts"]:
                        cx, cy = to_canvas((x, y))
                        canvas_r.create_rectangle(cx - 5, cy - 5, cx + 5, cy + 5, outline="#000000", fill="#9aa3ad")
        sel = st["sel"]
        if st["tool"] != "eraser" and not ted and alive(sel):
            draw_selection(doc, sel)
        hv = st["hover"]
        if st["tool"] == "eraser" and alive(hv):
            x0, y0, x1, y1 = canvas_bounds(hv, max(4, hv.get("width", 1) * s / 2 + 3))
            # 점선 + 굵은 선은 윈도우에서 안 그려질 수 있어서 실선
            canvas_r.create_rectangle(x0, y0, x1, y1, outline="#ff5050", width=2)
        canvas_r.create_text(8, 8, anchor="nw", fill="#8a9099",
                             text="%d×%d  가이드 %d개" % (doc.size[0], doc.size[1], len(visible(doc))))

    def refresh_soon():
        if not st["pending"]:
            st["pending"] = True

            def run():
                st["pending"] = False
                refresh()
            root.after(15, run)

    # =====================================================================
    # 되돌리기 기록 / 선택
    # =====================================================================
    def replace_shape(doc, sh, new):
        """sh 를 new 로 바꾸고 되돌리기 기록을 남긴다 (바뀐 도형은 맨 위로)"""
        i = next(k for k, x in enumerate(doc.shapes) if x is sh)
        del doc.shapes[i]
        new["_replaced"] = (i, sh)
        doc.shapes.append(new)
        doc.redo.clear()
        if st["sel"] is sh:
            st["sel"] = new
        return new

    def undo(*_):
        doc = cur()
        if doc and doc.shapes and not st["tedit"]:
            sh = doc.shapes.pop()
            rep = sh.get("_replaced")
            if rep:  # 고치기/지우기를 되돌림: 원래 것을 제자리에
                doc.shapes.insert(min(rep[0], len(doc.shapes)), rep[1])
                if st["sel"] is sh:
                    st["sel"] = rep[1]
            doc.redo.append(sh)
            refresh()

    def redo(*_):
        doc = cur()
        if doc and doc.redo and not st["tedit"]:
            sh = doc.redo.pop()
            rep = sh.get("_replaced")
            if rep:
                for i, x in enumerate(doc.shapes):
                    if x is rep[1]:
                        del doc.shapes[i]
                        break
                if st["sel"] is rep[1]:
                    st["sel"] = sh
            doc.shapes.append(sh)
            refresh()

    def clear_doc():
        doc = cur()
        if doc and visible(doc) and messagebox.askyesno(APP_NAME, "이 이미지의 가이드를 모두 지울까요?", parent=root):
            doc.redo = list(reversed(doc.shapes)) + doc.redo
            doc.shapes = []
            st["sel"] = None
            refresh()

    NAMES = dict(TOOLS)

    def select(sh):
        """선택하고, 위쪽 설정 칸을 그 가이드 값으로 맞춤 (값만 보여줌, 바꾸지는 않음)"""
        st["sel"] = sh
        if sh is None:
            return
        c = "#%02x%02x%02x" % tuple(sh["color"])
        st["color"] = c
        draw_color_swatch()
        alpha_var.set(max(5, int(round(sh.get("alpha", 255) / 2.55))))
        alpha_lbl.config(text="%d%%" % alpha_var.get())
        t = sh["type"]
        if t == "text":
            tsize_var.set(max(10, min(150, int(round(sh["size"] * st["scale"])))))
            tsize_lbl.config(text="%dpx" % tsize_var.get())
            stroke_var.set(bool(sh.get("stroke")))
        elif t == "grid":
            cols_var.set(sh.get("cols", 10))
            rows_var.set(sh.get("rows", 10))
            gw_var.set(max(1, int(round(sh.get("width", 2)))))
        else:
            width_var.set(max(1, min(60, int(round(sh["width"] * st["scale"])))))
            width_lbl.config(text="%dpx" % width_var.get())
        set_status("%s 선택  끌어서 이동 / 손잡이로 크기 / 휠로 확대·축소 / Delete 로 지우기 / Esc 로 선택 풀기"
                   % NAMES.get(t, "가이드"))

    def deselect(*_):
        if st["tedit"]:
            return
        if st["sel"] is not None:
            st["sel"] = None
            refresh()

    def delete_shape(doc, sh):
        replace_shape(doc, sh, dict(sh, _deleted=True))  # 되돌리기로 살릴 수 있게 기록만 남김
        if st["sel"] is not None and st["sel"].get("_deleted"):
            st["sel"] = None
        st["hover"] = None
        refresh()
        set_status("%s 지움 (되돌리기: %s+Z)" % (NAMES.get(sh["type"], "가이드"), MOD))

    def delete_selected(*_):
        doc = cur()
        if doc and not st["tedit"] and alive(st["sel"]):
            delete_shape(doc, st["sel"])

    # ---------- 선택한 가이드에 설정 적용 ----------
    last_counts = [max(1, min(64, int(cols_var.get()))), max(1, min(64, int(rows_var.get()))),
                   max(1, min(50, int(gw_var.get())))]

    def grid_values():
        """그리드 입력칸 값 (잘못 입력하면 마지막 올바른 값으로 되돌림)"""
        for n, (var, hi) in enumerate(((cols_var, 64), (rows_var, 64), (gw_var, 50))):
            try:
                v = int(str(var.get()).strip())
                if not 1 <= v <= hi:
                    raise ValueError
                last_counts[n] = v
            except Exception:
                var.set(last_counts[n])
        return tuple(last_counts)

    def apply_style(what):
        if what == "grid":
            grid_values()
        doc, sh = cur(), st["sel"]
        if not alive(sh) or st["tool"] == "eraser":
            return
        t = sh["type"]
        new = dict(sh)
        if what == "color":
            new["color"] = list(hex_to_rgb(st["color"]))
        elif what == "alpha":
            new["alpha"] = int(round(alpha_var.get() * 2.55))
        elif what == "width" and t not in ("text", "grid"):
            new["width"] = max(1.0, float(width_var.get()) / st["scale"])
        elif what == "grid" and t == "grid":
            new["cols"], new["rows"], new["width"] = last_counts[0], last_counts[1], float(last_counts[2])
        elif what == "tsize" and t == "text":
            new["size"] = max(2.0, float(tsize_var.get()) / st["scale"])
        elif what == "stroke" and t == "text":
            new["stroke"] = bool(stroke_var.get())
        else:
            return
        keys = ("color", "alpha", "width", "cols", "rows", "size", "stroke")
        if any(new.get(k) != sh.get(k) for k in keys):
            replace_shape(doc, sh, new)
            refresh()
            set_status("선택한 %s 바꿈" % NAMES.get(t, "가이드"))

    def change_width(d):
        width_var.set(max(1, min(60, int(width_var.get()) + d)))
        width_lbl.config(text="%dpx" % width_var.get())
        apply_style("width")

    # =====================================================================
    # 글자: 이미지 위에서 바로 입력
    # =====================================================================
    fams = set(tkfont.families(root))
    ui_family = next((f for f in ("Malgun Gothic", "맑은 고딕", "Apple SD Gothic Neo", "AppleGothic",
                                  "Noto Sans CJK KR", "NanumGothic") if f in fams), "TkDefaultFont")

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
        st["sel"] = None
        w.bind("<KeyRelease>", fit_editor)
        w.bind("<Escape>", lambda e: (finish_text_edit(), "break")[1])
        w.bind("<%s-Return>" % ("Command" if IS_MAC else "Control"), lambda e: (finish_text_edit(), "break")[1])
        refresh()
        w.focus_set()
        set_status("글자 입력 중…  다른 곳을 클릭하거나 Esc / %s+Enter 로 끝내기" % MOD)

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
                replace_shape(doc, old, dict(old, _deleted=True))
                set_status("글자 삭제")
            elif text != old["text"]:
                select(replace_shape(doc, old, dict(old, text=text, color=ted["style"]["color"])))
                set_status("글자 고침")
            elif ted["style"]["color"] != list(old["color"]):
                select(replace_shape(doc, old, dict(old, color=ted["style"]["color"])))
            else:
                select(old)
        elif text.strip():
            sh = dict(ted["style"], type="text", text=text, pts=[ted["p"]], width=1.0)
            doc.shapes.append(sh)
            doc.redo.clear()
            set_status("글자 추가 (가이드 %d개)  글자를 클릭하면 선택 -> 끌어서 이동 / 손잡이·휠로 크기 / 두 번 클릭해서 고치기"
                       % len(visible(doc)))
        refresh()
        try:
            canvas_r.focus_set()
        except Exception:
            pass

    # =====================================================================
    # 마우스
    # =====================================================================
    def top_hit(doc, ev, types=None):
        """마우스 아래 맨 위 가이드"""
        p = to_img(ev, clamp=False)
        tol = 6.0 / st["scale"]
        for sh in reversed(visible(doc)):
            if (types is None or sh["type"] in types) and shape_hit(sh, p, tol):
                return sh
        return None

    def handle_hit(sh, ev):
        for kind, k, pt in shape_handles(sh):
            cx, cy = to_canvas(pt)
            if abs(cx - ev.x) <= 9 and abs(cy - ev.y) <= 9:
                return kind, k
        return None

    def start_op(doc, sh, kind, k, ev):
        """선택한 가이드 끌기 시작 (복사본에서 고치다가 놓을 때 되돌리기 기록과 함께 바꿈)"""
        select(sh)
        work = dict(sh)
        i = next(n for n, x in enumerate(doc.shapes) if x is sh)
        doc.shapes[i] = work
        st["sel"] = work
        st["op"] = {"kind": kind, "k": k, "orig": sh, "work": work, "start": to_img(ev, clamp=False)}
        refresh()

    def is_ctrl(ev):
        return bool(ev.state & 0x0004) or (IS_MAC and bool(ev.state & 0x0008))

    def new_drawing(kind, p):
        w = tool_width(kind) if kind != st["tool"] else int(width_var.get())
        st["drawing"] = {"type": kind, "pts": [p], "color": list(hex_to_rgb(st["color"])),
                         "alpha": int(round(alpha_var.get() * 2.55)),
                         "width": max(1.0, float(w) / st["scale"]), "_w": w}
        st["temp_items"] = []

    def on_press(ev):
        canvas_r.focus_set()  # 입력칸에 있던 키보드 입력을 화면으로 (단축키가 다시 동작하게)
        doc = cur()
        st["press"] = (ev.x, ev.y)
        st["drawing"] = None
        if not doc:
            return
        if st["tedit"]:  # 글자 입력 중에 다른 곳을 누르면 입력 끝
            finish_text_edit()
            return
        tool = st["tool"]
        p = to_img(ev)
        if tool == "eraser":
            sh = top_hit(doc, ev)
            if sh is not None:
                delete_shape(doc, sh)
            return
        if is_ctrl(ev):  # Ctrl(맥은 Cmd) + 드래그 = 화살표
            new_drawing("arrow", p)
            return
        sel = st["sel"] if alive(st["sel"]) else None
        if sel is not None:
            h = handle_hit(sel, ev)
            if h:
                kind = h[0]
                start_op(doc, sel, kind, h[1], ev)
                st["op"]["shift"] = bool(ev.state & 0x0001)
                return
        if tool == "grid":  # 선택 안 한 그리드도 코너를 바로 잡을 수 있게
            for sh in reversed(visible(doc)):
                if sh["type"] == "grid":
                    h = handle_hit(sh, ev)
                    if h and h[0] == "corner":
                        start_op(doc, sh, "corner", h[1], ev)
                        st["op"]["shift"] = bool(ev.state & 0x0001)
                        return
        if tool == "select":
            sh = top_hit(doc, ev)
            if sh is not None:
                start_op(doc, sh, "move", 0, ev)
            else:
                st["sel"] = None
                refresh()
            return
        if tool == "text":
            sh = top_hit(doc, ev, ("text",))
            if sh is not None:
                start_op(doc, sh, "move", 0, ev)
            else:
                st["sel"] = None
                start_text_edit(doc, p)
            return
        if tool == "grid":
            sh = top_hit(doc, ev, ("grid",))
            if sh is not None:
                start_op(doc, sh, "move", 0, ev)
                return
        if sel is not None and tool != "pen" and shape_hit(sel, to_img(ev, clamp=False), 6.0 / st["scale"]):
            start_op(doc, sel, "move", 0, ev)  # 선택한 가이드 위를 누르면 이동
            return
        # 새로 그리기 (짧게 클릭만 하면 놓을 때 그 자리의 가이드를 선택)
        new_drawing(tool, p)

    def on_drag(ev):
        op = st["op"]
        if op:
            p = to_img(ev, clamp=False)  # 손잡이는 이미지 밖으로도 조금 나갈 수 있게
            new = edit_shape(op["orig"], op["kind"], op["k"], p, op["start"],
                             shift=bool(ev.state & 0x0001) or op.get("shift", False))
            op["work"].clear()
            op["work"].update(new)
            refresh_soon()
            return
        sh = st["drawing"]
        if not sh:
            return
        p = to_img(ev)
        w = max(1, int(round(sh["_w"])))
        col = st["color"]
        if sh["type"] == "pen":
            a = to_canvas(sh["pts"][-1])
            b = to_canvas(p)
            if abs(a[0] - b[0]) + abs(a[1] - b[1]) < 1.5:
                return
            sh["pts"].append(p)
            st["temp_items"].append(canvas_r.create_line(a[0], a[1], b[0], b[1], fill=col, width=w,
                                                         capstyle="round", joinstyle="round"))
            return
        sh["pts"] = [sh["pts"][0], p]
        for it in st["temp_items"]:
            canvas_r.delete(it)
        a, b = to_canvas(sh["pts"][0]), to_canvas(p)
        t = sh["type"]
        if t == "rect":
            items = [canvas_r.create_rectangle(a[0], a[1], b[0], b[1], outline=col, width=w)]
        elif t == "fillrect":
            items = [canvas_r.create_rectangle(a[0], a[1], b[0], b[1], outline=col, fill=col,
                                               stipple="gray50" if sh["alpha"] < 200 else "")]
        elif t == "line":
            items = [canvas_r.create_line(a[0], a[1], b[0], b[1], fill=col, width=w, capstyle="round")]
        elif t == "grid":
            c, r, gw = grid_values()
            w = max(1, int(round(gw * st["scale"])))
            items = [canvas_r.create_rectangle(a[0], a[1], b[0], b[1], outline=col, width=w)]
            for i in range(1, c):
                x = a[0] + (b[0] - a[0]) * i / c
                items.append(canvas_r.create_line(x, a[1], x, b[1], fill=col, width=w))
            for j in range(1, r):
                y = a[1] + (b[1] - a[1]) * j / r
                items.append(canvas_r.create_line(a[0], y, b[0], y, fill=col, width=w))
        else:
            items = [canvas_r.create_line(a[0], a[1], b[0], b[1], fill=col, width=w,
                                          arrow="last", arrowshape=(w * 4 + 8, w * 4 + 8, w * 1.6 + 4))]
        st["temp_items"] = items

    def on_release(ev):
        doc = cur()
        op = st["op"]
        if op:
            st["op"] = None
            work, orig = op["work"], op["orig"]
            i = next((n for n, x in enumerate(doc.shapes) if x is work), None) if doc else None
            if i is None:
                return
            doc.shapes[i] = orig
            st["sel"] = orig
            same = [tuple(x) for x in work["pts"]] == [tuple(x) for x in orig["pts"]] and \
                work.get("size") == orig.get("size")
            if not same:
                work.pop("_replaced", None)
                replace_shape(doc, orig, work)
                if work["type"] == "text":
                    tsize_var.set(max(10, min(150, int(round(work["size"] * st["scale"])))))
                    tsize_lbl.config(text="%dpx" % tsize_var.get())
                set_status("%s %s" % (NAMES.get(work["type"], "가이드"),
                                      "이동" if op["kind"] == "move" else "모양 / 크기 바꿈"))
            refresh()
            return
        sh = st["drawing"]
        st["drawing"] = None
        if not sh or not doc:
            return
        px, py = st["press"] or (ev.x, ev.y)
        clicked = abs(ev.x - px) < 4 and abs(ev.y - py) < 4
        if clicked:  # 짧게 클릭: 그 자리의 가이드를 선택
            hit = top_hit(doc, ev)
            if hit is not None:
                for it in st["temp_items"]:
                    canvas_r.delete(it)
                select(hit)
                refresh()
                return
            st["sel"] = None
            if sh["type"] != "pen":
                refresh()
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
            c, r, gw = grid_values()
            sh["cols"], sh["rows"], sh["width"] = c, r, float(gw)  # 그리드 선 굵기는 실제 이미지 픽셀
        sh.pop("_w", None)
        doc.shapes.append(sh)
        doc.redo.clear()
        st["sel"] = None
        refresh()
        set_status("%s 추가 (가이드 %d개)  클릭하면 선택해서 옮기기 / 크기 / 색 바꾸기 / 지우기"
                   % (NAMES.get(sh["type"], "가이드"), len(visible(doc))))

    def on_double(ev):
        doc = cur()
        if not doc or st["tool"] in ("eraser",):
            return
        sh = top_hit(doc, ev, ("text",))
        if sh is not None:
            st["op"] = None
            start_text_edit(doc, sh["pts"][0], old=sh)

    def on_wheel(ev):
        """가이드 위에서 휠: 가운데 기준 확대 / 축소"""
        doc = cur()
        if not doc or st["op"] or st["tedit"] or st["tool"] == "eraser":
            return
        up = (ev.num == 4) if getattr(ev, "num", None) in (4, 5) else ev.delta > 0
        f = 1.06 if up else 1 / 1.06
        sel = st["sel"] if alive(st["sel"]) else None
        inside = False
        if sel is not None:  # 선택한 가이드의 영역 안이면 (박스 안쪽 등) 그 가이드
            x0, y0, x1, y1 = canvas_bounds(sel, 8)
            inside = x0 <= ev.x <= x1 and y0 <= ev.y <= y1
        target = sel if inside else top_hit(doc, ev)
        if target is None:
            return
        import time
        w = st["wheel"]
        now = time.time()
        if not (w and w["obj"] is target and now - w["t"] < 1.0 and doc.shapes and doc.shapes[-1] is target):
            target = replace_shape(doc, target, dict(target))  # 이어서 굴리면 같은 기록 안에서 계속 바꿈
        new = scale_shape(target, f)
        target.update(new)
        st["sel"] = target
        st["wheel"] = {"obj": target, "t": now}
        refresh_soon()
        set_status("%s 크기 조절 (휠)" % NAMES.get(target["type"], "가이드"))
        return "break"

    def on_motion(ev):
        doc = cur()
        tool = st["tool"]
        cursor = "crosshair"
        if doc and not st["op"]:
            if tool == "eraser":
                hv = top_hit(doc, ev)
                cursor = "X_cursor" if hv is not None else "arrow"
                if hv is not st["hover"]:
                    st["hover"] = hv
                    refresh_soon()
            else:
                sel = st["sel"] if alive(st["sel"]) else None
                if sel is not None and handle_hit(sel, ev):
                    cursor = "hand2"
                elif tool == "select":
                    cursor = "fleur" if top_hit(doc, ev) is not None else "arrow"
                elif tool == "text":
                    cursor = "fleur" if top_hit(doc, ev, ("text",)) is not None else "xterm"
                elif tool == "grid":
                    cursor = "fleur" if top_hit(doc, ev, ("grid",)) is not None else "crosshair"
                elif sel is not None and tool != "pen" and shape_hit(sel, to_img(ev, clamp=False), 6.0 / st["scale"]):
                    cursor = "fleur"
        if str(canvas_r.cget("cursor")) != cursor:
            try:
                canvas_r.config(cursor=cursor)
            except Exception:
                canvas_r.config(cursor="crosshair")

    # =====================================================================
    # 내보내기
    # =====================================================================
    def open_out_folder():
        d = out_var.get().strip().strip('"')
        if d and os.path.isdir(d):
            try:
                if IS_WIN:
                    os.startfile(d)
                elif IS_MAC:
                    subprocess.Popen(["open", d])
                else:
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
        if st["tool"] not in ("select", "eraser"):
            tool_widths[st["tool"]] = int(width_var.get())
        gv = grid_values()
        settings.update(tool=st["tool"], color=st["color"], opacity=int(alpha_var.get()),
                        tool_widths=tool_widths, text_size=int(tsize_var.get()),
                        text_stroke_v2=bool(stroke_var.get()), out_dir=out_var.get().strip(),
                        export_mode=mode_var.get(), export_layout=layout_var.get(),
                        export_divider=bool(divider_var.get()), export_labels=bool(labels_var.get()),
                        grid_cols=gv[0], grid_rows=gv[1], grid_width=gv[2], geometry=root.geometry())
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

    # =====================================================================
    # 업데이트
    # =====================================================================
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

    # =====================================================================
    # 파일 끌어다 놓기
    # =====================================================================
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

    # =====================================================================
    # 키보드
    # =====================================================================
    def typing(e):
        return isinstance(e.widget, (tk.Entry, ttk.Entry, tk.Text))

    def key(fn):  # 글자를 입력하는 칸에서는 단축키(숫자, 화살표 등)가 동작하지 않게
        def h(e):
            if typing(e):
                return None
            return fn(e)
        return h

    def key_letter(e):
        return shortcut_letter(e.keysym, e.char, e.keycode)

    def on_mod_key(e):
        letter = key_letter(e)
        shift = bool(e.state & 0x0001)
        if letter == "o":
            open_files()
        elif letter == "e":
            export(False)
        elif typing(e):
            return None  # 입력칸에서는 되돌리기 등을 그 칸이 처리
        elif letter == "z":
            redo() if shift else undo()
        elif letter == "y":
            redo()
        else:
            return None
        return "break"

    def on_plain_key(e):
        """수정키 없이 누른 한 글자 (한글 입력 상태 포함)"""
        if typing(e) or (e.state & 0x000C):
            return None
        ch = e.char or ""
        if ch.isdigit() and ch != "0":
            n = int(ch) - 1
            if n < len(TOOLS):
                set_tool(TOOLS[n][0])
                return "break"
        if key_letter(e) == "v" and not (e.state & 0x0001):
            set_tool("select")
            return "break"
        return None

    canvas_r.bind("<ButtonPress-1>", on_press)
    canvas_r.bind("<B1-Motion>", on_drag)
    canvas_r.bind("<ButtonRelease-1>", on_release)
    canvas_r.bind("<Double-Button-1>", on_double)
    canvas_r.bind("<Motion>", on_motion)
    canvas_r.bind("<MouseWheel>", on_wheel)       # 윈도우 / 맥
    canvas_r.bind("<Button-4>", on_wheel)         # 리눅스
    canvas_r.bind("<Button-5>", on_wheel)
    if IS_MAC:  # 맥은 Control + 클릭이 오른쪽 클릭으로 바뀌는 경우가 있어서 그쪽도 받아줌
        canvas_r.bind("<Control-ButtonPress-1>", on_press)
        canvas_r.bind("<Control-B1-Motion>", on_drag)
        canvas_r.bind("<Control-ButtonRelease-1>", on_release)
    root.bind("<Delete>", key(delete_selected))
    root.bind("<BackSpace>", key(delete_selected))
    root.bind("<Escape>", key(deselect))
    for c in (canvas_l, canvas_r):
        c.bind("<Configure>", lambda e: root.after_idle(refresh))
    root.bind("<Control-KeyPress>", on_mod_key)
    if IS_MAC:
        root.bind("<Command-KeyPress>", on_mod_key)
    root.bind("<KeyPress>", on_plain_key, add="+")
    root.bind("<Left>", key(lambda e: go(-1)))
    root.bind("<Right>", key(lambda e: go(1)))
    root.bind("<bracketleft>", key(lambda e: change_width(-1)))
    root.bind("<bracketright>", key(lambda e: change_width(1)))
    root.protocol("WM_DELETE_WINDOW", on_close)

    draw_color_swatch()
    set_tool(st["tool"])
    update_export_opts()
    # 명령줄/아이콘에 끌어다 놓기로 실행한 경우 그 이미지들 열기
    args = [a for a in sys.argv[1:] if os.path.isfile(a) or os.path.isdir(a)]
    if args:
        root.after(200, lambda: drop_paths(args))
    root.after(200, poll)
    if getattr(sys, "frozen", False) or os.environ.get("GM_CHECK_UPDATE"):
        root.after(1500, lambda: check_updates(False))
    root.after(100, lambda: (top.layout(), opt.layout(), refresh()))
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
