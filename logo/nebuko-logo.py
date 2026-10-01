#!/usr/bin/env python3
"""NEBUKO — spirale en O, v3
 - le fil est continu : plus aucun nœud bleu, seul le ruban dessine la spirale
 - deux marqueurs ambre portent le sens : cible (début de l'issue) et losange
   élancé (gate de validation humaine) — le fil s'affine jusqu'à la pointe, sans
   point terminal
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
DIAMOND_WX, DIAMOND_HY = 0.90, 1.30        # losange : facteurs demi-largeur / demi-hauteur (>1 = élancé)
TAIL_TAPER = 0.50                          # fraction finale de la queue qui s'affine en pointe

def f(v):
    return f"{v:.2f}".rstrip("0").rstrip(".")

# ------------------------------------------------------------------ briques SVG
def annulus(c, r, ri, fill):
    """Disque évidé : rayon extérieur r, rayon intérieur ri."""
    x, y = c
    return (f'<path fill="{fill}" fill-rule="evenodd" d="M{f(x-r)} {f(y)}a{f(r)} {f(r)} 0 1 0 {f(2*r)} 0a{f(r)} {f(r)} 0 1 0 {f(-2*r)} 0Z'
            f'M{f(x-ri)} {f(y)}a{f(ri)} {f(ri)} 0 1 0 {f(2*ri)} 0a{f(ri)} {f(ri)} 0 1 0 {f(-2*ri)} 0Z"/>')

def target(c, r, fill):
    """Cible : anneau extérieur + point central, séparés par un vide (début de la spirale)."""
    x, y = c
    return annulus(c, r, r * 0.72, fill) + f'<circle cx="{f(x)}" cy="{f(y)}" r="{f(r*0.26)}" fill="{fill}"/>'

def diamond(c, r, fill):
    """Losange plein, plus haut que large (gate de validation humaine) : la diagonale
    verticale est allongée pour une silhouette de gemme plutôt qu'un carré tourné."""
    x, y = c
    wx, hy = r * DIAMOND_WX, r * DIAMOND_HY
    return (f'<path fill="{fill}" d="M{f(x)} {f(y-hy)}L{f(x+wx)} {f(y)}'
            f'L{f(x)} {f(y+hy)}L{f(x-wx)} {f(y)}Z"/>')

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

def ribbon(seg, hwf):
    """Ruban à largeur variable, refermé en un seul contour."""
    if len(seg) < 3:
        return ""
    k = max(1, len(seg) // 40)                                 # fin, pour que l'affinement de la queue reste lisse
    idx = list(range(0, len(seg), k))
    if idx[-1] != len(seg) - 1:
        idx.append(len(seg) - 1)
    Lp, Rp = [], []
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
    Rr = Rp[::-1]
    d = f"M{f(Lp[0][0])} {f(Lp[0][1])}" + bez_cmds(Lp)
    d += f"L{f(Rr[0][0])} {f(Rr[0][1])}" + bez_cmds(Rr) + "Z"
    return d

def outline(points, hwf):
    # contour du ruban : chaque point du fil décalé de ± sa demi-largeur, le long de la normale
    out = []
    for i, (x, y, t) in enumerate(points):
        a, b = points[max(i - 2, 0)], points[min(i + 2, len(points) - 1)]
        tx, ty = b[0] - a[0], b[1] - a[1]
        n = math.hypot(tx, ty) or 1.0
        h = hwf(t)
        out.append((x - ty / n * h, y + tx / n * h))
        out.append((x + ty / n * h, y - tx / n * h))
    return out

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

    w0, w1 = (4.2, 9.2) if compact else (3.0, 7.8)             # largeur du ruban : fine -> franche

    def hwf(t):
        """Demi-largeur du ruban : t ∈ [0,1] parcourt la spirale (largeur croissante),
        t ∈ ]1,2] la queue, dont la fin s'affine jusqu'à la pointe (plus de point terminal)."""
        if t <= 1.0:
            return (w0 + (w1 - w0) * t ** 1.25) / 2
        u = t - 1.0
        return (w1 / 2) * min(1.0, (1.0 - u) / TAIL_TAPER) ** 1.35

    R_H     = 7.4 if compact else 6.3                          # demi-largeur du losange (gate humain)
    R_start = 0.60 * R_H                                       # rayon de la cible (début : l'issue)
    P = geometry(compact)
    tg = 0.62 if compact else 0.64                             # position de la validation humaine

    at = lambda t: min(P, key=lambda q: abs(q[2] - t))
    start, gate = P[0], at(tg)                                 # les marqueurs se posent sur le fil
    # le fil est coupé sous les marqueurs : la coupe reste en deçà du bord du marqueur pour
    # qu'il ne réapparaisse ni dans le vide de la cible ni sur les flancs du losange.
    cuts = [(start[0], start[1], R_start * 0.90),
            (gate[0], gate[1], R_H * 0.55)]

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
        halo = 2.4                                             # halo ambre ∝ rayon du marqueur
        o += (f'<circle cx="{f(start[0])}" cy="{f(start[1])}" r="{f(halo * R_start)}" fill="url(#{uid}h)"/>'
              f'<circle cx="{f(gate[0])}" cy="{f(gate[1])}" r="{f(halo * R_H)}" fill="url(#{uid}h)"/>')

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
        ext.append((x, y, 1.0 + s_ / (S + L2)))                # t > 1 : la queue s'affine
    full = P + ext

    # morceaux de fil entre les marqueurs
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

    for r in runs:
        o += f'<path d="{ribbon(r, hwf)}" fill="{blue}"/>'

    # marqueurs ambre : cible (début de l'issue) et losange (gate humain)
    o += target((start[0], start[1]), R_start, amb)
    o += diamond((gate[0], gate[1]), R_H, amb)

    # boîte englobante = encre réellement dessinée : contour du ruban + les deux marqueurs.
    # C'est elle qu'on centre, pour que le logo entier (queue comprise) soit centré.
    ink = outline(full, hwf)
    for mx, my, mrx, mry in ((start[0], start[1], R_start, R_start),
                             (gate[0], gate[1], R_H * DIAMOND_WX, R_H * DIAMOND_HY)):
        ink += [(mx - mrx, my - mry), (mx + mrx, my + mry)]
    xs = [p[0] for p in ink]
    ys = [p[1] for p in ink]
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