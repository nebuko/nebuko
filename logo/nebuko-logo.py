#!/usr/bin/env python3
"""NEBUKO — spirale en O, v3
 - tous les nœuds (ambre et bleus) = même forme : un anneau avec UN seul trou vide
 - la flèche est fondue dans le ruban : un seul tracé, pas de pointe posée dessus
Dépendances : pip install cairosvg uharfbuzz fonttools
"""
import math, os
import cairosvg
import uharfbuzz as hb
from fontTools.ttLib import TTFont
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
import urllib.request

OUT      = "nebuko-spiral-v3/"
FONT     = "Montserrat-ExtraBold.ttf"
FONT_URL = ("https://raw.githubusercontent.com/JulietaUla/Montserrat/"
            "master/fonts/ttf/Montserrat-ExtraBold.ttf")   # dépôt officiel, licence OFL
os.makedirs(OUT, exist_ok=True)

if not os.path.exists(FONT):
    print("Téléchargement de Montserrat ExtraBold...")
    urllib.request.urlretrieve(FONT_URL, FONT)

BG, AMBER, G0, G1 = "#0F172A", "#F59E0B", "#5A3DF0", "#2F9BFF"

def f(v):
    return f"{v:.2f}".rstrip("0").rstrip(".")

# ------------------------------------------------------------------ briques SVG
def ring(c, r, fill, k=0.42):
    """Anneau avec un seul trou vide (rayon du trou = k * r). Identique pour l'ambre et le bleu."""
    x, y = c
    ri = r * k
    return (f'<path fill="{fill}" fill-rule="evenodd" d="M{f(x-r)} {f(y)}a{f(r)} {f(r)} 0 1 0 {f(2*r)} 0a{f(r)} {f(r)} 0 1 0 {f(-2*r)} 0Z'
            f'M{f(x-ri)} {f(y)}a{f(ri)} {f(ri)} 0 1 0 {f(2*ri)} 0a{f(ri)} {f(ri)} 0 1 0 {f(-2*ri)} 0Z"/>')

def bez_cmds(pts):
    """Catmull-Rom -> Béziers cubiques"""
    d, n = "", len(pts)
    for i in range(n - 1):
        p0 = pts[i - 1] if i > 0 else pts[i]
        p1, p2 = pts[i], pts[i + 1]
        p3 = pts[i + 2] if i + 2 < n else pts[i + 1]
        c1 = (p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6)
        c2 = (p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6)
        d += f"C{f(c1[0])} {f(c1[1])} {f(c2[0])} {f(c2[1])} {f(p2[0])} {f(p2[1])}"
    return d

