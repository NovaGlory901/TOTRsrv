"""
TOTR's own certificate authority, for HTTPS on the home network (no purchase, no outside service).

    python tools/make_certs.py ca                       once: makes certs/totr-ca.key (asks for a passphrase) and certs/totr-ca.pem
    python tools/make_certs.py server --ip 192.0.2.10  issues certs/server.key + certs/server.pem for the server (asks for the passphrase)
    python tools/make_certs.py check                    shows what is in certs/ and when it runs out

What matters:
  * certs/totr-ca.key is the secret. Keep it on a dev machine (and backed up), never on the server,
    never in git (certs/ is git-ignored). Anyone with it
    and the passphrase could make a certificate that devices trusting TOTR's CA would accept.
  * The CA is NAME-CONSTRAINED: it can only vouch for private network addresses (10.x, 192.168.x,
    172.16-31.x) and names ending in .local or
    .lan. If the key ever leaked, it still could not be used to impersonate a real website.
  * certs/totr-ca.pem is public. The apps carry a copy (TOTRspa/shared/tools/sync-ca.cjs); other
    devices and browsers can import it to stop warning.
  * Only server.key and server.pem go to the server (certs/ there; see docs/setup.md). The server
    certificate lasts 825 days: run `server` again
    before it runs out and copy the new pair over.
Uses the `openssl` command; the passphrase is handed to it through the environment, never on a
command line.
"""

import argparse
import getpass
import ipaddress
import os
import pathlib
import subprocess
import sys
import tempfile

CERTS = pathlib.Path(
    os.environ.get("TOTR_CERTS_DIR") or pathlib.Path(__file__).resolve().parent.parent / "certs"
)
CA_KEY, CA_PEM = CERTS / "totr-ca.key", CERTS / "totr-ca.pem"
SERVER_KEY, SERVER_PEM = CERTS / "server.key", CERTS / "server.pem"
CA_DAYS, SERVER_DAYS = 3650, 825

CA_CONF = """[req]
distinguished_name = dn
prompt = no
x509_extensions = v3_ca
[dn]
CN = TOTR local CA
O = TOTR (private, for the home network)
[v3_ca]
basicConstraints = critical, CA:TRUE, pathlen:0
keyUsage = critical, keyCertSign, cRLSign
subjectKeyIdentifier = hash
nameConstraints = critical, @names
[names]
permitted;IP.0 = 10.0.0.0/255.0.0.0
permitted;IP.1 = 192.168.0.0/255.255.0.0
permitted;IP.2 = 172.16.0.0/255.240.0.0
permitted;DNS.0 = .local
permitted;DNS.1 = .lan
"""

SERVER_CONF = """[v3_server]
basicConstraints = critical, CA:FALSE
keyUsage = critical, digitalSignature
extendedKeyUsage = serverAuth
subjectKeyIdentifier = hash
authorityKeyIdentifier = keyid
subjectAltName = @alt
[alt]
{alt}
"""


def openssl(*args, passphrase=None, stdin=None):
    env = dict(os.environ)
    if passphrase is not None:
        env["TOTR_CA_PASS"] = passphrase
    r = subprocess.run(["openssl", *args], env=env, input=stdin, capture_output=True, text=True)
    if r.returncode != 0:
        if "bad decrypt" in r.stderr or "pkcs12 cipherfinal" in r.stderr:
            sys.exit("That is not the CA passphrase.")
        sys.exit(f"openssl {args[0]} failed:\n{r.stderr.strip()}")
    return r.stdout


def private_file(path: pathlib.Path):
    path.chmod(0o600)


def make_ca(passphrase: str):
    CERTS.mkdir(parents=True, exist_ok=True)
    if CA_KEY.exists() or CA_PEM.exists():
        sys.exit(
            f"{CA_KEY} or {CA_PEM} already exists. Refusing to replace a certificate authority (every device would have to trust the new one). "
            "Delete the files yourself if you really want a new one."
        )
    with tempfile.TemporaryDirectory() as tmp:
        conf = pathlib.Path(tmp) / "ca.cnf"
        conf.write_text(CA_CONF)
        openssl(
            "genpkey",
            "-algorithm",
            "EC",
            "-pkeyopt",
            "ec_paramgen_curve:P-256",
            "-aes256",
            "-pass",
            "env:TOTR_CA_PASS",
            "-out",
            str(CA_KEY),
            passphrase=passphrase,
        )
        private_file(CA_KEY)
        openssl(
            "req",
            "-new",
            "-x509",
            "-key",
            str(CA_KEY),
            "-passin",
            "env:TOTR_CA_PASS",
            "-days",
            str(CA_DAYS),
            "-config",
            str(conf),
            "-out",
            str(CA_PEM),
            passphrase=passphrase,
        )
    print(
        f"Made {CA_PEM} (public: the apps and other devices trust this) and {CA_KEY} (SECRET: keep it off the server, back it up, never commit it)."
    )


