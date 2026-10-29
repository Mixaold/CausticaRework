"""Converts Bedrock RTX / PBR resource packs (texture sets: colour + MER(S) + normal or heightmap) into a
Java 26.2 LabPBR 1.3 resource pack (<name>.png, <name>_n.png, <name>_s.png) for Caustica and LabPBR shaders.

  python -P convert.py <pack dir | .zip | .mcpack | .mcaddon> [-o out.zip] [--jar 26.2.jar] [--mapping bedrock_to_java.json]

Block texture sets are mapped to Java names by name first (same name, ALIASES, renames), then by pixels
(bedrock_to_java.json, see build_mapping.py). A Bedrock colour texture replaces the Java one only when both
have the same layout; otherwise the Java colour stays and the PBR becomes the Bedrock material's median.
Entities have no texture sets in Bedrock RTX (they render rough and non-metal), so only their glow is
carried over: Bedrock emissive-alpha masks and the Java eye/glow layers become emissive _s textures.
"""
import argparse
import io
import json
import os
import re
import sys
import tempfile
import zipfile
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).parent))
from build_mapping import ALIASES, PBR_ONLY, name_candidates  # noqa: E402

HERE = Path(__file__).parent
DEFAULT_JAR = Path(os.environ.get("APPDATA", "")) / ".minecraft" / "versions" / "26.2" / "26.2.jar"
EXT_PRIORITY = (".tga", ".png", ".jpg", ".jpeg")
# Java colours that must stay vanilla: the Java model draws a tinted overlay on top of them.
KEEP_JAVA_COLOUR = {"grass_block_side"}
HEIGHT_NORMAL_STRENGTH = 2.0
# Bedrock RTX shows its normal maps with more relief than their stored tilt gives under Caustica: texel-scale
# luminance contrast of sunlit grass in Bedrock screenshots (0.11-0.12) matches Caustica with the slopes x2.3
# (x1: 0.073, x3: 0.14). Slopes (x/z, y/z) are scaled, i.e. the relief height, so tilt directions are kept.
NORMAL_MAP_STRENGTH = float(os.environ.get("NORMAL_MAP_STRENGTH", "2.3"))
# Heightmap texture sets (the plain "Vanilla RTX" pack): Bedrock turns the per-texel height steps into narrow
# bevels at texel borders, flat inside each texel. The normal map is shipped BEVEL_SCALE times larger with the
# bevels drawn in: BEVEL_BAND sub-texels on each side of a border carry the step. Caustica (fog30+) builds the
# material page at the normal map's resolution, so the colour stays at its own size (COLOUR_SCALE 1); older
# builds sample the normal at the colour's resolution and need COLOUR_SCALE = BEVEL_SCALE.
BEVEL_SCALE = 8
COLOUR_SCALE = 1
BEVEL_BAND = 1
# Edge tilt: the ramp slope is BEVEL_SCALE / (2 * BEVEL_BAND) height units per texel; 0.25 tilts a full step about
# 45 degrees, a narrow light/dark rim like Bedrock (1.5 drew black outlines, 0.4 glowing Fresnel rims).
BEVEL_STRENGTH = 0.25
# Same layout = opaque/transparent texels agree this often and luminance correlates at least this much.
LAYOUT_ALPHA_AGREEMENT = 0.95
LAYOUT_MIN_CORRELATION = 0.3
# Bedrock ignores the texture set of water (built-in water shader); smooth water F0 0.02.
WATER = {"water_still", "water_flow"}
WATER_S = (242, 5, 0, 255)
# Bedrock-only face textures, added to Java through model/blockstate overrides.
EXTRA_BLOCK_TEXTURES = {
    "pumpkin_side_lit": "jack_o_lantern_side", "pumpkin_top_lit": "jack_o_lantern_top",
    "redstone_ore_lit": "redstone_ore_lit", "deepslate/deepslate_redstone_ore_lit": "deepslate_redstone_ore_lit",
}
EXTRA_MODELS = {
    "assets/minecraft/models/block/jack_o_lantern.json": {
        "parent": "minecraft:block/orientable",
        "textures": {"front": "minecraft:block/jack_o_lantern", "side": "minecraft:block/jack_o_lantern_side",
                     "top": "minecraft:block/jack_o_lantern_top"}},
    "assets/minecraft/models/block/redstone_ore_lit.json": {
        "parent": "minecraft:block/cube_all", "textures": {"all": "minecraft:block/redstone_ore_lit"}},
    "assets/minecraft/models/block/deepslate_redstone_ore_lit.json": {
        "parent": "minecraft:block/cube_all", "textures": {"all": "minecraft:block/deepslate_redstone_ore_lit"}},
    "assets/minecraft/blockstates/redstone_ore.json": {"variants": {
        "lit=false": {"model": "minecraft:block/redstone_ore"},
        "lit=true": {"model": "minecraft:block/redstone_ore_lit"}}},
    "assets/minecraft/blockstates/deepslate_redstone_ore.json": {"variants": {
        "lit=false": {"model": "minecraft:block/deepslate_redstone_ore"},
        "lit=true": {"model": "minecraft:block/deepslate_redstone_ore_lit"}}},
}
# Bedrock entity textures drawn with an emissive-alpha material (alpha < 1 = emissive 1 - alpha) -> Java texture.
ENTITY_EMISSIVE_ALPHA = {
    "spider/spider": "spider/spider", "spider/cave_spider": "spider/cave_spider",
    "enderman/enderman": "enderman/enderman", "phantom": "phantom/phantom", "slime/magmacube_v2": "slime/magmacube",
}
# Java glow layers whose Bedrock counterpart is emissive (charged_creeper / emissive-alpha materials).
ENTITY_GLOW_LAYERS = [
    "spider/spider_eyes", "enderman/enderman_eyes", "phantom/phantom_eyes", "enderdragon/dragon_eyes",
    "creaking/creaking_eyes", "copper_golem/copper_golem_eyes", "copper_golem/copper_golem_eyes_exposed",
    "copper_golem/copper_golem_eyes_weathered", "copper_golem/copper_golem_eyes_oxidized",
    "warden/warden_heart", "warden/warden_pulsating_spots_1", "warden/warden_pulsating_spots_2",
    "warden/warden_bioluminescent_layer",
]
# Bedrock's default for surfaces without a texture set: non-metal, roughness 1 (Caustica's default is 0.9).
ENTITY_SMOOTHNESS = 0


