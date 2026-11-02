#!/usr/bin/env python3
"""Extract the four files the sunstone root build needs out of an iPhone X IPSW.

  ipsw-extract.py IPSW OUTDIR [--device d22ap] [--list]

Writes OUTDIR/paths.env with OS=, SYSOS=, APPOS=, ADT= so a shell can source it.
The three DMGs are read-only APFS images inside the zip; the DeviceTree is an
IM4P that is decompressed to the .adt that tools/mkadt.py reads for the product
properties (chip id, board id, model).

An IPSW carries several BuildIdentities (upgrade, erase, ...). The right one is
the identity whose DeviceTree names this board and whose manifest has all four
entries; --list prints every identity so a mismatch is obvious.

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


def describe(manifest, device):
    rows = []
    for index, ident in enumerate(manifest.get("BuildIdentities") or []):
        entries = ident.get("Manifest", {})
        info = ident.get("Info", {})
        present = [role for role in ROLES if ((entries.get(role) or {}).get("Info") or {}).get("Path")]
        tree = ((entries.get("DeviceTree") or {}).get("Info") or {}).get("Path", "")
        rows.append(
            {
                "index": index,
                "product": info.get("ProductType"),
                "class": info.get("DeviceClass"),
                "build": info.get("BuildNumber"),
                "roles": present,
                "tree": tree,
                "match": device in tree.lower(),
            }
        )
    return rows


def report(rows):
    print("identities:", file=sys.stderr)
    for row in rows:
        print(
            f"  [{row['index']:2}] product={row['product']} class={row['class']} build={row['build']} "
            f"match={row['match']} roles={','.join(row['roles']) or '-'} tree={row['tree']}",
            file=sys.stderr,
        )


def pick(rows):
    complete = [r for r in rows if set(ROLES) <= set(r["roles"])]
    for pool in ([r for r in complete if r["match"]], complete):
        if pool:
            return pool[0]
    best = max(rows, key=lambda r: (len(r["roles"]), r["match"]), default=None)
    if best is None:
        sys.exit("BuildManifest.plist has no BuildIdentities")
    missing = sorted(set(ROLES) - set(best["roles"]))
    sys.exit(f"no identity has all of {sorted(ROLES)}; closest is [{best['index']}] missing {missing}")


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
        subprocess.run(["ipsw", "img4", "im4p", "extract", str(tmp), "-o", str(out)], check=True)
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
    ap.add_argument("--list", action="store_true", help="only print the identities")
    args = ap.parse_args()

    args.outdir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.ipsw) as zf:
        manifest = plistlib.loads(zf.read("BuildManifest.plist"))
        rows = describe(manifest, args.device)
        report(rows)
        if args.list:
            return
        chosen = pick(rows)
        ident = manifest["BuildIdentities"][chosen["index"]]
        entries = ident.get("Manifest", {})
        print(f"using identity [{chosen['index']}] {chosen['product']} {chosen['build']}", file=sys.stderr)
        values = {}
        for role, key in ROLES.items():
            info = (entries.get(role) or {}).get("Info") or {}
            path = info.get("Path")
            if not path:
                sys.exit(f"identity [{chosen['index']}] has no {role} entry")
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
