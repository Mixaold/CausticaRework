"""Builds bedrock_to_java.json: Bedrock block texture name -> Java block texture name(s).

Bedrock vanilla RTX packs keep the vanilla colour textures, so names are matched by pixels: every Java
texture in the 26.2 client jar is compared against every Bedrock colour texture of the reference pack.
Exact RGBA matches first, then the closest same-size texture under a small error threshold, then plain
name aliases. Usage: python -I build_mapping.py <reference bedrock pack dir> <java client jar> <out.json>
"""
import io
import json
import sys
import zipfile
from pathlib import Path

import numpy as np
from PIL import Image

MAX_MAE = 6.0  # mean abs error per channel (0..255) for a "same art" near match

# Bedrock name -> Java name for textures whose pixels differ between editions (or are tinted differently).
ALIASES = {
    "grass_top": "grass_block_top", "grass_side": "grass_block_side","grass_side_carried": "grass_block_side", "grass_side_snowed": "grass_block_snow",
    "dirt_podzol_top": "podzol_top", "dirt_podzol_side": "podzol_side", "mycelium_top": "mycelium_top",
    "leaves_oak": "oak_leaves", "leaves_spruce": "spruce_leaves", "leaves_birch": "birch_leaves",
    "leaves_jungle": "jungle_leaves", "leaves_acacia": "acacia_leaves", "leaves_big_oak": "dark_oak_leaves",
    "water_still_grey": "water_still", "water_flow_grey": "water_flow", "tallgrass": "short_grass",
    "double_plant_grass_top": "tall_grass_top", "double_plant_grass_bottom": "tall_grass_bottom",
    "vine": "vine", "waterlily": "lily_pad", "reeds": "sugar_cane",
    "coral_purple": "bubble_coral_block", "honey_bottom": "honey_block_bottom", "honey_side": "honey_block_side",
    "honey_top": "honey_block_top", "slime": "slime_block", "pumpkin_stem_disconnected": "pumpkin_stem",
    "pumpkin_face_lit": "jack_o_lantern", "noteblock": "note_block", "pumpkin_face_off": "carved_pumpkin",
}

# Java textures that get a Bedrock texture set's PBR but keep their own colour: another block's art
# (golden dandelion) or a face Bedrock draws with a shared texture.
PBR_ONLY = {
    "flower_dandelion": ["golden_dandelion"], "dried_kelp_top": ["dried_kelp_bottom"],
    "eyeblossom_stem_blooming": ["open_eyeblossom"],
}


WOOD = {"wood": "oak", "acacia": "acacia", "dark_oak": "dark_oak", "jungle": "jungle", "spruce": "spruce",
        "birch": "birch", "iron": "iron"}
GHAST_FACE = {"top": "top", "bottom": "bottom", "front": "north", "back": "south", "left": "west", "right": "east"}
RENAMES = {
    "cocoa_stage_2": ["cocoa_stage2"], "trip_wire": ["tripwire"], "redstone_torch_on": ["redstone_torch"],
    "redstone_dust_line": ["redstone_dust_line0", "redstone_dust_line1"], "redstone_dust_cross": ["redstone_dust_dot"],
    "kelp_a": ["kelp_plant"], "kelp_top": ["kelp"], "weeping_vines_base": ["weeping_vines_plant"],
    "weeping_vines_bottom": ["weeping_vines"], "portal": ["nether_portal"], "chain1": ["iron_chain"],
    "firefly_bush_firefly": ["firefly_bush_emissive"], "eyeblossom_stem_blooming": [],
    "dried_kelp_side_b": [], "bamboo_leaf": ["bamboo_large_leaves"], "bamboo_small_leaf": ["bamboo_small_leaves"],
    "stonecutter2_bottom": ["stonecutter_bottom"], "stonecutter2_side": ["stonecutter_side"],
    "stonecutter2_top": ["stonecutter_top"], "stonecutter2_saw": ["stonecutter_saw"],
    "stonecutter_bottom": [], "stonecutter_side": [], "stonecutter_top": [], "stonecutter_other_side": [],
}


def name_candidates(b: str) -> list:
    """Java names a Bedrock texture name may correspond to when the pixels differ between editions."""
    n = b.split("/")[-1]
    if n in RENAMES:
        return RENAMES[n]
    out = [n]
    parts = n.split("_")
    if n.startswith("glass_pane_top_"):
        c = n[len("glass_pane_top_"):].replace("silver", "light_gray")
        out.append(f"{c}_stained_glass_pane_top")
    elif n.startswith("glass_"):
        out.append(f"{n[6:].replace('silver', 'light_gray')}_stained_glass")
    if n.startswith("door_") and parts[-1] in ("lower", "upper"):
        w = WOOD.get("_".join(parts[1:-1]))
        if w:
            out.append(f"{w}_door_{'bottom' if parts[-1] == 'lower' else 'top'}")
    if n.endswith("_door_lower"):
        out.append(n.replace("_door_lower", "_door_bottom"))
    if n.endswith(("_chain1",)):
        out.append(n[:-1])
    if n.startswith("dried_ghast_state_") and len(parts) == 5 and parts[4] in GHAST_FACE:
        state, face = int(parts[3]), parts[4]
        out.append(f"dried_ghast_hydration_{state - 1}_{GHAST_FACE[face]}")
    return out