# ---------- reading Bedrock texture sets ----------

def find_image(folder: Path, name: str):
    for ext in EXT_PRIORITY:
        p = folder / (name + ext)
        if p.exists():
            return p
    return None


def parse_uniform(v, channels: int):
    """Texture set uniform value: [r,g,b(,a)] 0..255, '#RRGGBB' / '#AARRGGBB' / '#XX', or a single number."""
    if isinstance(v, (int, float)):
        return [float(v)] * channels
    if isinstance(v, list):
        return [float(x) for x in v][:channels] + [255.0] * max(0, channels - len(v))
    s = v.lstrip("#")
    vals = [int(s[i:i + 2], 16) for i in range(0, len(s), 2)]
    if len(vals) == 4:  # ARGB
        vals = vals[1:] + vals[:1]
    if len(vals) == 1:
        vals = vals * channels
    return [float(x) for x in vals][:channels] + [255.0] * max(0, channels - len(vals))


def load_layer(folder: Path, v, channels: int, size=None):
    """Returns float32 HxWxC array 0..255, or None if the referenced image is missing."""
    if isinstance(v, str) and not v.startswith("#"):
        p = find_image(folder, v)
        if p is None:
            return None
        img = Image.open(p)
        mode = {1: "L", 3: "RGB", 4: "RGBA"}[channels]
        img = img.convert(mode)
        if size and img.size != size:
            img = img.resize(size, Image.NEAREST)
        a = np.asarray(img, dtype=np.float32)
        return a[..., None] if channels == 1 else a
    w, h = size or (16, 16)
    return np.broadcast_to(np.array(parse_uniform(v, channels), dtype=np.float32), (h, w, channels)).copy()


def load_set(folder: Path, body: dict):
    """(colour RGBA, MER(S), normal or None, height or None) as float 0..255, or None without a colour image."""
    colour = load_layer(folder, body["color"], 4)
    if colour is None:
        return None
    h, w = colour.shape[:2]
    mer_v = body.get("metalness_emissive_roughness_subsurface") or body.get("metalness_emissive_roughness")
    mer_ch = 4 if "metalness_emissive_roughness_subsurface" in body else 3
    mer = load_layer(folder, mer_v, mer_ch, (w, h)) if mer_v is not None else None
    if mer is None:
        mer = np.broadcast_to(np.array([0, 0, 255], np.float32), (h, w, 3)).copy()
    normal = load_layer(folder, body["normal"], 3, (w, h)) if "normal" in body else None
    height = load_layer(folder, body["heightmap"], 1, (w, h)) if "heightmap" in body else None
    if normal is None and height is None:
        normal = flat_normal(h, w)
    return colour, mer, normal, height


def flat_normal(h, w):
    return np.broadcast_to(np.array([128, 128, 255], np.float32), (h, w, 3)).copy()


# ---------- LabPBR encoding ----------

def normals_from_height(h: np.ndarray) -> np.ndarray:
    """Height (HxW, 0..1) -> DirectX tangent normal (HxWx3, -1..1), tiling-aware Sobel."""
    p = lambda dy, dx: np.roll(np.roll(h, dy, 0), dx, 1)
    gx = (p(-1, -1) + 2 * p(0, -1) + p(1, -1)) - (p(-1, 1) + 2 * p(0, 1) + p(1, 1))  # -dH/dx * 8
    gy = (p(-1, -1) + 2 * p(-1, 0) + p(-1, 1)) - (p(1, -1) + 2 * p(1, 0) + p(1, 1))  # -dH/dy_down * 8 (y down)
    n = np.stack([gx * HEIGHT_NORMAL_STRENGTH, gy * HEIGHT_NORMAL_STRENGTH, np.ones_like(h)], -1)
    return n / np.linalg.norm(n, axis=-1, keepdims=True)


