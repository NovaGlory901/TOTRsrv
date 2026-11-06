#!/usr/bin/env python3
"""The developer's tool for server releases (run on the developer's own machine, not on a server).

    python tools/release_tool.py keygen --out ~/.totr-signing        make the signing key (asks for a passphrase) and print the public line
    python tools/release_tool.py build  --key ~/.totr-signing/signing.key --out dist-release [--notes "what changed"] [--notes-file FILE] [--app DIR]
                                                                  pack this folder into TOTRsrv-<version>.tar.gz, write release.json, sign it
                                                                  (--app: a built web app, TOTRspa `npm run publish:web`, carried as static/app)
    python tools/release_tool.py passwd --key ~/.totr-signing/signing.key                   change the key's passphrase (the key and its public line stay the same)
    python tools/release_tool.py sign-file --key ~/.totr-signing/signing.key latest.yml     sign a desktop app's update file (latest.yml / latest-linux.yml) -> latest.yml.sig
    python tools/release_tool.py verify --dir dist-release [--pubkey update_pubkey.txt]
                                                                  check a built release the way a server will

To publish: attach TOTRsrv-<version>.tar.gz, release.json and release.json.sig to a GitHub release
of the repository the servers watch (UPDATE_REPO). THE PRIVATE KEY STAYS ON YOUR MACHINE. Never put
signing.key in git, in a chat, or on a server. The public line goes in update_pubkey.txt and ships
with every server; whoever has the private key can make a server install anything, so keep a backup
somewhere safe (if it is lost, servers can no longer trust new releases until they are given a new
public key by hand). The version comes from version.py: raise it first. The bundle leaves out
everything that belongs to one machine (.env, data, reports, certs, downloads, the virtual
environment, tests), the same things the installer leaves out.
"""

import argparse
import base64
import getpass
import gzip
import io
import json
import os
import pathlib
import re
import shutil
import sys
import tarfile
from datetime import UTC, datetime

PF = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PF))
from updates import signing  # noqa: E402

SKIP_NAMES = {
    ".venv",
    "__pycache__",
    "tests",
    ".env",
    "data",
    "reports",
    "certs",
    "downloads",
    "node_modules",
    ".git",
    ".pytest_cache",
    "dist-release",
}


def current_version(root=PF) -> str:
    m = re.search(
        r'^SERVER_VERSION\s*=\s*"([^"]+)"',
        (pathlib.Path(root) / "version.py").read_text(encoding="utf-8"),
        re.M,
    )
    if not m:
        sys.exit("no SERVER_VERSION in version.py")
    return m.group(1)


def passphrase(confirm=False) -> bytes:
    p = os.environ.get("TOTR_SIGNING_PASSPHRASE") or getpass.getpass(
        "Passphrase for the signing key: "
    )
    if (
        confirm
        and not os.environ.get("TOTR_SIGNING_PASSPHRASE")
        and getpass.getpass("Again: ") != p
    ):
        sys.exit("The two passphrases did not match. Nothing was made.")
    if len(p) < 12:
        sys.exit("Use a passphrase of at least 12 characters.")
    return p.encode("utf-8")


