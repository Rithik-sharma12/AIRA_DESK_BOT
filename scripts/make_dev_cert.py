"""Create a self-signed HTTPS certificate so phones and other devices can use the microphone.

Browsers only allow the microphone on https:// pages (or on http://localhost). This
makes certs/aira-dev.crt + certs/aira-dev.key covering localhost, this laptop's name and
its current LAN addresses, and prints the two .env lines to enable HTTPS.

  python scripts/make_dev_cert.py                 # current addresses
  python scripts/make_dev_cert.py --ip 192.168.43.20 --ip 10.0.0.5   # add more

Re-run it when the laptop gets a new address on a different network (hotspots change).
The certificate is for development on your own network only; browsers will show a
warning the first time, which you accept for this address.
"""

from __future__ import annotations

import argparse
import datetime as dt
import ipaddress
import socket
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> int:
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.x509.oid import NameOID
    except ImportError:
        print("Install the dependency first:  pip install cryptography")
        return 2
    from laptop_server.main import lan_ips

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ip", action="append", default=[], help="extra IP address to include")
    ap.add_argument("--out", type=Path, default=ROOT / "certs")
    ap.add_argument("--days", type=int, default=397)
    a = ap.parse_args()

    host = socket.gethostname()
    names = sorted({"localhost", host, f"{host}.local"})
    ips = sorted({"127.0.0.1", *lan_ips(), *a.ip})
    san = [x509.DNSName(n) for n in names] + [x509.IPAddress(ipaddress.ip_address(i)) for i in ips]

    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "AIRA development server")])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject).issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(days=a.days))
        .add_extension(x509.SubjectAlternativeName(san), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .sign(key, hashes.SHA256())
    )
    a.out.mkdir(parents=True, exist_ok=True)
    crt, pem = a.out / "aira-dev.crt", a.out / "aira-dev.key"
    crt.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    pem.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                      serialization.NoEncryption()))
    print(f"Wrote {crt}\n      {pem}   (private key: keep it, it is git-ignored)")
    print("Valid for: " + ", ".join(names + ips))
    rel = lambda p: p.relative_to(ROOT).as_posix() if p.is_relative_to(ROOT) else str(p)  # noqa: E731
    print("\nAdd to .env:")
    print(f"AIRA_SSL_CERTFILE={rel(crt)}\nAIRA_SSL_KEYFILE={rel(pem)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
