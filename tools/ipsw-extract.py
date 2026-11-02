#!/usr/bin/env python3
"""Extract the four files the sunstone root build needs out of an iPhone X IPSW.

  ipsw-extract.py IPSW OUTDIR [--device d22ap]

Writes OUTDIR/paths.env with OS=, SYSOS=, APPOS=, ADT= so a shell can source it.
The three DMGs are read-only APFS images inside the zip; the DeviceTree is an
IM4P that is decompressed to the .adt that tools/mkadt.py reads for the product
properties (chip id, board id, model).

pyimg4 is used when importable, otherwise the blacktop `ipsw` CLI:
    pip install pyimg4        # or: ipsw img4 im4p extract ...
"""
import argparse
import plistlib
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROLES = {"OS": "OS", "SystemOS": "SYSOS", "AppOS": "APPOS", "DeviceTree": "ADT"}


def pick_identity(manifest, device):
    identities = manifest.get("BuildIdentities") or []
    if not identities:
        sys.exit("BuildManifest.plist has no BuildIdentities")
    for ident in identities:
        tree = ident.get("Manifest", {}).get("DeviceTree", {})
        path = (tree.get("Info") or {}).get("Path", "")
        if device in path.lower():
            return ident
    return identities[0]


def member(zf, path):
    try:
        return zf.getinfo(path)
    except KeyError:
        sys.exit(f"IPSW does not contain {path}")


def decompress_im4p(data):
    try:
        from pyimg4 import IM4P  # type: ignore
    except ImportError:
        IM4P = None
    if IM4P is not None:
        try:
            return IM4P(data).payload.output()
        except Exception as exc:  # noqa: BLE001 - fall through to the CLI
            print(f"pyimg4 failed ({exc}); trying the ipsw CLI", file=sys.stderr)
    if shutil.which("ipsw"):
        tmp = Path(".im4p.tmp")
        tmp.write_bytes(data)
        out = tmp.with_suffix(".out")
        subprocess.run(
            ["ipsw", "img4", "im4p", "extract", str(tmp), "-o", str(out)],
            check=True,
        )
        result = out.read_bytes()
        tmp.unlink(missing_ok=True)
        out.unlink(missing_ok=True)
        return result
    sys.exit("no IM4P decompressor: pip install pyimg4 (or install the ipsw CLI)")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("ipsw", type=Path)
    ap.add_argument("outdir", type=Path)
    ap.add_argument("--device", default="d22ap")
    args = ap.parse_args()

    args.outdir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.ipsw) as zf:
        manifest = plistlib.loads(zf.read("BuildManifest.plist"))
        ident = pick_identity(manifest, args.device)
        entries = ident.get("Manifest", {})
        product = ident.get("Info", {})
        print(
            f"identity: {ident.get('ApBoardID')} product={product.get('ProductType')} "
            f"build={product.get('BuildNumber')}",
            file=sys.stderr,
        )
        values = {}
        for role, key in ROLES.items():
            info = (entries.get(role) or {}).get("Info") or {}
            path = info.get("Path")
            if not path:
                sys.exit(f"identity has no {role} entry")
            data = zf.read(member(zf, path))
            if role == "DeviceTree":
                target = args.outdir / f"{args.device}.adt"
                target.write_bytes(decompress_im4p(data))
            else:
                target = args.outdir / Path(path).name
                target.write_bytes(data)
            values[key] = target.resolve()
            print(f"{role:11} {path} -> {target.name} ({target.stat().st_size} bytes)", file=sys.stderr)

    env = args.outdir / "paths.env"
    env.write_text("".join(f"{k}={v}\n" for k, v in values.items()))
    print(env)


if __name__ == "__main__":
    main()
