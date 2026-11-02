#!/usr/bin/env python3
"""Extract the four files the sunstone root build needs out of an iPhone X IPSW.

  ipsw-extract.py IPSW OUTDIR [--device d22ap] [--list]

Writes OUTDIR/paths.env with OS=, SYSOS=, APPOS=, ADT= so a shell can source it.
The DMGs are APFS images inside the zip; the DeviceTree is an IM4P that is
decompressed to the .adt that tools/mkadt.py reads for the product properties
(chip id, board id, model).

An IPSW carries several BuildIdentities (one per board and restore variant), and
on iOS 16 the cryptexes are not always listed per identity: the 20H392 iPhone X
manifest exposes OS and DeviceTree only, while the SystemOS and AppOS DMGs sit in
the archive as separate members (Cryptex1,SystemOS / Cryptex1,AppOS). The lookup
therefore tries the manifest key, its Cryptex1 alias, and finally identifies the
remaining DMGs by size (AppOS is the small one, SystemOS the middle one).

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

ROLES = ("OS", "SystemOS", "AppOS", "DeviceTree")
ALIASES = {
    "OS": ("os",),
    "SystemOS": ("systemos", "cryptex1systemos", "cryptex1,systemos"),
    "AppOS": ("appos", "cryptex1appos", "cryptex1,appos"),
    "DeviceTree": ("devicetree",),
}


def normalise(key):
    return key.replace(" ", "").lower()


def entry_path(entries, role):
    for key, value in entries.items():
        name = normalise(key)
        if any(name == alias for alias in ALIASES[role]):
            path = ((value or {}).get("Info") or {}).get("Path")
            if path:
                return path
    return None


def describe(manifest, device):
    rows = []
    for index, ident in enumerate(manifest.get("BuildIdentities") or []):
        entries = ident.get("Manifest", {})
        info = ident.get("Info", {})
        present = [role for role in ROLES if entry_path(entries, role)]
        tree = entry_path(entries, "DeviceTree") or ""
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


def report(rows, manifest):
    print("identities:", file=sys.stderr)
    for row in rows:
        print(
            f"  [{row['index']:2}] product={row['product']} class={row['class']} build={row['build']} "
            f"match={row['match']} roles={','.join(row['roles']) or '-'} tree={row['tree']}",
            file=sys.stderr,
        )
    if rows:
        keys = sorted(manifest["BuildIdentities"][rows[0]["index"]].get("Manifest", {}))
        print("manifest keys of identity 0: " + ", ".join(keys), file=sys.stderr)


def pick(rows):
    scored = [r for r in rows if "OS" in r["roles"] and r["match"]]
    scored.sort(key=lambda r: len(r["roles"]), reverse=True)
    if scored:
        return scored[0]
    fallback = sorted(rows, key=lambda r: len(r["roles"]), reverse=True)
    if not fallback:
        sys.exit("BuildManifest.plist has no BuildIdentities")
    print("no identity names this board in its DeviceTree; using the most complete one", file=sys.stderr)
    return fallback[0]


def dmg_by_size(zf, exclude):
    """Identify the cryptex DMGs by size when the manifest does not list them.

    iPhone X 20H392: OS 4.7 GB, SystemOS 2.7 GB, AppOS 10 MB. Ranking the
    remaining members by size gives the two cryptexes.
    """
    members = [
        info
        for info in zf.infolist()
        if info.filename.endswith(".dmg") and info.filename not in exclude
    ]
    members.sort(key=lambda info: info.file_size, reverse=True)
    return members


def member(zf, path):
    try:
        return zf.getinfo(path)
    except KeyError:
        sys.exit(f"IPSW does not contain {path}")


def _as_bytes(value, depth=0):
    """Pull bytes out of whatever a library hands back (bytes, memoryview, wrapper)."""
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value)
    if value is None or depth > 3:
        return None
    for attr in ("output", "data", "payload", "value", "_data"):
        item = getattr(value, attr, None)
        if callable(item):
            try:
                item = item()
            except TypeError:
                continue
        found = _as_bytes(item, depth + 1)
        if found:
            return found
    return None


def decompress_im4p(data):
    # The blacktop CLI is what the port's own notes use; it always returns the
    # decompressed payload, so prefer it and keep pyimg4 as the fallback.
    if shutil.which("ipsw"):
        tmp = Path(".im4p.tmp")
        tmp.write_bytes(data)
        out = tmp.with_suffix(".out")
        subprocess.run(["ipsw", "img4", "im4p", "extract", str(tmp), "-o", str(out)], check=True)
        result = out.read_bytes()
        tmp.unlink(missing_ok=True)
        out.unlink(missing_ok=True)
        return result
    try:
        from pyimg4 import IM4P  # type: ignore
    except ImportError:
        IM4P = None
    if IM4P is not None:
        try:
            result = _as_bytes(IM4P(data).payload)
        except Exception as exc:  # noqa: BLE001
            sys.exit(f"pyimg4 could not read the DeviceTree payload: {exc}")
        if result:
            return result
        sys.exit("pyimg4 returned no payload bytes: install the ipsw CLI instead")
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
        report(rows, manifest)
        if args.list:
            return
        chosen = pick(rows)
        entries = manifest["BuildIdentities"][chosen["index"]].get("Manifest", {})
        print(f"using identity [{chosen['index']}] {chosen['product']} {chosen['build']}", file=sys.stderr)

        paths = {role: entry_path(entries, role) for role in ROLES}
        missing = [role for role in ("SystemOS", "AppOS") if not paths[role]]
        if missing:
            used = {p for p in paths.values() if p}
            spare = dmg_by_size(zf, used)
            print(
                "manifest does not name "
                + ", ".join(missing)
                + "; candidates by size: "
                + ", ".join(f"{i.filename} ({i.file_size})" for i in spare),
                file=sys.stderr,
            )
            if len(spare) >= 2:
                # largest spare is SystemOS, the smallest is AppOS
                if not paths["SystemOS"]:
                    paths["SystemOS"] = spare[0].filename
                if not paths["AppOS"]:
                    paths["AppOS"] = spare[-1].filename
        for role in ROLES:
            if not paths[role]:
                sys.exit(f"could not locate the {role} input in this IPSW")

        values = {}
        for role in ROLES:
            path = paths[role]
            data = zf.read(member(zf, path))
            if role == "DeviceTree":
                target = args.outdir / f"{args.device}.adt"
                target.write_bytes(decompress_im4p(data))
            else:
                target = args.outdir / Path(path).name
                target.write_bytes(data)
            values[{"OS": "OS", "SystemOS": "SYSOS", "AppOS": "APPOS", "DeviceTree": "ADT"}[role]] = target.resolve()
            print(f"{role:11} {path} -> {target.name} ({target.stat().st_size} bytes)", file=sys.stderr)

    env = args.outdir / "paths.env"
    env.write_text("".join(f"{k}={v}\n" for k, v in values.items()))
    print(env)


if __name__ == "__main__":
    main()