def load_rgba(data: bytes) -> np.ndarray:
    return np.asarray(Image.open(io.BytesIO(data)).convert("RGBA"), dtype=np.int16)


def bedrock_colour_textures(pack: Path) -> dict:
    """name -> RGBA array, for colour textures referenced by texture sets (or any non-PBR texture)."""
    out = {}
    blocks = pack / "textures" / "blocks"
    # Layers named by texture sets are not colour textures (some colour textures end in _normal: piston_top_normal).
    pbr_layers = set()
    for ts in blocks.rglob("*.texture_set.json"):
        body = json.loads(ts.read_text(encoding="utf-8-sig")).get("minecraft:texture_set", {})
        for k, v in body.items():
            if k != "color" and isinstance(v, str):
                pbr_layers.add((ts.parent / v).relative_to(blocks).as_posix())
    for f in sorted(blocks.rglob("*")):
        if f.suffix.lower() not in (".png", ".tga") or f.name.endswith(".texture_set.json"):
            continue
        if f.relative_to(blocks).with_suffix("").as_posix() in pbr_layers:
            continue
        rel = f.relative_to(blocks).with_suffix("").as_posix()
        if rel in out and f.suffix.lower() == ".png":
            continue  # .tga wins over .png, like the game
        out[rel] = load_rgba(f.read_bytes())
    return out


def java_textures(jar: Path) -> dict:
    out = {}
    with zipfile.ZipFile(jar) as z:
        for n in z.namelist():
            if n.startswith("assets/minecraft/textures/block/") and n.endswith(".png"):
                out[n[len("assets/minecraft/textures/block/"):-4]] = load_rgba(z.read(n))
    return out


def key(a: np.ndarray) -> bytes:
    b = a.copy()
    b[b[..., 3] == 0] = 0  # ignore colour under fully transparent pixels
    return a.shape[:2].__repr__().encode() + b.astype(np.uint8).tobytes()


def mae(a: np.ndarray, b: np.ndarray) -> float:
    m = (a[..., 3] > 0) | (b[..., 3] > 0)
    if not m.any():
        return 0.0
    return float(np.abs(a - b)[m].mean())


def main():
    pack, jar, out = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
    bed = bedrock_colour_textures(pack)
    jav = java_textures(jar)
    by_key = {}
    for name, arr in bed.items():
        by_key.setdefault(key(arr), []).append(name)
    by_shape = {}
    for name, arr in bed.items():
        by_shape.setdefault(arr.shape, []).append(name)

    mapping, how = {}, {}
    for jname, jarr in sorted(jav.items()):
        hits = by_key.get(key(jarr))
        if hits:
            for b in hits:
                mapping.setdefault(b, []).append(jname)
            how[jname] = ("exact", hits)
            continue
        best, best_e = None, MAX_MAE
        for b in by_shape.get(jarr.shape, []):
            e = mae(jarr, bed[b])
            if e < best_e:
                best, best_e = b, e
        if best:
            mapping.setdefault(best, []).append(jname)
            how[jname] = ("near %.2f" % best_e, [best])
    for b, j in ALIASES.items():
        if b in bed and j in jav and j not in how:
            mapping.setdefault(b, []).append(j)
            how[j] = ("alias", [b])
    for b in sorted(bed):
        for j in name_candidates(b):
            if j in jav and j not in how:
                mapping.setdefault(b, []).append(j)
                how[j] = ("name", [b])
    for b, j in list(mapping.items()):
        mapping[b] = sorted(set(j))

    unmatched_java = sorted(set(jav) - set(how))
    unused_bedrock = sorted(set(bed) - set(mapping))
    out.write_text(json.dumps({"mapping": mapping, "unmatched_java": unmatched_java,
                               "unused_bedrock": unused_bedrock}, indent=1), encoding="utf-8")
    kinds = {}
    for k, _ in how.values():
        kinds[k.split()[0]] = kinds.get(k.split()[0], 0) + 1
    print(f"java textures: {len(jav)}, bedrock colour textures: {len(bed)}")
    print(f"matched java: {len(how)} {kinds}; unmatched java: {len(unmatched_java)}; unused bedrock: {len(unused_bedrock)}")


if __name__ == "__main__":
    main()