def keygen(out: pathlib.Path):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519

    key_file = out / "signing.key"
    if key_file.exists():
        sys.exit(
            f"{key_file} already exists. Refusing to replace a signing key (servers trust the old one)."
        )
    out.mkdir(parents=True, exist_ok=True)
    out.chmod(0o700)
    key = ed25519.Ed25519PrivateKey.generate()
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.BestAvailableEncryption(passphrase(confirm=True)),
    )
    fd = os.open(key_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(pem)
    raw = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    line = f"ed25519 {base64.b64encode(raw).decode('ascii')} totr release key {datetime.now(UTC).date()}"
    (out / "update_pubkey.txt").write_text(line + "\n", encoding="utf-8")
    print(f"Made {key_file} (SECRET: keep it off servers and out of git; back it up).")
    print(
        "Put this line in TOTRsrv/update_pubkey.txt (also saved as update_pubkey.txt next to the key):"
    )
    print(line)


def load_key(path):
    from cryptography.hazmat.primitives import serialization

    try:
        return serialization.load_pem_private_key(
            pathlib.Path(path).read_bytes(), password=passphrase()
        )
    except (ValueError, TypeError):
        sys.exit("That is not the key's passphrase, or the file is not a signing key.")


def change_passphrase(key_path: pathlib.Path):
    """Re-encrypts the same signing key under a new passphrase. The key itself (and so
    update_pubkey.txt, everywhere) does not change. The old file is kept as signing.key.old until
    the new one has been read back with the new passphrase, then removed."""
    from cryptography.hazmat.primitives import serialization

    old_pw = os.environ.get("TOTR_SIGNING_PASSPHRASE") or getpass.getpass("Current passphrase: ")
    try:
        key = serialization.load_pem_private_key(
            key_path.read_bytes(), password=old_pw.encode("utf-8")
        )
    except (ValueError, TypeError):
        sys.exit("That is not the key's passphrase, or the file is not a signing key.")
    new_pw = os.environ.get("TOTR_NEW_SIGNING_PASSPHRASE") or getpass.getpass(
        "New passphrase (12 or more characters): "
    )
    if not os.environ.get("TOTR_NEW_SIGNING_PASSPHRASE") and getpass.getpass("Again: ") != new_pw:
        sys.exit("The two passphrases did not match. Nothing was changed.")
    if len(new_pw) < 12:
        sys.exit("Use a passphrase of at least 12 characters. Nothing was changed.")
    if new_pw == old_pw:
        sys.exit("That is the same passphrase. Nothing was changed.")
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.BestAvailableEncryption(new_pw.encode("utf-8")),
    )
    backup, tmp = (
        key_path.with_name(key_path.name + ".old"),
        key_path.with_name(key_path.name + ".new"),
    )
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(pem)
    try:
        back = serialization.load_pem_private_key(tmp.read_bytes(), password=new_pw.encode("utf-8"))
        same = (
            back.private_bytes_raw() == key.private_bytes_raw()
            if hasattr(back, "private_bytes_raw")
            else True
        )
    except (ValueError, TypeError):
        same = False
    if not same:
        tmp.unlink(missing_ok=True)
        sys.exit("The re-encrypted key could not be read back. Nothing was changed.")
    shutil.copy2(key_path, backup)
    os.replace(tmp, key_path)
    backup.unlink()
    print(
        f"The passphrase of {key_path} is changed. The key itself is the same, so no public key file needs to change."
    )


def files_to_pack(root=PF) -> list:
    root = pathlib.Path(root)
    out = []
    for dirpath, dirs, names in os.walk(root):
        rel_dir = pathlib.Path(dirpath).relative_to(root)
        dirs[:] = sorted(
            d
            for d in dirs
            if d not in SKIP_NAMES and not (rel_dir == pathlib.Path("static") and d == "downloads")
        )
        for n in sorted(names):
            if n in SKIP_NAMES or n.endswith(".pyc") or n == ".env":
                continue
            out.append((rel_dir / n).as_posix())
    return out


APP_DIR_IN_BUNDLE = "static/app"


def bundle_entries(root=PF, app=None) -> list:
    """(name in the bundle, file on disk) for everything a release holds, sorted by name. With `app`
    (a built web app: its index.html and version.json are required), that folder becomes static/app
    and replaces whatever the server folder has there."""
    root = pathlib.Path(root)
    entries = {rel: root / rel for rel in files_to_pack(root)}
    if app is not None:
        app = pathlib.Path(app)
        if not (app / "index.html").is_file() or not (app / "version.json").is_file():
            sys.exit(
                f"{app} is not a built web app (no index.html and version.json): build it with `npm run publish:web` in TOTRspa"
            )
        entries = {
            rel: src for rel, src in entries.items() if not rel.startswith(APP_DIR_IN_BUNDLE + "/")
        }
        for dirpath, dirs, names in os.walk(app):
            dirs.sort()
            for n in sorted(names):
                src = pathlib.Path(dirpath) / n
                entries[
                    (
                        pathlib.PurePosixPath(APP_DIR_IN_BUNDLE) / src.relative_to(app).as_posix()
                    ).as_posix()
                ] = src
    return sorted(entries.items())


def build_bundle(path: pathlib.Path, root=PF, app=None):
    """A reproducible .tar.gz: sorted names, no owners, no timestamps."""
    raw = io.BytesIO()
    with (
        gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as gz,
        tarfile.open(fileobj=gz, mode="w", format=tarfile.PAX_FORMAT) as tar,
    ):
        for rel, src in bundle_entries(root, app):
            info = tarfile.TarInfo(rel)
            info.size = src.stat().st_size
            info.mode = 0o755 if os.access(src, os.X_OK) and src.stat().st_mode & 0o111 else 0o644
            info.mtime = 0
            info.uname = info.gname = ""
            with open(src, "rb") as f:
                tar.addfile(info, f)
    path.write_bytes(raw.getvalue())