def ribbon(seg, hwf, head=None):
    """Ruban à largeur variable. Si head=(largeur, longueur, recul) est fourni, la pointe de
    flèche est dessinée DANS LE MÊME CONTOUR que le ruban : un seul tracé, aucune couture."""
    if len(seg) < 3:
        return ""
    k = max(1, len(seg) // 12)
    idx = list(range(0, len(seg), k))
    if idx[-1] != len(seg) - 1:
        idx.append(len(seg) - 1)
    Lp, Rp, last = [], [], None
    for i in idx:
        x, y, t = seg[i]
        j0, j1 = max(i - 2, 0), min(i + 2, len(seg) - 1)
        tx, ty = seg[j1][0] - seg[j0][0], seg[j1][1] - seg[j0][1]
        n = math.hypot(tx, ty) or 1.0
        ux, uy = tx / n, ty / n
        nx, ny = -uy, ux
        h = hwf(t)
        Lp.append((x + nx * h, y + ny * h))
        Rp.append((x - nx * h, y - ny * h))
        last = (x, y, ux, uy, nx, ny)
    Rr = Rp[::-1]
    d = f"M{f(Lp[0][0])} {f(Lp[0][1])}" + bez_cmds(Lp)
    if head:
        wh, lh, back = head
        x, y, ux, uy, nx, ny = last
        c1 = (x - ux * back + nx * wh / 2, y - uy * back + ny * wh / 2)   # barbe gauche
        tip = (x + ux * lh, y + uy * lh)                                  # pointe
        c2 = (x - ux * back - nx * wh / 2, y - uy * back - ny * wh / 2)   # barbe droite
        d += "".join(f"L{f(px)} {f(py)}" for px, py in (c1, tip, c2))
    d += f"L{f(Rr[0][0])} {f(Rr[0][1])}" + bez_cmds(Rr) + "Z"
    return d

# ------------------------------------------------------------------ géométrie de la spirale
def geometry(compact=False):
    turns = 1.5 if compact else 1.75
    R, r0 = 27.0, 5.4
    th1 = -math.pi / 2                    # le fil finit en haut, cap à droite
    th0 = th1 - turns * 2 * math.pi
    n = 500
    pts = []
    for i in range(n + 1):
        t = i / n
        th = th0 + (th1 - th0) * t
        r = r0 + (R - r0) * t
        pts.append((r * math.cos(th), r * math.sin(th), t))
    return pts

def build(uid, mode="dark"):
    flat = mode[5:] if mode.startswith("flat:") else None
    compact = mode == "compact"
    P = geometry(compact)

    w0, w1 = (4.2, 9.2) if compact else (3.0, 7.8)             # largeur du ruban : fine -> franche
    hwf = lambda t: (w0 + (w1 - w0) * min(t, 1.0) ** 1.25) / 2
    R_H = 7.4 if compact else 6.3                              # rayon des anneaux ambre
    tg = 0.62 if compact else 0.64                             # position de la 2e validation humaine
    pearls = [] if compact else [0.17, 0.35, 0.83]             # agents (anneaux bleus)

    at = lambda t: min(P, key=lambda q: abs(q[2] - t))
    gate = at(tg)
    cuts = [(gate[0], gate[1], R_H - 1.4)]                     # le fil est coupé sous chaque anneau
    blue_rings = []
    for t in pearls:
        p = at(t)
        outer = hwf(t) + 2.0
        blue_rings.append((p[0], p[1], outer))
        cuts.append((p[0], p[1], outer - 1.5))

    if flat is None:
        defs = (f'<radialGradient id="{uid}s" gradientUnits="userSpaceOnUse" cx="0" cy="0" r="34">'
                f'<stop offset=".15" stop-color="{G0}"/><stop offset=".9" stop-color="{G1}"/></radialGradient>'
                f'<radialGradient id="{uid}h"><stop offset="0" stop-color="{AMBER}" stop-opacity=".34"/>'
                f'<stop offset=".5" stop-color="{AMBER}" stop-opacity=".18"/>'
                f'<stop offset="1" stop-color="{AMBER}" stop-opacity="0"/></radialGradient>')
        blue, amb = f"url(#{uid}s)", AMBER
    else:
        defs, blue, amb = "", flat, flat

    o = ""
    if mode == "dark":
        o += (f'<circle cx="0" cy="0" r="18" fill="url(#{uid}h)"/>'
              f'<circle cx="{f(gate[0])}" cy="{f(gate[1])}" r="15" fill="url(#{uid}h)"/>')

    # prolongement du fil : il se redresse puis part vers la droite, en montant
    K, pk = P[-1], P[-6]
    alpha = math.atan2(K[1] - pk[1], K[0] - pk[0])
    phi = math.radians(-28)
    S, L2, step = 15.0, 3.0, 0.5
    x, y, s_ = K[0], K[1], 0.0
    ext = []
    while s_ < S + L2:
        u = min(s_ / S, 1.0)
        h = alpha + (phi - alpha) * (3 * u * u - 2 * u ** 3)
        x += math.cos(h) * step
        y += math.sin(h) * step
        s_ += step
        ext.append((x, y, 1.0))
    full = P + ext

    # morceaux de fil entre les anneaux
    keep = [all(math.hypot(p[0] - cx_, p[1] - cy_) > tr for cx_, cy_, tr in cuts) for p in full]
    runs, cur = [], []
    for p, k_ in zip(full, keep):
        if k_:
            cur.append(p)
        elif cur:
            runs.append(cur)
            cur = []
    if cur:
        runs.append(cur)

    wh, lh, back = w1 * 2.2, 15.0, 2.2                         # taille de la pointe
    for i, r in enumerate(runs):
        head = (wh, lh, back) if i == len(runs) - 1 else None  # le dernier morceau porte la flèche
        o += f'<path d="{ribbon(r, hwf, head)}" fill="{blue}"/>'

    # anneaux : MÊME forme pour les agents (bleu) et les humains (ambre)
    for px, py, outer in blue_rings:
        o += ring((px, py), outer, blue)
    o += ring((0, 0), R_H, amb)                                # 1er prompt humain
    o += ring((gate[0], gate[1]), R_H, amb)                    # validation humaine

    # boîte englobante (pour centrer / mettre à l'échelle)
    Q = ext[-1]
    ux, uy = math.cos(phi), math.sin(phi)
    nx, ny = -uy, ux
    hp = [(Q[0] - ux * back + nx * wh / 2, Q[1] - uy * back + ny * wh / 2),
          (Q[0] + ux * lh, Q[1] + uy * lh),
          (Q[0] - ux * back - nx * wh / 2, Q[1] - uy * back - ny * wh / 2)]
    xs = [p[0] for p in full] + [p[0] for p in hp]
    ys = [p[1] for p in full] + [p[1] for p in hp]
    m = w1 / 2 + 1.5
    return f"<defs>{defs}</defs>" + o, (min(xs) - m, min(ys) - m, max(xs) + m, max(ys) + m)

# ------------------------------------------------------------------ mise en page
def fit(inner, bbox, target=56):
    x0, y0, x1, y1 = bbox
    s = target / max(x1 - x0, y1 - y0)
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    g = f'<g transform="translate(32 32) scale({f(s)}) translate({f(-cx)} {f(-cy)})">{inner}</g>'
    hw, hh = (x1 - x0) / 2 * s, (y1 - y0) / 2 * s
    return g, (32 - hw, 32 - hh, 32 + hw, 32 + hh)

def svg(inner, vb, sz=8):
    x, y, w, h = vb
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{f(x)} {f(y)} {f(w)} {f(h)}" '
            f'width="{f(w*sz)}" height="{f(h*sz)}">{inner}</svg>')

