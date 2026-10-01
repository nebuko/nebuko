#!/usr/bin/env python3
"""NEBUKO — spirale en O, v3
 - le fil est continu : plus aucun nœud bleu, seul le ruban dessine la spirale
 - trois repères ambre racontent le parcours : le disque plein au centre (le début, d'où
   le ruban sort), le grand losange (la validation humaine) posé sur l'axe centre →
   destination, et le disque plein à la pointe du fil (la destination) — les trois
   partagent le même axe
 - le ruban prend sa pleine largeur en fin de spirale et se termine net, refermé par le
   disque d'arrivée ; deux jalons de sa propre couleur, isolés par une coupure étroite,
   ponctuent la spirale
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
DIAMOND_WIDE  = 1.25                       # losange : demi-axe en travers / demi-largeur du fil
DIAMOND_ELONG = 2.00                       # losange : demi-axe le long du fil / demi-axe en travers
GATE_WINDOW = (0.55, 0.92)                 # fenêtre où l'on pose le losange sur le fil
DOT_TS = (0.34, 0.68)                      # jalons du fil : un par coupure
BEAD_R = 0.90                              # rayon d'un jalon = BEAD_R × demi-largeur du fil
BEAD_GAP = 0.55                            # vide étroit et constant autour d'un jalon

def f(v):
    return f"{v:.2f}".rstrip("0").rstrip(".")

# ------------------------------------------------------------------ briques SVG
def diamond(c, tangent, normal, across, along, fill):
    """Losange plein posé sur le fil (gate de validation humaine). Il est aligné sur le
    trajet : `across` en travers du fil (il le barre) et `along` le long du fil."""
    x, y = c
    tx, ty = tangent
    nx, ny = normal
    pts = ((x + nx * across, y + ny * across), (x + tx * along, y + ty * along),
           (x - nx * across, y - ny * across), (x - tx * along, y - ty * along))
    return f'<path fill="{fill}" d="M' + "L".join(f"{f(px)} {f(py)}" for px, py in pts) + 'Z"/>'

def plain_dot(c, r, fill):
    """Disque plein : le point de départ et les jalons du fil (ni anneau, ni trou)."""
    x, y = c
    return f'<circle cx="{f(x)}" cy="{f(y)}" r="{f(r)}" fill="{fill}"/>'

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
    R, r0 = 27.0, 0.0                     # le fil part du centre : le ruban sort du disque ambre
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
        t ∈ ]1,2] la queue, qui garde sa largeur maximale et se termine net."""
        if t <= 1.0:
            return (w0 + (w1 - w0) * t ** 1.25) / 2
        return w1 / 2

    R_H    = 7.4 if compact else 6.3                           # repère ambre : rayon de référence
    R_dot  = 0.60 * R_H                                        # rayon du cercle plein du centre (le début)
    R_dest = 0.62 * R_H                                        # rayon du disque d'arrivée (la destination)
    P = geometry(compact)
    at = lambda t: min(P, key=lambda q: abs(q[2] - t))

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
        ext.append((x, y, 1.0 + s_ / (S + L2)))                # t > 1 : queue de largeur constante
    full = P + ext
    dest = ext[-1]                                             # pointe du fil : la destination
    # le disque d'arrivée coiffe la pointe du fil : centré sur l'axe, il la referme d'un bout
    # bien rond, le ruban se perdant dessous (disque soudé à l'extrémité).
    dst = (dest[0], dest[1])

    # le losange se pose là où le fil recoupe l'axe centre → destination, du côté opposé à
    # celle-ci : le début, le losange et la destination se retrouvent ainsi alignés.
    axis = lambda p: abs(p[0] * dest[1] - p[1] * dest[0])
    gate = min((p for p in P if GATE_WINDOW[0] <= p[2] <= GATE_WINDOW[1]), key=axis)

    # repère local du fil au passage du losange : il est centré sur le trajet, aligné sur le
    # ruban, un peu plus large que lui et étiré en gemme le long du fil — c'est lui qui barre
    # le trajet et fait office de grosse coupure.
    i = P.index(gate)
    g0, g1 = P[max(i - 3, 0)], P[min(i + 3, len(P) - 1)]
    gx, gy = g1[0] - g0[0], g1[1] - g0[1]
    gn = math.hypot(gx, gy) or 1.0
    tangent, normal = (gx / gn, gy / gn), (-gy / gn, gx / gn)
    across = hwf(gate[2]) * DIAMOND_WIDE
    along = across * DIAMOND_ELONG

    # jalons posés sur la spirale, de la couleur du fil : un peu plus petits que lui et
    # isolés par une coupure étroite, identique pour tous, pour que l'œil suive le fil.
    beads = [(at(t), hwf(t) * BEAD_R) for t in DOT_TS]

    # le fil est coupé sous les marqueurs : grosse coupure sous le losange, étroite sous les
    # jalons, pour que rien ne réapparaisse dans leurs vides.
    cuts = [(gate[0], gate[1], across)]
    cuts += [(p[0], p[1], r + BEAD_GAP) for p, r in beads]

    if flat is None:
        defs = (f'<radialGradient id="{uid}s" gradientUnits="userSpaceOnUse" cx="0" cy="0" r="34">'
                f'<stop offset=".15" stop-color="{G0}"/><stop offset=".9" stop-color="{G1}"/></radialGradient>')
        blue, amb = f"url(#{uid}s)", AMBER
    else:
        defs, blue, amb = "", flat, flat

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

    o = "".join(f'<path d="{ribbon(r, hwf)}" fill="{blue}"/>' for r in runs)

    # jalons posés sur la spirale, de la couleur du fil
    o += "".join(plain_dot((p[0], p[1]), r, blue) for p, r in beads)

    # repères ambre : le début (disque plein au centre), la validation humaine (le grand
    # losange) et la destination (disque plein qui coiffe la pointe du fil)
    o += plain_dot((0, 0), R_dot, amb)
    o += diamond((gate[0], gate[1]), tangent, normal, across, along, amb)
    o += plain_dot(dst, R_dest, amb)

    # boîte englobante = encre réellement dessinée : contour du ruban, jalons et marqueurs.
    # C'est elle qu'on centre, pour que le logo entier (queue comprise) soit centré.
    ink = outline(full, hwf)
    for mx, my, mrx, mry in ((0, 0, R_dot, R_dot), (dst[0], dst[1], R_dest, R_dest)):
        ink += [(mx - mrx, my - mry), (mx + mrx, my + mry)]
    ink += [(gate[0] + normal[0] * across, gate[1] + normal[1] * across),
            (gate[0] - normal[0] * across, gate[1] - normal[1] * across),
            (gate[0] + tangent[0] * along, gate[1] + tangent[1] * along),
            (gate[0] - tangent[0] * along, gate[1] - tangent[1] * along)]
    for p, r in beads:
        ink += [(p[0] - r, p[1] - r), (p[0] + r, p[1] + r)]
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