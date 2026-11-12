from __future__ import annotations

import datetime
import ipaddress
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID


@dataclass(frozen=True)
class TLSBundle:
    ca_certificate: Path
    server_certificate: Path
    server_key: Path
    ca_sha256: str


def _validated_tls_names(names: list[str]):
    sans = []
    seen = set()
    for name in names:
        if not isinstance(name, str) or not name or name != name.strip():
            raise ValueError(f"Invalid TLS name: {name!r}")
        try:
            address = ipaddress.ip_address(name)
        except ValueError:
            if len(name) > 253 or not all(
                re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label)
                for label in name.split(".")
            ):
                raise ValueError(f"Invalid TLS name: {name!r}") from None
            key = ("dns", name.lower())
            san = x509.DNSName(name.lower())
        else:
            if address.is_unspecified:
                raise ValueError(f"Invalid TLS name: {name!r}")
            key = ("ip", address)
            san = x509.IPAddress(address)
        if key not in seen:
            seen.add(key)
            sans.append(san)
    if not sans:
        raise ValueError("At least one TLS name is required")
    return sans


def create_tls_bundle(destination: str | Path, names: list[str]) -> TLSBundle:
    """Create a fresh local CA and server certificate for explicit host names."""
    destination = Path(destination).absolute()
    if destination.exists():
        raise FileExistsError(destination)
    sans = _validated_tls_names(names)

    now = datetime.datetime.now(datetime.timezone.utc)
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    server_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "AI Edda Local CA")])
    server_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "AI Edda Server")])
    ca = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=None,
                decipher_only=None,
            ),
            critical=True,
        )
        .sign(ca_key, hashes.SHA256())
    )
    server = (
        x509.CertificateBuilder()
        .subject_name(server_name)
        .issuer_name(ca_name)
        .public_key(server_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=825))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.SubjectAlternativeName(sans), critical=False)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=True,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=None,
                decipher_only=None,
            ),
            critical=True,
        )
        .sign(ca_key, hashes.SHA256())
    )
    stage = Path(tempfile.mkdtemp(prefix=f".{destination.name}.", dir=destination.parent))
    try:
        files = {
            "ca.pem": ca.public_bytes(serialization.Encoding.PEM),
            "server.pem": server.public_bytes(serialization.Encoding.PEM),
            "server-key.pem": server_key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            ),
        }
        for filename, data in files.items():
            fd = os.open(
                stage / filename,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600 if filename == "server-key.pem" else 0o644,
            )
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
        os.rename(stage, destination)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    return TLSBundle(
        destination / "ca.pem",
        destination / "server.pem",
        destination / "server-key.pem",
        ca.fingerprint(hashes.SHA256()).hex(),
    )
