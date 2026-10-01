"""The backend's own CA and server certificate: browsers need HTTPS before they allow the microphone."""
from cryptography import x509
from cryptography.hazmat.primitives.asymmetric import ec

from search_engine import tls


def load(path):
    return x509.load_pem_x509_certificate(open(path, 'rb').read())


def san(cert):
    value = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    return {str(v) for v in value.get_values_for_type(x509.DNSName)} | {str(v) for v in value.get_values_for_type(x509.IPAddress)}


def test_issues_a_server_certificate_signed_by_a_stable_local_ca(tmp_path):
    cert_file, key_file, ca_file = tls.ensure_certificates(tmp_path, {'tls': {'names': ['search.lab', '10.0.0.5']}})
    ca, leaf = load(ca_file), load(cert_file)
    assert ca.extensions.get_extension_for_class(x509.BasicConstraints).value.ca
    assert leaf.issuer == ca.subject
    ca.public_key().verify(leaf.signature, leaf.tbs_certificate_bytes, ec.ECDSA(leaf.signature_hash_algorithm))
    assert {'localhost', '127.0.0.1', 'search.lab', '10.0.0.5'} <= san(leaf)
    assert open(cert_file, 'rb').read().count(b'BEGIN CERTIFICATE') == 2      # chain carries the CA

    ca_bytes, leaf_bytes = open(ca_file, 'rb').read(), open(cert_file, 'rb').read()
    tls.ensure_certificates(tmp_path, {'tls': {'names': ['search.lab', '10.0.0.5']}})
    assert open(cert_file, 'rb').read() == leaf_bytes                         # nothing changed: nothing re-issued

    tls.ensure_certificates(tmp_path, {'tls': {'names': ['search.lab', '10.0.0.5', '10.0.0.6']}})
    assert open(ca_file, 'rb').read() == ca_bytes                             # clients never re-install the CA
    assert '10.0.0.6' in san(load(cert_file))                                 # a new address gets a new certificate


def test_administrator_supplied_certificate_wins(tmp_path):
    assert tls.ensure_certificates(tmp_path, {'tls': {'cert_file': 'c.pem', 'key_file': 'k.pem'}}) == ('c.pem', 'k.pem', None)
    assert not any(tmp_path.iterdir())
