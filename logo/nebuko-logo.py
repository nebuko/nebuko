#!/usr/bin/env python3
"""NEBUKO — génère le logo (SVG, PNG, favicons, ICO) : une spirale organique.
 - un ruban qui s'élargit du centre vers l'extérieur (une petite issue qui grossit) ;
 - trois anneaux bleus posés sur le ruban (les agents) ; l'intérieur a la couleur du fond ;
 - un anneau ambre au centre (le départ), un losange ambre (la validation humaine),
   un disque ambre au bout du ruban (l'arrivée) ; lueur douce sur les repères ambre (version sombre).
Dépendances : pip install cairosvg pillow uharfbuzz fonttools
Usage : python3 nebuko_logo.py   ->  dossier final/ (la police Montserrat ExtraBold est téléchargée au besoin)
"""
import math, os, io
import cairosvg
from PIL import Image

OUT = "final/"; os.makedirs(OUT, exist_ok=True)
BG, AMBER, G0, G1 = "#0F172A", "#F59E0B", "#5A3DF0", "#2F9BFF"

# ---- réglages
TURNS      = 1.75            # tours de spirale
R_OUT      = 28.0            # rayon final
W0, W1     = 2.4, 7.4        # largeur du ruban : début -> fin
W_TIP      = 1.25            # la queue continue de s'élargir
RING_T     = (0.47, 0.66, 0.88)   # positions des anneaux d'agents sur le fil
RING_PLUS  = 2.6             # anneau = demi-largeur du fil + RING_PLUS
RING_HOLE  = 0.50
AMB_IN     = 0.64            # part ambre dans les formes de départ / validation / arrivée
GATE_T     = None            # None = losange exactement à mi-chemin (longueur d'arc) entre les anneaux 2 et 3
DIA_A, DIA_B = 2.2, 1.9      # demi-axes du losange (× demi-largeur du fil) : vertical / horizontal
DIA_ROUND  = 0.9             # arrondi des angles du losange (unités)
R0         = 2.2             # rayon de naissance du ruban (sous l'anneau)
CENTER_R   = 4.8             # anneau ambre de départ
EXIT_EXTRA = 0.0             # rad. en plus pour allonger la sortie (la cible reste sur l'axe si 0)
GLOW       = True
END_K      = 1.32            # cercle orange de fin = demi-largeur du ruban × END_K
HOLE       = {'dark': BG, 'light': '#FFFFFF', 'flat:#FFFFFF': BG, 'flat:#000000': '#FFFFFF'}

f = lambda v: f"{v:.2f}".rstrip("0").rstrip(".")

def hwf(t):
    if t <= 1:
        u = min(t / 0.22, 1); k = 0.35 + 0.65 * (3 * u * u - 2 * u ** 3)   # le ruban naît fin, sous l'anneau
        return k * (W0 + (W1 - W0) * t ** 0.9) / 2
    return W1 * (1 + (W_TIP - 1) * min(t - 1, 1)) / 2

def spiral():
    th1 = -math.pi / 2; th0 = th1 - TURNS * 2 * math.pi; n = 600; P = []
    for i in range(n + 1):
        t = i / n; th = th0 + (th1 - th0) * t; r = R0 + (R_OUT - R0) * t
        P.append((r * math.cos(th), r * math.sin(th), t))
    # amorce cachée sous l'anneau central : le ruban naît DANS l'anneau (sinon sa
    # tranche de départ, large de W0, dépasse du cercle ambre -> léger décalage)
    dth = (th1 - th0) / n; dr = (R_OUT - R0) / n
    pre = [((R0 - 0.25 * j) * math.cos(th0 - j * dth * 1.0),
            (R0 - 0.25 * j) * math.sin(th0 - j * dth * 1.0), 0.0) for j in range(1, 7)]
    return pre[::-1] + P

def bez(pts):
    d = ""; n = len(pts)
    for i in range(n - 1):
        p0 = pts[i - 1] if i else pts[i]; p1, p2 = pts[i], pts[i + 1]; p3 = pts[i + 2] if i + 2 < n else p2
        c1 = (p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6)
        c2 = (p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6)
        d += f"C{f(c1[0])} {f(c1[1])} {f(c2[0])} {f(c2[1])} {f(p2[0])} {f(p2[1])}"
    return d