def make_server(passphrase: str, ips, names):
    if not CA_KEY.exists() or not CA_PEM.exists():
        sys.exit("No certificate authority yet: run `python tools/make_certs.py ca` first.")
    alt = [f"IP.{i} = {ipaddress.ip_address(ip)}" for i, ip in enumerate(ips)] + [
        f"DNS.{i} = {n}" for i, n in enumerate(names)
    ]
    if not alt:
        sys.exit(
            "Give the server at least one address: --ip 192.0.2.10(and optionally --name server.local)."
        )
    CERTS.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        t = pathlib.Path(tmp)
        (t / "server.cnf").write_text(SERVER_CONF.format(alt="\n".join(alt)))
        openssl(
            "genpkey",
            "-algorithm",
            "EC",
            "-pkeyopt",
            "ec_paramgen_curve:P-256",
            "-out",
            str(SERVER_KEY),
        )
        private_file(SERVER_KEY)
        openssl(
            "req",
            "-new",
            "-key",
            str(SERVER_KEY),
            "-subj",
            "/CN=TOTR server",
            "-out",
            str(t / "server.csr"),
        )
        openssl(
            "x509",
            "-req",
            "-in",
            str(t / "server.csr"),
            "-CA",
            str(CA_PEM),
            "-CAkey",
            str(CA_KEY),
            "-passin",
            "env:TOTR_CA_PASS",
            "-CAcreateserial",
            "-CAserial",
            str(t / "ca.srl"),
            "-days",
            str(SERVER_DAYS),
            "-extfile",
            str(t / "server.cnf"),
            "-extensions",
            "v3_server",
            "-out",
            str(SERVER_PEM),
            passphrase=passphrase,
        )
    try:
        verify(ips[0] if ips else None)
    except SystemExit:
        # a certificate the CA's own rules reject is never left lying around
        for f in (
            SERVER_PEM,
            SERVER_KEY,
        ):
            f.unlink(missing_ok=True)
        raise
    print(
        f"Made {SERVER_PEM} and {SERVER_KEY} (valid {SERVER_DAYS} days). Copy these two (only these) to the server."
    )


def verify(ip=None):
    args = ["verify", "-CAfile", str(CA_PEM), "-purpose", "sslserver"]
    if ip:
        args += ["-verify_ip", str(ip)]
    out = openssl(*args, str(SERVER_PEM)).strip()
    if "OK" not in out:
        sys.exit("The new certificate does not verify against the CA: " + out)


def check():
    for label, path in (("CA certificate", CA_PEM), ("server certificate", SERVER_PEM)):
        if not path.exists():
            print(f"{label}: missing ({path})")
            continue
        out = openssl(
            "x509", "-in", str(path), "-noout", "-subject", "-enddate", "-ext", "subjectAltName"
        )
        print(
            f"{label} ({path}):\n  "
            + "\n  ".join(line.strip() for line in out.strip().splitlines() if line.strip())
        )
    print("CA key:", "present" if CA_KEY.exists() else "missing", f"({CA_KEY})")


def ask(prompt_again=False):
    p = os.environ.get("TOTR_CA_PASSPHRASE") or getpass.getpass("CA passphrase: ")
    if (
        prompt_again
        and not os.environ.get("TOTR_CA_PASSPHRASE")
        and getpass.getpass("Again: ") != p
    ):
        sys.exit("The two passphrases did not match. Nothing was made.")
    if len(p) < 12:
        sys.exit("Use a passphrase of at least 12 characters.")
    return p


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("ca")
    s = sub.add_parser("server")
    s.add_argument(
        "--ip", action="append", default=[], help="the server's address (repeat for more than one)"
    )
    s.add_argument(
        "--name",
        action="append",
        default=[],
        help="a name ending in .local or .lan that reaches the server (optional)",
    )
    sub.add_parser("check")
    a = ap.parse_args()
    if a.cmd == "ca":
        make_ca(ask(prompt_again=True))
    elif a.cmd == "server":
        make_server(ask(), a.ip, a.name)
    else:
        check()


if __name__ == "__main__":
    main()