def mark_group(uid, mode, target=56):
    inner, bb = build(uid, mode)
    return fit(inner, bb, target)

def tile(uid, mode="dark", target=48, rx=14):
    g, _ = mark_group(uid + "m", mode, target)
    bg = (f'<defs><radialGradient id="{uid}b" cx=".5" cy=".5" r=".75"><stop offset="0" stop-color="#1B2650"/>'
          f'<stop offset="1" stop-color="{BG}"/></radialGradient></defs>'
          f'<rect width="64" height="64" rx="{rx}" fill="url(#{uid}b)"/>'
          f'<rect x=".5" y=".5" width="63" height="63" rx="{rx-.5}" fill="none" stroke="#fff" stroke-opacity=".08"/>')
    return svg(bg + g, (0, 0, 64, 64))

# ------------------------------------------------------------------ mot NEBUKO en tracés
def text_outline(fontfile, text, size, tracking):
    tt = TTFont(fontfile)
    gs, order, upem = tt.getGlyphSet(), tt.getGlyphOrder(), tt["head"].unitsPerEm
    font = hb.Font(hb.Face(hb.Blob(open(fontfile, "rb").read())))
    buf = hb.Buffer()
    buf.add_str(text)
    buf.guess_segment_properties()
    hb.shape(font, buf, {"kern": True, "liga": False})
    sc, x, d, il, ir = size / upem, 0.0, [], None, 0.0
    for info, pos in zip(buf.glyph_infos, buf.glyph_positions):
        g = order[info.codepoint]
        pen = SVGPathPen(gs, ntos=f)
        gs[g].draw(TransformPen(pen, (sc, 0, 0, -sc, x + pos.x_offset * sc, 0)))
        cmd = pen.getCommands()
        if cmd:
            d.append(cmd)
            gl = tt["glyf"][g]
            il = x + gl.xMin * sc if il is None else il
            ir = x + gl.xMax * sc
        x += pos.x_advance * sc + tracking * size
    return " ".join(d), il, ir, tt["OS/2"].sCapHeight * sc

def horizontal(uid, variant):
    mode = {"dark": "dark", "light": "light", "white": "flat:#FFFFFF", "black": "flat:#000000"}[variant]
    word = {"dark": "#FFFFFF", "light": BG, "white": "#FFFFFF", "black": "#000000"}[variant]
    g, ink = mark_group(uid, mode, 56)
    d, l, r, cap = text_outline(FONT, "NEBUKO", 22.0 / 0.70, 0.03)
    x0, base = ink[2] + 12 - l, 32 + cap / 2
    txt = f'<path transform="translate({f(x0)} {f(base)})" d="{d}" fill="{word}"/>'
    w = ink[2] + 12 + (r - l) + 8 + 8
    return svg(g + txt, (-8, -8, w, 80), 5)

# ------------------------------------------------------------------ fichiers
files = {
    "mark-dark":  svg(mark_group("a", "dark", 56)[0], (-8, -8, 80, 80)),
    "mark-light": svg(mark_group("b", "light", 60)[0], (0, 0, 64, 64)),
    "mark-white": svg(mark_group("c", "flat:#FFFFFF", 60)[0], (0, 0, 64, 64)),
    "mark-black": svg(mark_group("d", "flat:#000000", 60)[0], (0, 0, 64, 64)),
    "tile":            tile("t", "dark", 48),
    "favicon-compact": tile("fc", "compact", 54),
    "logo-horizontal-dark":  horizontal("e", "dark"),
    "logo-horizontal-light": horizontal("g", "light"),
    "logo-horizontal-white": horizontal("h", "white"),
    "logo-horizontal-black": horizontal("i", "black"),
}
for name, s in files.items():
    open(OUT + name + ".svg", "w").write(s)

for name, w in (("mark-dark", 2048), ("mark-light", 2048), ("tile", 1024),
                ("logo-horizontal-dark", 4096), ("logo-horizontal-light", 4096)):
    cairosvg.svg2png(bytestring=files[name].encode(), write_to=f"{OUT}{name}.png", output_width=w)

for sz in (64, 32, 16):
    src = files["favicon-compact" if sz <= 32 else "tile"]
    cairosvg.svg2png(bytestring=src.encode(), write_to=f"{OUT}favicon-{sz}.png", output_width=sz)

print("ok ->", OUT)