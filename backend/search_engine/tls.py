"""HTTPS for the console: browsers only allow the microphone on a secure (https) page.

Either an administrator supplies a certificate (tls.cert_file / tls.key_file in config, e.g. from the company
CA), or the backend keeps its own small certificate authority: the CA is created once and never changes, so
clients trust it once; the server certificate is re-issued whenever the machine's names/addresses change or it
nears expiry, without anyone having to re-install anything.
"""
import datetime
import ipaddress
import socket
from pathlib import Path

CA_NAME = 'Search Engine Local CA'
LEAF_DAYS = 397
RENEW_BEFORE = datetime.timedelta(days=30)


def local_names(extra=()):
    host = socket.gethostname()
    names = {host.lower(), socket.getfqdn().lower(), 'localhost', '127.0.0.1', '::1'}
    try:
        names.update(info[4][0] for info in socket.getaddrinfo(host, None, socket.AF_INET))
    except OSError:
        pass
    names.update(n.strip().lower() for n in extra if n and n.strip())
    return sorted(n for n in names if n)


def _key():
    from cryptography.hazmat.primitives.asymmetric import ec
    return ec.generate_private_key(ec.SECP256R1())


def _write(path, data):
    path.write_bytes(data)


def _load_cert(path):
    from cryptography import x509
    return x509.load_pem_x509_certificate(path.read_bytes())


def _san(names):
    from cryptography import x509
    entries = []
    for n in names:
        try:
            entries.append(x509.IPAddress(ipaddress.ip_address(n)))
        except ValueError:
            entries.append(x509.DNSName(n))
    return x509.SubjectAlternativeName(entries)


def ensure_ca(folder):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.x509.oid import NameOID
    cert_path, key_path = folder / 'ca.crt', folder / 'ca.key'
    if cert_path.exists() and key_path.exists():
        return cert_path, key_path
    folder.mkdir(parents=True, exist_ok=True)
    key = _key()
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f'{CA_NAME} ({socket.gethostname()})'),
                      x509.NameAttribute(NameOID.ORGANIZATION_NAME, 'Search Engine')])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(minutes=5))
            .not_valid_after(now + datetime.timedelta(days=3650))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(x509.KeyUsage(digital_signature=True, key_cert_sign=True, crl_sign=True, content_commitment=False,
                                         key_encipherment=False, data_encipherment=False, key_agreement=False,
                                         encipher_only=False, decipher_only=False), critical=True)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
            .sign(key, hashes.SHA256()))
    _write(key_path, key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    _write(cert_path, cert.public_bytes(serialization.Encoding.PEM))
    return cert_path, key_path


def _leaf_current(cert_path, names):
    from cryptography import x509
    if not cert_path.exists():
        return False
    cert = _load_cert(cert_path)
    if cert.not_valid_after_utc - datetime.datetime.now(datetime.timezone.utc) < RENEW_BEFORE:
        return False
    have = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    current = {str(v) for v in have.get_values_for_type(x509.DNSName)} | {str(v) for v in have.get_values_for_type(x509.IPAddress)}
    return current == set(names)


def ensure_certificates(folder, config=None):
    """(cert_file, key_file, ca_file or None) to serve HTTPS with."""
    tls = (config or {}).get('tls') or {}
    if tls.get('cert_file') and tls.get('key_file'):
        return tls['cert_file'], tls['key_file'], None
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.serialization import load_pem_private_key
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
    folder = Path(folder)
    ca_cert_path, ca_key_path = ensure_ca(folder)
    names = local_names(tls.get('names', ()))
    cert_path, key_path = folder / 'server.crt', folder / 'server.key'
    if not (_leaf_current(cert_path, names) and key_path.exists()):
        ca_cert = _load_cert(ca_cert_path)
        ca_key = load_pem_private_key(ca_key_path.read_bytes(), None)
        key = _key()
        now = datetime.datetime.now(datetime.timezone.utc)
        cert = (x509.CertificateBuilder()
                .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, socket.gethostname())]))
                .issuer_name(ca_cert.subject).public_key(key.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(now - datetime.timedelta(minutes=5)).not_valid_after(now + datetime.timedelta(days=LEAF_DAYS))
                .add_extension(_san(names), critical=False)
                .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
                .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
                .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
                .sign(ca_key, hashes.SHA256()))
        _write(key_path, key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        # The chain file carries the CA too, so clients that only trust the CA can still build the path.
        _write(cert_path, cert.public_bytes(serialization.Encoding.PEM) + ca_cert_path.read_bytes())
    return str(cert_path), str(key_path), str(ca_cert_path)
