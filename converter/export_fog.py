"""Exports Vanilla RTX's per-biome volumetric fog (Bedrock RTX) into a table keyed by Java biome id.

Usage: python -P export_fog.py <VanillaRTX dir> <out.json>
Each entry: air [density, maxHeight, zeroHeight] (heights null = uniform), weather [...] likewise,
scattering/absorption per channel (per block at density 1) for air, water's per-block coefficients, and
waterFogColour: the sRGB colour of the biome's underwater distance fog ("distance" > "water" > "fog_color"), 0..1.
"""
import glob, json, os, sys

# Bedrock biome identifiers that differ from Java's.
RENAMES = {
    "hell": ["nether_wastes"], "soulsand_valley": ["soul_sand_valley"],
    "the_end": ["the_end", "end_highlands", "end_midlands", "small_end_islands", "end_barrens"],
    "roofed_forest": ["dark_forest"], "mega_taiga": ["old_growth_pine_taiga"],
    "redwood_taiga_mutated": ["old_growth_spruce_taiga"], "cold_taiga": ["snowy_taiga"],
    "ice_plains": ["snowy_plains"], "ice_plains_spikes": ["ice_spikes"], "extreme_hills": ["windswept_hills"],
    "extreme_hills_plus_trees": ["windswept_forest"], "extreme_hills_mutated": ["windswept_gravelly_hills"],
    "savanna_mutated": ["windswept_savanna"], "mesa": ["badlands"], "mesa_plateau_stone": ["wooded_badlands"],
    "mesa_bryce": ["eroded_badlands"], "jungle_edge": ["sparse_jungle"],
    "birch_forest_mutated": ["old_growth_birch_forest"], "swampland": ["swamp"], "stone_beach": ["stony_shore"],
    "cold_beach": ["snowy_beach"], "mushroom_island": ["mushroom_fields"],
}

def colour(v):
    if isinstance(v, str):
        h = v.lstrip("#")
        return [int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4)]
    return [float(x) for x in v]

def profile(d):
    if d is None: return None
    uniform = d.get("uniform", False)
    return [d["max_density"], None if uniform else d.get("max_density_height"),
            None if uniform else d.get("zero_density_height")]

def main(pack, out):
    table = {}
    for path in sorted(glob.glob(os.path.join(pack, "fogs", "*_volumetric_fog_setting.json"))):
        name = os.path.basename(path).replace("_volumetric_fog_setting.json", "")
        settings = json.load(open(path, encoding="utf-8"))["minecraft:fog_settings"]
        v = settings.get("volumetric")
        water_fog = settings.get("distance", {}).get("water", {}).get("fog_color", "#44AFF5")
        if not v: continue
        media = v.get("media_coefficients", {})
        air, water = media.get("air", {}), media.get("water", {})
        entry = {
            "air": profile(v["density"].get("air")),
            "weather": profile(v["density"].get("weather")),
            "scattering": colour(air.get("scattering", [0, 0, 0])),
            "absorption": colour(air.get("absorption", [0, 0, 0])),
            "waterScattering": colour(water.get("scattering", [0, 0, 0])),
            "waterAbsorption": colour(water.get("absorption", [0, 0, 0])),
            "waterFogColour": colour(water_fog),
        }
        for java in RENAMES.get(name, [name]):
            table.setdefault(java, entry)
    json.dump(table, open(out, "w", encoding="utf-8"), indent=1, sort_keys=True)
    print(len(table), "biomes")

if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
