"""Real signed test identities without passwords or external account services."""
import hashlib
import json
import secrets

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, utils
from tunnel.accounts import canonical, encode


def sign(key, value):
    r, s = utils.decode_dss_signature(key.sign(canonical(value), ec.ECDSA(hashes.SHA256())))
    return encode(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))


def enroll(store, username, *, delegated=False):
    key = ec.generate_private_key(ec.SECP256R1())
    numbers = key.public_key().public_numbers()
    public = dict(kty='EC', crv='P-256', x=encode(numbers.x.to_bytes(32, 'big')),
                  y=encode(numbers.y.to_bytes(32, 'big')))
    uid = secrets.token_hex(16)
    account = uid + '.' + store.federation.id
    state = dict(account=account, revision=1, box=dict(salt=encode(secrets.token_bytes(16)),
        iv=encode(secrets.token_bytes(12)), ciphertext=encode(secrets.token_bytes(64)), iterations=600000))
    route = dict(account=account, revision=1, copies=0, digest=hashlib.sha256(canonical(state)).hexdigest())
    store.enroll(uid, username, public, state, sign(key, state), 0, sign(key, route))
    delegate = ec.generate_private_key(ec.SECP256R1()) if delegated else None
    delegation = None
    if delegate:
        numbers = delegate.public_key().public_numbers()
        delegation = dict(kty='EC', crv='P-256', x=encode(numbers.x.to_bytes(32, 'big')),
                          y=encode(numbers.y.to_bytes(32, 'big')))
    challenge = store.challenge('login', account, 'https://test.invalid', delegation)
    grant = json.loads(challenge['message'])
    credential = store.credential(account, (grant, sign(key, grant)))
    if delegate:
        credential += '~' + encode(delegate.private_numbers().private_value.to_bytes(32, 'big'))
    return account, credential