def make_release(root, out: pathlib.Path, key, notes: str = "", app=None) -> dict:
    """Packs the server folder `root`, writes release.json and its signature (made with `key`, a
    cryptography Ed25519PrivateKey) into `out`. Returns the manifest."""
    root, version = pathlib.Path(root), current_version(root)
    out.mkdir(parents=True, exist_ok=True)
    name = f"TOTRsrv-{version}.tar.gz"
    bundle = out / name
    build_bundle(bundle, root, app)
    manifest = {
        "format": signing.FORMAT,
        "format_version": signing.FORMAT_VERSION,
        "product": signing.PRODUCT,
        "release": version,
        "created": datetime.now(UTC).isoformat(timespec="seconds"),
        "bundle": {
            "name": name,
            "sha256": signing.sha256_file(bundle),
            "size": bundle.stat().st_size,
        },
        "notes": notes,
    }
    data = (json.dumps(manifest, indent=1, sort_keys=True) + "\n").encode("utf-8")
    signing.parse_manifest(data)  # refuse to sign something a server would refuse
    (out / "release.json").write_bytes(data)
    (out / "release.json.sig").write_text(signing.sign(data, key) + "\n", encoding="ascii")
    return manifest


def build(key_path, out: pathlib.Path, notes: str, app=None):
    manifest = make_release(PF, out, load_key(key_path), notes, app)
    bundle = out / manifest["bundle"]["name"]
    print(
        f"Built {bundle} ({bundle.stat().st_size // 1024} KB), release.json and release.json.sig for {manifest['release']} in {out}"
    )
    print("Check it:  python tools/release_tool.py verify --dir " + str(out))


def verify(directory: pathlib.Path, pubkey_file):
    key = signing.load_pubkey(pubkey_file)
    if key is None:
        sys.exit(f"{pubkey_file} holds no public key")
    data = (directory / "release.json").read_bytes()
    manifest = signing.verify_release(data, (directory / "release.json.sig").read_text(), key)
    signing.verify_bundle(directory / manifest["bundle"]["name"], manifest)
    print(
        f"OK: release {manifest['release']} is signed by the key in {pubkey_file} and the bundle matches."
    )


# the desktop apps check this prefix too (TOTRspa/shared/electron/appsig.cjs): a server release
# signature is never a valid app one
APP_SIGNATURE_PREFIX = b"totr-app-update\n"


def sign_file(key_path, file: pathlib.Path):
    """Signs the exact bytes of an app update file (latest.yml / latest-linux.yml, which pins the
    installer's SHA-512) -> FILE.sig, for the desktop apps."""
    data = file.read_bytes()
    if not data or len(data) > 64 * 1024:
        sys.exit(f"{file} is empty or too big to be an update file")
    sig = file.with_name(file.name + ".sig")
    sig.write_text(
        signing.sign(APP_SIGNATURE_PREFIX + data, load_key(key_path)) + "\n", encoding="ascii"
    )
    print(f"Signed {file.name} -> {sig.name}. Upload both (deploy.cjs does).")


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    k = sub.add_parser("keygen")
    k.add_argument("--out", required=True, type=pathlib.Path)
    b = sub.add_parser("build")
    b.add_argument("--key", required=True)
    b.add_argument("--out", required=True, type=pathlib.Path)
    b.add_argument("--notes", default="")
    b.add_argument("--notes-file", type=pathlib.Path)
    b.add_argument(
        "--app",
        type=pathlib.Path,
        help="a built web app (TOTRspa: npm run publish:web) to carry as static/app in the release",
    )
    v = sub.add_parser("verify")
    v.add_argument("--dir", required=True, type=pathlib.Path)
    v.add_argument("--pubkey", default=str(PF / "update_pubkey.txt"))
    pw = sub.add_parser("passwd")
    pw.add_argument("--key", required=True)
    f = sub.add_parser("sign-file")
    f.add_argument("--key", required=True)
    f.add_argument("file", type=pathlib.Path)
    a = ap.parse_args(argv)
    try:
        if a.cmd == "keygen":
            keygen(a.out.expanduser())
        elif a.cmd == "build":
            notes = a.notes_file.read_text(encoding="utf-8").strip() if a.notes_file else a.notes
            build(
                os.path.expanduser(a.key),
                a.out.expanduser(),
                notes,
                a.app.expanduser() if a.app else None,
            )
        elif a.cmd == "passwd":
            change_passphrase(pathlib.Path(os.path.expanduser(a.key)))
        elif a.cmd == "sign-file":
            sign_file(os.path.expanduser(a.key), a.file.expanduser())
        else:
            verify(a.dir.expanduser(), a.pubkey)
    except signing.UpdateError as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main()
