# -*- coding: utf-8 -*-
"""
Guide Maker
-----------
이미지를 불러와서 왼쪽에는 원본, 오른쪽에는 같은 이미지 위에 가이드(펜/박스/직선/화살표)를 그리고
원본 해상도로 "<원본이름>_guide.<확장자>" 로 내보낸다.

단축키
  Ctrl+O 불러오기   Ctrl+Z 실행 취소   Ctrl+Y / Ctrl+Shift+Z 다시 실행
  ← / → 이전/다음 이미지   1~5 도구 선택   [ / ] 굵기   Ctrl+E 현재 이미지 내보내기
"""
import os
import sys
import json
import math
import platform
import traceback

from PIL import Image, ImageDraw, ImageOps

APP_NAME = "Guide Maker"
APP_VERSION = "1.0.0"
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


def composite(base_rgba, shapes, scale):
    """base_rgba(이미 scale 에 맞춘 크기) 위에 가이드를 그려서 돌려준다.
    도형마다 자기 투명도로 겹치고, 같은 도형 안에서 겹친 부분은 진해지지 않는다."""
    out = base_rgba.copy()
    W, H = out.size
    for sh in shapes:
        x0, y0, x1, y1 = shape_bbox(sh, scale)
        x0, y0 = max(0, x0), max(0, y0)
        x1, y1 = min(W, x1), min(H, y1)
        if x1 <= x0 or y1 <= y0:
            continue
        layer = Image.new("RGBA", (x1 - x0, y1 - y0), (0, 0, 0, 0))
        draw_shape(ImageDraw.Draw(layer), sh, scale, (x0, y0))
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
    root = tk.Tk()
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
    ttk.Scale(bar2, from_=5, to=100, variable=alpha_var, length=150,
              command=lambda v: alpha_lbl.config(text="%d%%" % float(v))).pack(side="left", padx=4)
    alpha_lbl.pack(side="left")
    ttk.Label(bar2, text="  굵기").pack(side="left")
    width_var = tk.IntVar(value=int(settings.get("width", 6)))
    width_lbl = ttk.Label(bar2, width=5)
    ttk.Scale(bar2, from_=1, to=60, variable=width_var, length=150,
              command=lambda v: width_lbl.config(text="%dpx" % float(v))).pack(side="left", padx=4)
    width_lbl.pack(side="left")
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
    status = ttk.Label(root, text="이미지를 불러오세요.  (Ctrl+O)", anchor="w", padding=(10, 0, 10, 6))
    status.pack(fill="x")

    # ---------- 동작 ----------
    def cur():
        return st["docs"][st["idx"]] if 0 <= st["idx"] < len(st["docs"]) else None

    def set_status(t):
        status.config(text=t)

    def set_tool(t):
        st["tool"] = t
        tool_var.set(t)

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
                              text="'이미지 불러오기'로 이미지를 여세요\n(여러 장 선택 가능)", justify="center")
            return
        name = doc.name if len(doc.name) <= 30 else doc.name[:27] + "…"
        mark = " ●" if doc.shapes else ""
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
        canvas_r.create_text(8, 8, anchor="nw", fill="#8a9099",
                             text="%d×%d  가이드 %d개" % (doc.size[0], doc.size[1], len(doc.shapes)))

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

    def on_press(ev):
        doc = cur()
        if not doc:
            return
        p = to_img(ev)
        st["drawing"] = {"type": st["tool"], "pts": [p], "color": list(hex_to_rgb(st["color"])),
                         "alpha": int(round(alpha_var.get() * 2.55)),
                         "width": max(1.0, float(width_var.get()) / st["scale"])}
        st["temp_items"] = []

    def on_drag(ev):
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
            else:
                items = [canvas_r.create_line(a[0], a[1], b[0], b[1], fill=col, width=w,
                                              arrow="last", arrowshape=(w * 4 + 8, w * 4 + 8, w * 1.6 + 4))]
            st["temp_items"] = items

    def on_release(ev):
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
        doc.shapes.append(sh)
        doc.redo.clear()
        refresh()
        set_status("%s 추가 (가이드 %d개)" % (dict(TOOLS)[sh["type"]], len(doc.shapes)))

    def undo(*_):
        doc = cur()
        if doc and doc.shapes:
            doc.redo.append(doc.shapes.pop())
            refresh()

    def redo(*_):
        doc = cur()
        if doc and doc.redo:
            doc.shapes.append(doc.redo.pop())
            refresh()

    def clear_doc():
        doc = cur()
        if doc and doc.shapes and messagebox.askyesno(APP_NAME, "이 이미지의 가이드를 모두 지울까요?", parent=root):
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
            drawn = [d for d in docs if d.shapes]
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

    def on_close():
        settings.update(tool=st["tool"], color=st["color"], opacity=int(alpha_var.get()),
                        width=int(width_var.get()), out_dir=out_var.get().strip(), geometry=root.geometry())
        save_settings(settings)
        unsaved = [d for d in st["docs"] if d.shapes]
        if unsaved and not messagebox.askyesno(
                APP_NAME, "가이드를 그린 이미지가 %d장 있습니다.\n내보내지 않은 가이드는 사라집니다. 종료할까요?" % len(unsaved),
                parent=root):
            return
        root.destroy()

    def change_width(d):
        width_var.set(max(1, min(60, int(width_var.get()) + d)))
        width_lbl.config(text="%dpx" % width_var.get())

    canvas_r.bind("<ButtonPress-1>", on_press)
    canvas_r.bind("<B1-Motion>", on_drag)
    canvas_r.bind("<ButtonRelease-1>", on_release)
    for c in (canvas_l, canvas_r):
        c.bind("<Configure>", lambda e: root.after_idle(refresh))
    mod = "Command" if IS_MAC else "Control"
    root.bind("<%s-o>" % mod, lambda e: open_files())
    root.bind("<%s-z>" % mod, undo)
    root.bind("<%s-y>" % mod, redo)
    root.bind("<%s-Z>" % mod, redo)
    root.bind("<%s-e>" % mod, lambda e: export(False))
    root.bind("<Left>", lambda e: go(-1))
    root.bind("<Right>", lambda e: go(1))
    root.bind("<bracketleft>", lambda e: change_width(-1))
    root.bind("<bracketright>", lambda e: change_width(1))
    for i, (key, _) in enumerate(TOOLS):
        root.bind(str(i + 1), lambda e, k=key: set_tool(k))
    root.protocol("WM_DELETE_WINDOW", on_close)

    set_color(st["color"])
    set_tool(st["tool"])
    alpha_lbl.config(text="%d%%" % alpha_var.get())
    width_lbl.config(text="%dpx" % width_var.get())
    # 명령줄/파일 끌어다 놓기로 실행한 경우 그 이미지들 열기
    args = [a for a in sys.argv[1:] if os.path.isfile(a) or os.path.isdir(a)]
    if args:
        paths = []
        for a in args:
            paths += list_images(a) if os.path.isdir(a) else [a]
        root.after(200, lambda: add_docs(paths))
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