def ribbon(seg):
    """contour fermé d'un ruban à largeur variable (le long de la courbe `seg`)"""
    idx = list(range(0, len(seg), max(1, len(seg) // 45)))
    if idx[-1] != len(seg) - 1: idx.append(len(seg) - 1)
    L, R = [], []
    for i in idx:
        x, y, t = seg[i]; a, b = seg[max(i - 2, 0)], seg[min(i + 2, len(seg) - 1)]
        tx, ty = b[0] - a[0], b[1] - a[1]; n = math.hypot(tx, ty) or 1
        ux, uy = tx / n, ty / n; nx, ny = -uy, ux; h = hwf(t)
        L.append((x + nx * h, y + ny * h)); R.append((x - nx * h, y - ny * h))
    R = R[::-1]
    d = f"M{f(L[0][0])} {f(L[0][1])}" + bez(L)
    return d + f"L{f(R[0][0])} {f(R[0][1])}" + bez(R) + "Z"

def diamond(c, a, b, fill, rnd=None):
    rnd = DIA_ROUND if rnd is None else rnd
    x, y = c; return (f'<path d="M{f(x)} {f(y-a)}L{f(x+b)} {f(y)}L{f(x)} {f(y+a)}L{f(x-b)} {f(y)}Z" fill="{fill}" '
                      f'stroke="{fill}" stroke-width="{rnd*2}" stroke-linejoin="round"/>')

def build(uid, mode="dark"):
    flat = mode[5:] if mode.startswith("flat:") else None
    P = spiral(); at = lambda t: min(P, key=lambda q: abs(q[2] - t))
    if GATE_T is None and len(RING_T) < 3: gate = at(0.76)   # favicon simplifié : pas d'anneaux
    elif GATE_T is None:
        i2, i3 = (min(range(len(P)), key=lambda k: abs(P[k][2] - t)) for t in RING_T[1:])
        cum = [0.0]
        for k in range(i2, i3): cum.append(cum[-1] + math.hypot(P[k+1][0]-P[k][0], P[k+1][1]-P[k][1]))
        j = min(range(len(cum)), key=lambda k: abs(cum[k] - cum[-1] / 2)); gate = P[i2 + j]
    else: gate = at(GATE_T)
    # sortie : la spirale continue simplement (même loi r(θ)) jusqu'à l'axe losange -> centre
    th1 = -math.pi / 2; phi = math.atan2(-gate[1], -gate[0])
    dth = (phi - th1) % (2 * math.pi) + EXIT_EXTRA
    slope = (R_OUT - R0) / (TURNS * 2 * math.pi)      # dr / dθ de la spirale
    ext = []; m = 40
    for q in range(1, m + 1):
        u = q / m; th = th1 + dth * u; r = R_OUT + slope * dth * u
        ext.append((r * math.cos(th), r * math.sin(th), 1 + 0.35 * u))
    full = P + ext
    gx, gy = gate[0], gate[1]; hg = hwf(gate[2]); da, db = hg * DIA_A, hg * DIA_B
    rings = [(at(t)[0], at(t)[1], hwf(t) + RING_PLUS) for t in RING_T]
    hole = HOLE.get(mode.split(':')[0] if not mode.startswith('flat:') else mode, BG)
    if flat is None:
        defs = (f'<radialGradient id="{uid}s" gradientUnits="userSpaceOnUse" cx="0" cy="0" r="34"><stop offset=".12" stop-color="{G0}"/><stop offset=".9" stop-color="{G1}"/></radialGradient>'
                f'<radialGradient id="{uid}h"><stop offset="0" stop-color="{AMBER}" stop-opacity=".32"/><stop offset=".55" stop-color="{AMBER}" stop-opacity=".16"/><stop offset="1" stop-color="{AMBER}" stop-opacity="0"/></radialGradient>')
        blue, amb = f"url(#{uid}s)", AMBER
    else: defs, blue, amb = "", flat, flat
    o = f"<defs>{defs}</defs>"
    if GLOW and mode == "dark":
        o += f'<circle cx="0" cy="0" r="15" fill="url(#{uid}h)"/><circle cx="{f(gx)}" cy="{f(gy)}" r="16" fill="url(#{uid}h)"/>'
    # le ruban d'un seul tenant ; aucune coupe
    o += f'<path d="{ribbon(full)}" fill="{blue}" stroke="{blue}" stroke-width="1.1" stroke-linejoin="round"/>'
    # pas de capuchon de départ : le ruban naît sous l'anneau central (aucun dépassement)
    # anneaux d'agents posés AU-DESSUS du ruban ; l'intérieur a la couleur du fond
    for rx, ry, ro in rings:
        o += f'<circle cx="{f(rx)}" cy="{f(ry)}" r="{f(ro)}" fill="{blue}"/><circle cx="{f(rx)}" cy="{f(ry)}" r="{f(ro*RING_HOLE)}" fill="{hole}"/>'
    # même grammaire partout : bordure bleue + intérieur (fond pour les agents, ambre pour départ / validation / arrivée)
    o += f'<circle cx="0" cy="0" r="{CENTER_R}" fill="{blue}"/><circle cx="0" cy="0" r="{f(CENTER_R*AMB_IN)}" fill="{amb}"/>'
    o += diamond((gx, gy), da, db, blue)
    ia, ib = (da + DIA_ROUND) * AMB_IN - 0.5, (db + DIA_ROUND) * AMB_IN - 0.5
    o += diamond((gx, gy), ia, ib, amb, 0.5)
    ex = full[-1]; r_end = hwf(ex[2]) * END_K
    if GLOW and mode == "dark":
        o += f'<circle cx="{f(ex[0])}" cy="{f(ex[1])}" r="{f(r_end*2.6)}" fill="url(#{uid}h)"/>'
    # cible : disque ambre (plus de bordure bleue d'intégration) > anneau bleu > point ambre
    o += f'<circle cx="{f(ex[0])}" cy="{f(ex[1])}" r="{f(r_end)}" fill="{amb}"/><circle cx="{f(ex[0])}" cy="{f(ex[1])}" r="{f(r_end*0.66)}" fill="{blue}"/><circle cx="{f(ex[0])}" cy="{f(ex[1])}" r="{f(r_end*0.36)}" fill="{amb}"/>'
    # boîte englobante (pour centrer le logo)
    pts = [(p[0] + s * hwf(p[2]), p[1] + s2 * hwf(p[2])) for p in full[::6] for s, s2 in ((1, 1), (-1, -1), (1, -1), (-1, 1))]
    pts += [(gx - db, gy - da), (gx + db, gy + da)] + [(rx + ro, ry + ro) for rx, ry, ro in rings] + [(rx - ro, ry - ro) for rx, ry, ro in rings]
    _e = full[-1]; _r = hwf(_e[2]) * END_K; pts += [(_e[0] - _r, _e[1] - _r), (_e[0] + _r, _e[1] + _r)]
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return o, (min(xs) - 1, min(ys) - 1, max(xs) + 1, max(ys) + 1)

def svg(inner, vb=(0, 0, 64, 64), sz=10):
    x, y, w, h = vb; return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{x} {y} {w} {h}" width="{w*sz}" height="{h*sz}">{inner}</svg>'

import urllib.request, struct
import uharfbuzz as hb
from fontTools.ttLib import TTFont
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
FONT = "Montserrat-ExtraBold.ttf"
FONT_URL = ("https://raw.githubusercontent.com/JulietaUla/Montserrat/"
            "master/fonts/ttf/Montserrat-ExtraBold.ttf")      # dépôt officiel, licence OFL
if not os.path.exists(FONT):
    print("Téléchargement de Montserrat ExtraBold...")
    urllib.request.urlretrieve(FONT_URL, FONT)

def mark_fit(uid, mode, target, simple=False):
    global W0, W1, RING_T
    save = (W0, W1, RING_T)
    if simple: W0, W1, RING_T = 3.6, 9.4, ()
    try: inner, bb = build(uid, mode)
    finally: W0, W1, RING_T = save
    x0, y0, x1, y1 = bb; sc = target / max(x1 - x0, y1 - y0); cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    g = f'<g transform="translate(32 32) scale({f(sc)}) translate({f(-cx)} {f(-cy)})">{inner}</g>'
    return g, (32 - (x1 - x0) / 2 * sc, 32 - (y1 - y0) / 2 * sc, 32 + (x1 - x0) / 2 * sc, 32 + (y1 - y0) / 2 * sc)

def tile(uid, target=46, simple=False, rx=14):
    g, _ = mark_fit(uid + "m", "dark", target, simple)
    bg = (f'<defs><radialGradient id="{uid}b" cx=".5" cy=".5" r=".75"><stop offset="0" stop-color="#1B2650"/><stop offset="1" stop-color="{BG}"/></radialGradient></defs>'
          f'<rect width="64" height="64" rx="{rx}" fill="url(#{uid}b)"/>')
    return svg(bg + g)

def text_outline(text, size, tr):
    tt = TTFont(FONT); gs, order, upem = tt.getGlyphSet(), tt.getGlyphOrder(), tt["head"].unitsPerEm
    font = hb.Font(hb.Face(hb.Blob(open(FONT, "rb").read()))); buf = hb.Buffer(); buf.add_str(text); buf.guess_segment_properties()
    hb.shape(font, buf, {"kern": True, "liga": False}); sc, x, d, il, ir = size / upem, 0.0, [], None, 0.0
    for info, pos in zip(buf.glyph_infos, buf.glyph_positions):
        g = order[info.codepoint]; pen = SVGPathPen(gs, ntos=f)
        gs[g].draw(TransformPen(pen, (sc, 0, 0, -sc, x + pos.x_offset * sc, 0))); c = pen.getCommands()
        if c:
            d.append(c); gl = tt["glyf"][g]; il = x + gl.xMin * sc if il is None else il; ir = x + gl.xMax * sc
        x += pos.x_advance * sc + tr * size
    return " ".join(d), il, ir, tt["OS/2"].sCapHeight * sc

def horizontal(uid, variant):
    mode = {"dark": "dark", "light": "light", "white": "flat:#FFFFFF", "black": "flat:#000000"}[variant]
    word = {"dark": "#FFFFFF", "light": BG, "white": "#FFFFFF", "black": "#000000"}[variant]
    g, ink = mark_fit(uid, mode, 56)
    d, l, r, cap = text_outline("NEBUKO", 22.0 / 0.70, 0.03)
    x0, base = ink[2] + 12 - l, 32 + cap / 2
    txt = f'<path transform="translate({f(x0)} {f(base)})" d="{d}" fill="{word}"/>'
    w = ink[2] + 12 + (r - l) + 8 + 8
    return svg(g + txt, (-8, -8, w, 80), 5)

files = {
  "mark-dark":  svg(mark_fit("a", "dark", 56)[0], (-4, -4, 72, 72)),
  "mark-light": svg(mark_fit("b", "light", 60)[0]),
  "mark-white": svg(mark_fit("c", "flat:#FFFFFF", 60)[0]),
  "mark-black": svg(mark_fit("d", "flat:#000000", 60)[0]),
  "tile": tile("t"),
  "favicon": tile("fv", 50, simple=True),
  "logo-horizontal-dark": horizontal("e", "dark"), "logo-horizontal-light": horizontal("g", "light"),
  "logo-horizontal-white": horizontal("h", "white"), "logo-horizontal-black": horizontal("i", "black"),
}
for n, v in files.items(): open(OUT + n + ".svg", "w").write(v)
png = lambda n, w, to=None: cairosvg.svg2png(bytestring=files[n].encode(), write_to=OUT + (to or n) + ".png", output_width=w)
png("mark-dark", 2048); png("mark-light", 2048); png("tile", 1024); png("logo-horizontal-dark", 4096); png("logo-horizontal-light", 4096)
for sz in (64, 48, 32, 16): png("favicon", sz, f"favicon-{sz}")
ims = [Image.open(io.BytesIO(cairosvg.svg2png(bytestring=files["favicon"].encode(), output_width=sz))).convert("RGBA") for sz in (16, 32, 48)]
blobs = []
for im in ims:
    b = io.BytesIO(); im.save(b, "PNG"); blobs.append(b.getvalue())
hdr = struct.pack("<HHH", 0, 1, 3); off = 6 + 16 * 3; ent = b""
for sz, bl in zip((16, 32, 48), blobs):
    ent += struct.pack("<BBBBHHII", sz, sz, 0, 0, 1, 32, len(bl), off); off += len(bl)
open(OUT + "favicon.ico", "wb").write(hdr + ent + b"".join(blobs))
print("ok")