def encode_n(normal_rgb, height):
    """LabPBR _n: R,G = DirectX normal XY (Bedrock normal maps are DirectX too), B = AO (255 = none),
    A = height (255 = surface, min 1)."""
    if height is not None:
        h = height[..., 0] / 255.0
        n = normals_from_height(h)
        rg = (n[..., :2] * 0.5 + 0.5) * 255
        a = np.clip(height[..., 0], 1, 255)
    else:
        n = normal_rgb / 255.0 * 2 - 1
        n[..., 2] = np.clip(n[..., 2], 0.05, None)
        n /= np.maximum(np.linalg.norm(n, axis=-1, keepdims=True), 1e-6)
        n[..., :2] *= NORMAL_MAP_STRENGTH
        n /= np.linalg.norm(n, axis=-1, keepdims=True)
        rg = (n[..., :2] * 0.5 + 0.5) * 255
        a = np.full(rg.shape[:2], 255.0)
    b = np.full(rg.shape[:2], 255.0)
    return np.dstack([rg, b, a])


def upscale(a: np.ndarray, k: int) -> np.ndarray:
    return np.repeat(np.repeat(a, k, 0), k, 1)


def bevel_normals(height: np.ndarray, k: int = BEVEL_SCALE) -> np.ndarray:
    """Height (HxWx1, 0..255; animated strips of square frames) -> LabPBR _n at k x resolution: flat texels,
    each border's height step spread over BEVEL_BAND sub-texels on both sides (tiles within each frame)."""
    h = height[..., 0] / 255.0
    H, W = h.shape
    fh = W if H > W and H % W == 0 else H
    out = []
    for f0 in range(0, H, fh):
        hf = h[f0:f0 + fh]
        dx = np.roll(hf, -1, 1) - hf  # step to the right neighbour (height units)
        dy = np.roll(hf, -1, 0) - hf  # step to the neighbour below
        dx[:, -1] = 0  # the block border meets another block's texture, so it stays flat
        dy[-1, :] = 0
        slope_x = np.zeros((fh * k, W * k))
        slope_y = np.zeros((fh * k, W * k))
        band_slope = k / (2.0 * BEVEL_BAND)  # height per texel across the 2*band sub-texel ramp
        for o in range(BEVEL_BAND):
            slope_x[:, k - 1 - o::k] += dx.repeat(k, 0) * band_slope  # right edge of each texel
            slope_x[:, o::k] += np.roll(dx, 1, 1).repeat(k, 0) * band_slope       # left edge (from left step)
            slope_y[k - 1 - o::k, :] += dy.repeat(k, 1) * band_slope
            slope_y[o::k, :] += np.roll(dy, 1, 0).repeat(k, 1) * band_slope
        n = np.stack([-slope_x * BEVEL_STRENGTH, -slope_y * BEVEL_STRENGTH, np.ones_like(slope_x)], -1)
        n /= np.linalg.norm(n, axis=-1, keepdims=True)
        rg = (n[..., :2] * 0.5 + 0.5) * 255
        a = np.clip(upscale(height[f0:f0 + fh, :, 0], k), 1, 255)
        out.append(np.dstack([rg, np.full(rg.shape[:2], 255.0), a]))
    return np.concatenate(out, 0)


def scale_meta(meta, k: int):
    anim = meta.get("animation") if meta else None
    if isinstance(anim, dict):
        for key in ("width", "height"):
            if key in anim:
                anim[key] *= k
    return meta


def srgb_to_linear(c):
    c = c / 255.0
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def encode_s(mer, albedo):
    """LabPBR _s from Bedrock MER(S) (0..255 each) and the albedo it is drawn with:
    R smoothness = 1 - roughness (both perceptual), B = SSS 65..255 from MERS alpha, A = emission 1..254
    (255 = none, kept for zero emission so mipmaps and atlas stitching never see a transparent pixel).
    G: Bedrock metalness is continuous (F0 = lerp(0.04, albedo, m), diffuse = albedo * (1 - m)), LabPBR is
    dielectric F0 0..229 or albedo metal 255. Each texel takes whichever of the two is closer to Bedrock's
    diffuse + specular: a dielectric with F0 = luminance of Bedrock's F0, or a full metal."""
    m, e, r = mer[..., 0] / 255.0, mer[..., 1], mer[..., 2]
    sss = mer[..., 3] if mer.shape[-1] == 4 else np.zeros_like(e)
    c = srgb_to_linear(albedo[..., :3])
    spec = 0.04 * (1 - m)[..., None] + c * m[..., None]
    diffuse = c * (1 - m)[..., None]
    f0 = np.clip(spec @ np.array([0.2126, 0.7152, 0.0722]), 0.0, 229 / 255)
    err_dielectric = (np.abs(c * (1 - f0)[..., None] - diffuse) + np.abs(f0[..., None] - spec)).sum(-1)
    err_metal = (diffuse + np.abs(c - spec)).sum(-1)
    metal = err_metal < err_dielectric
    g = np.where(metal, 255.0, np.round(f0 * 255))
    b = np.where((sss > 0) & ~metal, 65 + sss * (190 / 255), 0.0)
    a = np.where(e > 0, np.clip(np.round(e * 254 / 255), 1, 254), 255.0)
    return np.dstack([255 - r, g, b, a])


