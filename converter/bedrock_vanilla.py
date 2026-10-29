"""Unpacks the built-in Bedrock vanilla resource packs (PBR texture sets of Vibrant Visuals, used by RTX for
everything a RTX pack does not override: mobs, block entities, items, new blocks) into one folder.

  python -P bedrock_vanilla.py [<Bedrock data/resource_packs dir>] [-o ../converted/_bedrock_vanilla]

Packs are layered like the game stacks them: vanilla, then vanilla_<version> in version order, later wins.
Their files live in __brarchive/<dir>.brarchive containers: header (u64 magic, u32 count, u32 version),
count x 256-byte entries (u8 name length, name, ..., u32 offset at 248, u32 size at 252), then raw file data.
An archive <dir>.brarchive holds the files directly inside <dir>/.
"""
import argparse
import re
import shutil
import struct
from pathlib import Path

DEFAULT_PACKS = Path("C:/XboxGames/Minecraft for Windows/Content/data/resource_packs")
MAGIC = 0x267052A0B125277D


def read_brarchive(path: Path):
    d = path.read_bytes()
    magic, count, _ = struct.unpack_from("<QII", d, 0)
    if magic != MAGIC:
        raise ValueError(f"{path}: not a brarchive")
    base = 16 + count * 256
    for i in range(count):
        o = 16 + i * 256
        name = d[o + 1:o + 1 + d[o]].decode("utf-8")
        off, size = struct.unpack_from("<II", d, o + 248)
        yield name, d[base + off:base + off + size]


def pack_order(packs: Path):
    def key(p: Path):
        m = re.fullmatch(r"vanilla_(\d+(?:\.\d+)*)", p.name)
        return tuple(int(x) for x in m.group(1).split(".")) if m else ()
    layered = [p for p in packs.iterdir() if p.name == "vanilla" or re.fullmatch(r"vanilla_\d+(\.\d+)*", p.name)]
    return sorted(layered, key=key)


def unpack(packs: Path, out: Path):
    if out.exists():
        shutil.rmtree(out)
    files = 0
    for pack in pack_order(packs):
        arch = pack / "__brarchive"
        if not arch.is_dir():
            continue
        for a in sorted(arch.rglob("*.brarchive")):
            rel_dir = a.relative_to(arch).with_suffix("")
            if rel_dir.parts[0] != "textures":
                continue
            for name, data in read_brarchive(a):
                dst = out / rel_dir / name
                dst.parent.mkdir(parents=True, exist_ok=True)
                # a .tga and a .png of the same texture: the newer pack's file must win, so drop the other one
                for ext in (".png", ".tga"):
                    other = dst.with_suffix(ext)
                    if other != dst and other.exists() and dst.suffix in (".png", ".tga"):
                        other.unlink()
                dst.write_bytes(data)
                files += 1
    return files


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("packs", type=Path, nargs="?", default=DEFAULT_PACKS)
    ap.add_argument("-o", "--out", type=Path, default=Path(__file__).parent.parent / "converted" / "_bedrock_vanilla")
    args = ap.parse_args()
    n = unpack(args.packs, args.out)
    sets = list(args.out.rglob("*.texture_set.json"))
    print(f"{args.out}: {n} files written, {len(sets)} texture sets")


if __name__ == "__main__":
    main()