def animation_meta(vanilla, w: int, h: int):
    """The .mcmeta to ship next to a replaced colour texture. Minecraft reads .mcmeta only from the pack that
    supplies the .png, so without it an animated strip is stretched as one tall static sprite. Explicit
    frame lists are dropped when they index past the Bedrock strip's frame count."""
    if vanilla is None:
        return None
    meta = json.loads(json.dumps(vanilla))
    anim = meta.get("animation")
    if isinstance(anim, dict) and "frames" in anim:
        fw = anim.get("width", w)
        fh = anim.get("height", min(w, h) if "width" not in anim else fw)
        count = max(1, (w // max(1, fw)) * (h // max(1, fh)))
        idx = [f["index"] if isinstance(f, dict) else f for f in anim["frames"]]
        if max(idx) >= count:
            del anim["frames"]
    return meta


def to_png(a: np.ndarray, mode: str) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(np.clip(np.round(a), 0, 255).astype(np.uint8), mode).save(buf, "PNG", optimize=True)
    return buf.getvalue()


def first_frame(a: np.ndarray) -> np.ndarray:
    h, w = a.shape[:2]
    return a[:w] if h > w and h % w == 0 else a


def resize(a: np.ndarray, w: int, h: int) -> np.ndarray:
    if a.shape[1] == w and a.shape[0] == h:
        return a
    return np.asarray(Image.fromarray(np.clip(a, 0, 255).astype(np.uint8)).resize((w, h), Image.NEAREST),
                      np.float32)


def same_layout(bedrock: np.ndarray, java: np.ndarray, opaque: bool) -> bool:
    """Bedrock and Java colour (first frames, RGBA) show the same art layout, possibly recoloured. The alpha of
    a block that is opaque in Java is a Bedrock tint mask, not coverage, so it is not compared."""
    b = resize(first_frame(bedrock), java.shape[1], java.shape[0])
    ab, aj = (b[..., 3] > 127) | opaque, java[..., 3] > 127
    if (ab == aj).mean() < LAYOUT_ALPHA_AGREEMENT:
        return False
    both = ab & aj
    gb, gj = b[..., :3].mean(-1)[both], java[..., :3].mean(-1)[both]
    if both.sum() < 5 or gb.std() < 1 or gj.std() < 1:
        return True
    return np.corrcoef(gb, gj)[0, 1] >= LAYOUT_MIN_CORRELATION


def median_material(colour, mer):
    """The Bedrock material as one uniform MER(S): medians over its opaque texels."""
    opaque = colour[..., 3] > 0
    px = mer[opaque] if opaque.any() else mer.reshape(-1, mer.shape[-1])
    return np.median(px, axis=0)


# ---------- pack discovery ----------

def open_input(path: Path, tmp: Path) -> Path:
    if path.is_dir():
        return path
    with zipfile.ZipFile(path) as z:
        z.extractall(tmp / "in")
    root = tmp / "in"
    # .mcaddon / zips of zips: extract nested packs too
    for inner in list(root.rglob("*")):
        if inner.suffix.lower() in (".mcpack", ".zip"):
            with zipfile.ZipFile(inner) as z:
                z.extractall(inner.with_suffix(""))
    return root


def find_block_dirs(root: Path):
    return sorted({ts.parent for ts in root.rglob("*.texture_set.json")
                   if "textures" in ts.parts and "blocks" in ts.parts})


def blocks_root(d: Path) -> Path:
    while d.name != "blocks":
        d = d.parent
    return d


def read_java(jar: Path):
    """Java block textures (RGBA arrays), their .mcmeta, entity and item texture RGBA arrays."""
    blocks, meta, entities, items = {}, {}, {}, {}
    with zipfile.ZipFile(jar) as jz:
        for n in jz.namelist():
            if n.startswith("assets/minecraft/textures/block/") and n.endswith(".png"):
                blocks[n[32:-4]] = np.asarray(Image.open(io.BytesIO(jz.read(n))).convert("RGBA"), np.float32)
            elif n.startswith("assets/minecraft/textures/block/") and n.endswith(".png.mcmeta"):
                meta[n[32:-11]] = json.loads(jz.read(n))
            elif n.startswith("assets/minecraft/textures/entity/") and n.endswith(".png"):
                entities[n[33:-4]] = np.asarray(Image.open(io.BytesIO(jz.read(n))).convert("RGBA"), np.float32)
            elif n.startswith("assets/minecraft/textures/item/") and n.endswith(".png"):
                items[n[31:-4]] = np.asarray(Image.open(io.BytesIO(jz.read(n))).convert("RGBA"), np.float32)
    return blocks, meta, entities, items


# ---------- conversion ----------

def assign_targets(sets: dict, mapping: dict, java: dict) -> dict:
    """Java texture -> (Bedrock texture set, pbr_only). A Java name the Bedrock set is named after (same name,
    ALIASES, renames) beats a pixel match; ties prefer the non-"_carried" set, then the first by name."""
    claims = {}
    for rel in sets:
        named = [j for j in [ALIASES.get(rel)] + name_candidates(rel) if j]
        for t in dict.fromkeys(named + mapping.get(rel, [])):
            if t in java:
                claims.setdefault(t, []).append((0 if t in named else 1, rel.endswith("_carried"), rel, False))
        for t in PBR_ONLY.get(rel, []):
            if t in java:
                claims.setdefault(t, []).append((0, False, rel, True))
    return {t: (min(c)[2], min(c)[3]) for t, c in claims.items()}


def convert_block(t, rel, pbr_only, layers, java, java_meta, java_opaque):
    """-> (colour png or None, n png, s png, mcmeta or None, mode)"""
    colour, mer, normal, height = layers
    jc = java[t]
    h, w = colour.shape[:2]
    jh, jw = jc.shape[:2]
    same_shape = abs(w / h - jw / jh) < 1e-6
    layout = same_layout(colour, first_frame(jc), t in java_opaque) or ("glass" in t and "pane" not in t)
    if layout and same_shape:
        keep_java = pbr_only or t in KEEP_JAVA_COLOUR
        albedo = colour
        s = encode_s(mer, albedo)
        n = encode_n(normal, height)
        mode = "pbr-only" if keep_java else "full"
    elif layout and w == jw:
        # Animated strips with different frame counts (lava_still): Java colour and animation, Bedrock PBR of
        # the first frame (Caustica reads only the first frame of an animated _s/_n).
        keep_java = True
        f = lambda a: first_frame(a)
        s = encode_s(f(mer), f(colour))
        n = encode_n(f(normal) if normal is not None else None, f(height) if height is not None else None)
        mode = "first-frame"
    else:
        # Different layout: Java colour; the Bedrock material as a uniform surface over the Java texels.
        keep_java = True
        uni = np.broadcast_to(median_material(colour, mer), jc.shape[:2] + (mer.shape[-1],)).copy()
        s = encode_s(uni, jc)
        n = encode_n(flat_normal(jh, jw), None)
        mode = "uniform"
    if t in WATER:
        s = np.broadcast_to(np.array(WATER_S, np.float32), s.shape).copy()
    c_png = meta = None
    if mode in ("full", "pbr-only") and height is not None and BEVEL_SCALE > 1:
        # bevelled heightmap relief needs the colour at the normal map's resolution, Java colour included
        src = jc if keep_java else colour
        n = bevel_normals(height if not keep_java or jc.shape == colour.shape else resize(height, jw, jh))
        out = src.copy()
        if t in java_opaque:
            out[..., 3] = 255
        c_png = to_png(upscale(out, COLOUR_SCALE), "RGBA")
        meta = scale_meta(json.loads(json.dumps(java_meta[t])) if keep_java and t in java_meta
                          else animation_meta(java_meta.get(t), w, h), COLOUR_SCALE)
        return c_png, to_png(n, "RGBA"), to_png(s, "RGBA"), meta, mode
    if not keep_java:
        out = colour.copy()
        # Bedrock keeps a biome-tint mask in the alpha of opaque blocks (grass_side_snowed is all 0);
        # Java reads it as cutout, so a block that is opaque in vanilla must stay opaque.
        if t in java_opaque:
            out[..., 3] = 255
        c_png = to_png(out, "RGBA")
        meta = animation_meta(java_meta.get(t), w, h)
    return c_png, to_png(n, "RGBA"), to_png(s, "RGBA"), meta, mode


def texture_sets_in(root: Path, sub: str) -> dict:
    """{path under textures/<sub> without extension: (folder, texture set body)}"""
    base = root / "textures" / sub
    out = {}
    for ts in sorted(base.rglob("*.texture_set.json")) if base.is_dir() else []:
        try:
            out[ts.relative_to(base).as_posix()[:-len(".texture_set.json")]] = (
                ts.parent, json.loads(ts.read_text(encoding="utf-8-sig"))["minecraft:texture_set"])
        except Exception:
            pass
    return out


def fit_sheet(a: np.ndarray, h: int, w: int) -> np.ndarray:
    """A Bedrock sheet laid over a Java one of another size: cut to it, or padded with transparent zeros
    (Java 64x64 humanoid sheets keep the Bedrock 64x32 layout in their top half)."""
    out = np.zeros((h, w) + a.shape[2:], a.dtype)
    hh, ww = min(h, a.shape[0]), min(w, a.shape[1])
    out[:hh, :ww] = a[:hh, :ww]
    return out


def colour_fit(b: np.ndarray, j: np.ndarray, crop: bool) -> float:
    """Mean abs RGBA error of a Bedrock colour texture against a Java one over their opaque texels; a larger
    Bedrock sheet may extend the Java UV layout (compared on its top-left corner) when crop is set."""
    if b.shape != j.shape:
        if not crop:
            return float("inf")
        b = fit_sheet(b, j.shape[0], j.shape[1])
    # Bedrock sheets often keep extra opaque (unused) UV area, so only texels opaque in both are compared,
    # and they must cover most of the Java texture.
    jo = j[..., 3] > 0
    m = (b[..., 3] > 0) & jo
    if m.sum() < 4 or m.sum() < 0.6 * jo.sum():
        return float("inf")
    return float(np.abs(b - j)[m][:, :3].mean())


def match_by_pixels(java: dict, bsets: dict, max_err: float = 8.0) -> dict:
    """Java texture -> Bedrock texture set with the same art: same base name first (crop allowed), then any
    set of the same size. Bedrock vanilla keeps the Java art for most textures, so pixels decide."""
    colours, by_base, by_shape = {}, {}, {}
    for rel, (folder, body) in bsets.items():
        c = load_layer(folder, body["color"], 4) if isinstance(body.get("color"), str) else None
        if c is None:
            continue
        colours[rel] = c
        by_base.setdefault(rel.split("/")[-1], []).append(rel)
        by_shape.setdefault(c.shape, []).append(rel)
    out = {}
    for j, ja in java.items():
        if ja[..., 3].max() == 0:
            continue
        best = (max_err, None)
        for rel in by_base.get(j.split("/")[-1], []):
            e = colour_fit(colours[rel], ja, True)
            if e < best[0]:
                best = (e, rel)
        if best[1] is None:
            for rel in by_shape.get(ja.shape, []):
                e = colour_fit(colours[rel], ja, False)
                if e < best[0]:
                    best = (e, rel)
        if best[1] is not None:
            out[j] = best[1]
    return out


def convert_sets_to_java(java: dict, bsets: dict) -> dict:
    """Standalone textures (entities, items): {java path: (s png, n png or None, bedrock rel)}; the PBR is cut
    to the Java size and keyed to the Java colour."""
    out = {}
    for j, rel in match_by_pixels(java, bsets).items():
        layers = load_set(*bsets[rel])
        if layers is None:
            continue
        colour, mer, normal, height = layers
        jh, jw = java[j].shape[:2]
        crop = lambda a: fit_sheet(a, jh, jw) if a is not None else None
        has_normal = "normal" in bsets[rel][1] or "heightmap" in bsets[rel][1]
        out[j] = (to_png(encode_s(crop(mer), java[j]), "RGBA"),
                  to_png(encode_n(crop(normal), crop(height)), "RGBA") if has_normal else None, rel)
    return out


# Java standalone texture -> Bedrock texture set names (base names) where the names differ between editions.
ARMOR = {"leather": "cloth", "chainmail": "chain", "iron": "iron", "gold": "gold", "diamond": "diamond",
         "netherite": "netherite", "copper": "copper"}
STANDALONE_ALIASES = {
    **{f"equipment/humanoid/{j}": [f"{b}_1"] for j, b in ARMOR.items()},
    **{f"equipment/humanoid_leggings/{j}": [f"{b}_2"] for j, b in ARMOR.items()},
    "chest/normal_left": ["double_normal"], "chest/normal_right": ["double_normal"],
    "chest/trapped_left": ["trapped_double"], "chest/trapped_right": ["trapped_double"],
    "armorstand/armorstand": ["armor_stand"], "minecart/minecart": ["minecart"],
}
# Java block textures of block entities drawn by Bedrock as entities.
BLOCK_FROM_ENTITY = [(re.compile(r"(\w+?)_bed_.*"), lambda m: [f"bed/{m.group(1)}".replace("light_gray", "silver")]),
                     (re.compile(r"(\w+)_hanging_sign"), lambda m: [f"{m.group(1)}_hanging_sign"]),
                     (re.compile(r"oak_sign"), lambda m: ["sign"]),
                     (re.compile(r"(\w+)_sign"), lambda m: [f"{m.group(1)}_sign"]),
                     (re.compile(r"bed_.*"), lambda m: ["bed/white"]),
                     (re.compile(r"conduit"), lambda m: ["conduit_base"])]


def standalone_names(j: str) -> list:
    """Bedrock names to try for a Java standalone texture: aliases, itself, the name without variant suffixes,
    then its folder (fox/fox_snow -> fox, sheep/sheep_wool -> sheep, chest/copper_left -> copper)."""
    base = j.split("/")[-1]
    stripped = re.sub(r"(_(left|right|baby|sleep|overlay|outer_layer|undercoat|wool|snow|exposed|weathered|"
                      r"oxidized|crackiness_\w+|collar|stinger))+$", "", base)
    folder = j.split("/")[-2] if "/" in j else base
    return STANDALONE_ALIASES.get(j, []) + [j, base, stripped, folder]


def find_by_name(names, parent, bsets):
    """Bedrock set whose base name is one of names (or a full rel path), preferring the same parent folder."""
    by_base = {}
    for rel in bsets:
        by_base.setdefault(rel.split("/")[-1], []).append(rel)
    for n in names:
        if n in bsets:
            return n
        hits = by_base.get(n.split("/")[-1], [])
        if hits:
            return sorted(hits, key=lambda r: (r.rsplit("/", 1)[0] != parent, r))[0]
    return None


def convert_by_name(java: dict, bsets: dict, names_for) -> dict:
    """Second pass for textures whose art differs between editions: the Bedrock set of the same name gives a
    per-texel PBR when the layouts agree, else its median material over the Java texture."""
    out = {}
    for j, ja in java.items():
        if ja[..., 3].max() == 0:
            continue
        rel = find_by_name(names_for(j), j.rsplit("/", 1)[0] if "/" in j else "", bsets)
        layers = load_set(*bsets[rel]) if rel else None
        if layers is None:
            continue
        colour, mer, normal, height = layers
        jh, jw = ja.shape[:2]
        if colour.shape[0] >= jh and colour.shape[1] >= jw and abs(colour.shape[1] / colour.shape[0] - jw / jh) < 1e-6                 and same_layout(colour, ja, False):
            mer_j, colour_j = resize(mer, jw, jh), resize(colour, jw, jh)
            out[j] = (to_png(encode_s(mer_j, ja), "RGBA"), None, rel + " (layout)")
        else:
            uni = np.broadcast_to(median_material(colour, mer), (jh, jw, mer.shape[-1])).copy()
            out[j] = (to_png(encode_s(uni, ja), "RGBA"), None, rel + " (uniform)")
    return out


def convert_entities(root: Path, java_entities: dict):
    """Entity _s textures: glow from Bedrock emissive-alpha masks and Java eye/glow layers, else Bedrock's
    rough non-metal default. -> {java entity path: (s png, emissive texel count)}"""
    out = {}
    tex = next((d for d in root.rglob("entity") if d.is_dir() and d.parent.name == "textures"), None)
    for brel, jrel in ENTITY_EMISSIVE_ALPHA.items():
        p = find_image(tex, brel) if tex else None
        if p is None or jrel not in java_entities:
            continue
        b = np.asarray(Image.open(p).convert("RGBA"), np.float32)
        j = java_entities[jrel]
        jh, jw = j.shape[:2]
        if b.shape[0] < jh or b.shape[1] < jw:
            continue
        b = b[:jh, :jw]  # Bedrock sheets extend the Java UV layout downwards
        mask = (b[..., 3] > 0) & (b[..., 3] < 255) & (j[..., 3] > 0)
        if not mask.any() or np.abs(b[..., :3] - j[..., :3])[mask].mean() > 24:
            continue
        emission = np.where(mask, 1 - b[..., 3] / 255, 0)
        out[jrel] = (entity_s(emission), int(mask.sum()))
    for jrel in ENTITY_GLOW_LAYERS:
        j = java_entities.get(jrel)
        if j is None:
            continue
        mask = j[..., 3] > 0
        out[jrel] = (entity_s(mask.astype(np.float32)), int(mask.sum()))
    return out


def entity_s(emission):
    h, w = emission.shape
    a = np.where(emission > 0, np.clip(np.round(emission * 254), 1, 254), 255)
    return to_png(np.dstack([np.full((h, w), float(ENTITY_SMOOTHNESS)), np.full((h, w), 10.0),
                             np.zeros((h, w)), a]), "RGBA")


# ---------- main ----------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pack", type=Path)
    ap.add_argument("-o", "--out", type=Path)
    ap.add_argument("--jar", type=Path, default=DEFAULT_JAR)
    ap.add_argument("--mapping", type=Path, default=HERE / "bedrock_to_java.json")
    ap.add_argument("--name", help="pack name shown in Minecraft")
    ap.add_argument("--vanilla", type=Path, default=HERE.parent / "converted" / "_bedrock_vanilla",
                    help="unpacked Bedrock vanilla packs (bedrock_vanilla.py): PBR of mobs, items and blocks "
                         "the RTX pack does not override")
    args = ap.parse_args()

    mapping = json.loads(args.mapping.read_text(encoding="utf-8"))["mapping"]
    java, java_meta, java_entities, java_items = read_java(args.jar)
    java_opaque = {t for t, a in java.items() if a[..., 3].min() == 255}

    name = args.name or (args.pack.stem + " LabPBR")
    out = args.out or args.pack.with_name(re.sub(r"[^\w\- .()+]", "_", name) + ".zip")
    with tempfile.TemporaryDirectory() as td:
        root = open_input(args.pack, Path(td))
        dirs = find_block_dirs(root)
        if not dirs:
            sys.exit("no textures/blocks/*.texture_set.json found - not a Bedrock RTX/PBR pack?")
        manifest = next(root.rglob("manifest.json"), None)
        icon = next(root.rglob("pack_icon.png"), None)

        sets, skipped = {}, []
        for d in dirs:
            broot = blocks_root(d)
            for ts in sorted(d.glob("*.texture_set.json")):
                rel = ts.relative_to(broot).as_posix()[:-len(".texture_set.json")]
                try:
                    sets[rel] = (ts.parent, json.loads(ts.read_text(encoding="utf-8-sig"))["minecraft:texture_set"])
                except Exception as ex:
                    skipped.append(f"{rel}: bad json ({ex})")

        cache = {}

        def layers(rel):
            if rel not in cache:
                cache[rel] = load_set(*sets[rel])
            return cache[rel]

        done, modes = {}, {}
        for t, (rel, pbr_only) in sorted(assign_targets(sets, mapping, java).items()):
            ls = layers(rel)
            if ls is None:
                skipped.append(f"{rel}: colour image missing")
                continue
            done[t] = (rel,) + convert_block(t, rel, pbr_only, ls, java, java_meta, java_opaque)
            modes[done[t][-1]] = modes.get(done[t][-1], 0) + 1
        # Bedrock vanilla (Vibrant Visuals) texture sets: RTX renders them wherever the RTX pack has none.
        vv = args.vanilla if args.vanilla and args.vanilla.is_dir() else None
        if vv:
            vv_blocks = texture_sets_in(vv, "blocks")
            missing = {t: java[t] for t in java if t not in done}
            for t, rel in sorted(match_by_pixels(missing, vv_blocks).items()):
                ls = load_set(*vv_blocks[rel])
                if ls is None:
                    continue
                done[t] = ("vanilla:" + rel,) + convert_block(t, rel, False, ls, java, java_meta, java_opaque)
                modes["vanilla-" + done[t][-1]] = modes.get("vanilla-" + done[t][-1], 0) + 1
            vv_entity_sets = {**texture_sets_in(vv, "entity"),
                              **{"models/" + k: v for k, v in texture_sets_in(vv, "models").items()}}
            vv_items = texture_sets_in(vv, "items")
            standalone = {"entity": convert_sets_to_java(java_entities, vv_entity_sets),
                          "item": convert_sets_to_java(java_items, vv_items)}
            for kind, jt, bs in (("entity", java_entities, vv_entity_sets), ("item", java_items, vv_items)):
                rest = {j: a for j, a in jt.items() if j not in standalone[kind]}
                standalone[kind].update(convert_by_name(rest, bs, standalone_names))
            # block textures of Bedrock block entities (beds, signs): the entity's material, uniform
            for t in sorted(java):
                if t in done:
                    continue
                for rx, names in BLOCK_FROM_ENTITY:
                    m = rx.fullmatch(t)
                    rel = find_by_name(names(m), "", vv_entity_sets) if m else None
                    if rel:
                        ls = load_set(*vv_entity_sets[rel])
                        if ls is not None:
                            uni = np.broadcast_to(median_material(ls[0], ls[1]),
                                                  java[t].shape[:2] + (ls[1].shape[-1],)).copy()
                            done[t] = ("vanilla-entity:" + rel, None,
                                       to_png(encode_n(flat_normal(*java[t].shape[:2]), None), "RGBA"),
                                       to_png(encode_s(uni, java[t]), "RGBA"), None, "entity-uniform")
                            modes["entity-uniform"] = modes.get("entity-uniform", 0) + 1
                        break
        else:
            standalone = {"entity": {}, "item": {}}
        extra = {}
        for rel, t in EXTRA_BLOCK_TEXTURES.items():
            ls = layers(rel) if rel in sets else None
            if ls is None:
                continue
            colour, mer, normal, height = ls
            solid = colour.copy()
            solid[..., 3] = 255
            if height is not None and BEVEL_SCALE > 1:
                extra[t] = (rel, to_png(upscale(solid, COLOUR_SCALE), "RGBA"), to_png(bevel_normals(height), "RGBA"),
                            to_png(encode_s(mer, colour), "RGBA"))
            else:
                extra[t] = (rel, to_png(solid, "RGBA"), to_png(encode_n(normal, height), "RGBA"),
                            to_png(encode_s(mer, colour), "RGBA"))
        entities = convert_entities(root, java_entities)
        used = {v[0] for v in done.values()} | {v[0] for v in extra.values()}
        unmapped = sorted(set(sets) - used)

        desc = f"LabPBR from Bedrock pack: {name}"
        if manifest:
            try:
                hd = json.loads(manifest.read_text(encoding="utf-8-sig"))["header"]
                desc = f"{hd.get('name', name)} (Bedrock RTX -> LabPBR)"
            except Exception:
                pass
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as oz:
            oz.writestr("pack.mcmeta", json.dumps({"pack": {"description": desc, "min_format": 65, "max_format": 100}}, indent=2))
            if icon:
                oz.write(icon, "pack.png")
            for t, (rel, c, n, s, meta, mode) in sorted(done.items()):
                base = f"assets/minecraft/textures/block/{t}"
                # Vanilla RTX water is a flat grey; Java's own water texture keeps the pixel pattern Caustica turns
                # into Bedrock's ripple streaks, so water ships PBR only.
                if c is not None and t not in WATER:
                    oz.writestr(base + ".png", c)
                    if meta is not None:
                        oz.writestr(base + ".png.mcmeta", json.dumps(meta, indent=1))
                oz.writestr(base + "_n.png", n)
                oz.writestr(base + "_s.png", s)
            if all(t in extra for t in ("jack_o_lantern_side", "jack_o_lantern_top", "redstone_ore_lit",
                                        "deepslate_redstone_ore_lit")):
                for t, (rel, c, n, s) in sorted(extra.items()):
                    base = f"assets/minecraft/textures/block/{t}"
                    oz.writestr(base + ".png", c)
                    oz.writestr(base + "_n.png", n)
                    oz.writestr(base + "_s.png", s)
                for path, model in EXTRA_MODELS.items():
                    oz.writestr(path, json.dumps(model, indent=2))
            for kind, conv in standalone.items():
                for jrel, (s, n, _) in sorted(conv.items()):
                    if kind == "entity" and jrel in entities:
                        continue
                    oz.writestr(f"assets/minecraft/textures/{kind}/{jrel}_s.png", s)
                    if n is not None:
                        oz.writestr(f"assets/minecraft/textures/{kind}/{jrel}_n.png", n)
            for jrel, (s, _) in sorted(entities.items()):
                oz.writestr(f"assets/minecraft/textures/entity/{jrel}_s.png", s)
            report = {"converted": {t: v[0] for t, v in sorted(done.items())},
                      "mode": {t: v[-1] for t, v in sorted(done.items())},
                      "extra_textures": {t: v[0] for t, v in sorted(extra.items())},
                      "entity_glow_texels": {k: v[1] for k, v in sorted(entities.items())},
                      "entity_pbr": {k: v[2] for k, v in sorted(standalone["entity"].items())},
                      "item_pbr": {k: v[2] for k, v in sorted(standalone["item"].items())},
                      "java_entity_without_pbr": sorted(set(java_entities) - set(standalone["entity"]) - set(entities)),
                      "skipped": skipped, "unmapped_bedrock": unmapped,
                      "java_without_pbr": sorted(set(java) - set(done))}
            oz.writestr("conversion-report.json", json.dumps(report, indent=1))
    print(f"{out}: {len(done)} Java block textures {modes}, {len(extra)} Bedrock-only faces, "
          f"{len(standalone['entity'])} entity + {len(standalone['item'])} item PBR, {len(entities)} entity glow textures; {len(unmapped)} Bedrock sets unused, {len(skipped)} skipped")
    for s in skipped[:15]:
        print("  skipped", s)


if __name__ == "__main__":
    main()
