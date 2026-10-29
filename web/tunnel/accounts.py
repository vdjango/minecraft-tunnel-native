"""Portable signed identities. Only public keys and encrypted client boxes replicate."""
import base64
from contextvars import ContextVar
import hashlib
import hmac
import json
import re
import secrets
import time
from pathlib import Path

import asyncssh
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, utils

MAX_ACCOUNTS = 512
MAX_BOX = 4096
ITERATIONS = 600000
# Task-local, fixed denial categories; never retain a token or decoded claims.
AUTHENTICATION_FAILURE = ContextVar('account_authentication_failure', default=None)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()


def encode(value):
    return base64.urlsafe_b64encode(value).decode().rstrip('=')


def decode(value):
    if not isinstance(value, str):
        raise ValueError('Invalid encoding')
    return base64.b64decode(value + '=' * (-len(value) % 4), altchars=b'-_', validate=True)


def public_key(value):
    if not isinstance(value, dict) or set(value) != {'kty', 'crv', 'x', 'y'} or value['kty'] != 'EC' or value['crv'] != 'P-256':
        raise ValueError('Invalid account key')
    x, y = decode(value['x']), decode(value['y'])
    if len(x) != 32 or len(y) != 32:
        raise ValueError('Invalid account coordinates')
    return ec.EllipticCurvePublicNumbers(int.from_bytes(x, 'big'), int.from_bytes(y, 'big'), ec.SECP256R1()).public_key()


def verify(key, message, signature):
    raw = decode(signature)
    if len(raw) != 64:
        raise ValueError('Invalid signature')
    der = utils.encode_dss_signature(int.from_bytes(raw[:32], 'big'), int.from_bytes(raw[32:], 'big'))
    try:
        public_key(key).verify(der, message, ec.ECDSA(hashes.SHA256()))
    except InvalidSignature as exc:
        raise ValueError('Invalid signature') from exc


def validate_box(box):
    if not isinstance(box, dict) or set(box) != {'salt', 'iv', 'ciphertext', 'iterations'}:
        raise ValueError('Invalid encrypted identity')
    if type(box['iterations']) is not int or box['iterations'] != ITERATIONS:
        raise ValueError('Invalid password derivation parameters')
    if len(decode(box['salt'])) != 16 or len(decode(box['iv'])) != 12 or not 32 <= len(decode(box['ciphertext'])) <= MAX_BOX:
        raise ValueError('Invalid encrypted identity')


class Accounts:
    def __init__(self, federation, path):
        self.federation, self.path = federation, Path(path)
        self.records = {}
        if self.path.exists():
            values = json.loads(self.path.read_text(encoding='utf-8'))
            if not isinstance(values, list) or len(values) > MAX_ACCOUNTS:
                raise ValueError('Invalid identity replica')
            for record in values:
                self.ingest_one(record)
        self.challenges = {}
        self.sessions = {}
        self.grants = {}
        from .account_replication import Replication
        self.replication = Replication(self)
        from .account_email import EmailAccounts
        self.email_accounts = EmailAccounts(self)
        from .social import Social
        self.social = Social(self)

    def save(self):
        from .portal import atomic_json
        atomic_json(self.path, list(self.records.values()))
        if hasattr(self, 'replication'):
            self.replication.save()

    def validate_record(self, record):
        fields = {'certificate', 'issuer_signature', 'state', 'signature'}
        if not isinstance(record, dict) or set(record) not in (fields, fields | {'routing', 'routing_signature'}):
            raise ValueError('Invalid identity record')
        if len(canonical(record)) > 16384:
            raise ValueError('Identity record too large')
        cert, state = record['certificate'], record['state']
        if not isinstance(cert, dict) or not isinstance(state, dict) or set(cert) != {'uid', 'home', 'home_key', 'username', 'public_key', 'created'}:
            raise ValueError('Invalid identity certificate')
        from .federation import identity
        if not isinstance(cert['uid'], str) or not re.fullmatch('[0-9a-f]{32}', cert['uid']) or identity(cert['home_key']) != cert['home']:
            raise ValueError('Invalid identity issuer')
        if not isinstance(cert['username'], str) or not re.fullmatch(r'[A-Za-z0-9_-]{3,32}', cert['username']) or type(cert['created']) not in (float, int):
            raise ValueError('Invalid account name')
        public_key(cert['public_key'])
        if not asyncssh.import_public_key(cert['home_key']).verify(canonical(cert), decode(record['issuer_signature'])):
            raise ValueError('Invalid issuer signature')
        if set(state) != {'account', 'revision', 'box'} or state['account'] != cert['uid'] + '.' + cert['home']:
            raise ValueError('Invalid identity state')
        if type(state['revision']) is not int or not 1 <= state['revision'] <= 2147483647:
            raise ValueError('Invalid identity revision')
        validate_box(state['box'])
        verify(cert['public_key'], canonical(state), record['signature'])
        if 'routing' in record:
            from .account_replication import header, validate_header
            validate_header(header(record))
            route = record['routing']
            if route['revision'] != state['revision'] or route['digest'] != hashlib.sha256(canonical(state)).hexdigest():
                raise ValueError('Backup policy does not match account state')
        return state['account']

    def ingest_one(self, record):
        account = self.validate_record(record)
        cert, state = record['certificate'], record['state']
        old = self.records.get(account)
        if old:
            if canonical(old['certificate']) != canonical(cert):
                raise ValueError('Identity certificate changed')
            if 'routing' in old and 'routing' not in record:
                raise ValueError('Cannot remove signed backup policy')
            # Deterministic convergence for concurrent writes; lower revisions never win.
            order = lambda r: (r['state']['revision'], hashlib.sha256(canonical(r['state'])).hexdigest())
            if order(record) <= order(old):
                return False
        elif len(self.records) >= MAX_ACCOUNTS:
            raise ValueError('Identity replica full')
        self.records[account] = record
        if hasattr(self, 'replication') and 'routing' in record:
            from .account_replication import header
            self.replication.accept(header(record))
        return True

    def ingest(self, entries):
        if not isinstance(entries, list) or len(entries) > MAX_ACCOUNTS:
            raise ValueError('Too many identity replicas')
        changed = False
        for entry in entries:
            try:
                record = entry['record']
                home = record['certificate']['home']
                if home in self.federation.blocked or not hmac.compare_digest(entry['proof'], self.federation.proof(home)):
                    continue
                # Identical records were verified when first accepted.
                account = record['state']['account']
                if 'routing' in record:
                    from .account_replication import header
                    self.replication.accept(header(record))
                    if not self.replication.current(record):
                        continue
                    if self.federation.id not in self.replication.targets(account) and account not in self.records:
                        continue
                else:
                    continue  # Older peers cannot replicate without signed user consent.
                if self.records.get(account) == record:
                    continue
                changed = self.ingest_one(record) or changed
            except (ValueError, TypeError, KeyError, asyncssh.Error):
                continue
        if changed:
            self.save()

    def snapshot(self, recipient=None):
        return [{'record': record, 'proof': self.federation.proof(record['certificate']['home'])}
                for account, record in self.records.items() if record['certificate']['home'] not in self.federation.blocked
                and 'routing' in record and record['routing']['copies'] > 0 and
                     (recipient is None or recipient in self.replication.targets(account))]

    def enroll(self, uid, username, key, state, signature, copies=0, routing_signature=None):
        if not isinstance(username, str) or not re.fullmatch(r'[A-Za-z0-9_-]{3,32}', username):
            raise ValueError('Username needs 3-32 letters, digits, _ or -')
        if len([r for r in self.records.values() if r['certificate']['home'] == self.federation.id]) >= 128:
            raise ValueError('Local account limit reached')
        cert = {'uid': uid, 'home': self.federation.id, 'home_key': self.federation.profile['server_public_key'],
                'username': username, 'public_key': key, 'created': time.time()}
        record = {'certificate': cert, 'issuer_signature': encode(self.federation.key.sign(canonical(cert), b'ssh-ed25519')),
                  'state': state, 'signature': signature}
        if routing_signature is not None:
            record.update(routing={'account': state['account'], 'revision': state['revision'],
                                   'copies': copies, 'digest': hashlib.sha256(canonical(state)).hexdigest()},
                          routing_signature=routing_signature)
        self.ingest_one(record)
        self.save()
        return record

    def challenge(self, kind, account, origin, delegation=None):
        now = time.monotonic()
        self.challenges = {k: v for k, v in self.challenges.items() if v['expires'] > now}
        if len(self.challenges) >= 256:
            raise ValueError('Authentication busy')
        nonce = secrets.token_urlsafe(32)
        message = '\n'.join(('buggcraft-account-v1', kind, self.federation.id, origin, account, nonce))
        if kind == 'login':
            message = canonical({'a': account, 'i': self.federation.id, 'e': int(time.time()) + 7 * 86400,
                                 'n': self.federation.proof('account-audience')[:24],
                                 'p': 'buggcraft-account-login-v2', 'o': origin, 'r': nonce}).decode()
            if delegation is not None:
                public_key(delegation)
                message = canonical(dict(json.loads(message), d=delegation)).decode()
        self.challenges[nonce] = {'kind': kind, 'account': account, 'message': message, 'expires': now + 60}
        return {'nonce': nonce, 'message': message}

    def consume(self, nonce, kind):
        challenge = self.challenges.pop(nonce, None)
        if not challenge or challenge['kind'] != kind or challenge['expires'] <= time.monotonic():
            raise ValueError('Challenge expired')
        return challenge

    def authorize(self, account, challenge, signature):
        verify(self.replication.get(account)['certificate']['public_key'], challenge['message'].encode(), signature)
        grant = json.loads(challenge['message'])
        if grant['a'] != account or grant['i'] != self.federation.id or grant['p'] != 'buggcraft-account-login-v2':
            raise ValueError('Invalid login grant')
        self.grants[account] = (grant, signature)

    def credential(self, account, authorization=None):
        grant, signature = authorization or self.grants[account]
        expiry = grant['e']
        if grant.get('p') == 'buggcraft-launcher-device-v1':
            expiry = min(expiry, int(time.time()) + 7 * 86400)
        claims = {'a': account, 'i': self.federation.id, 'e': expiry, 'n': grant['n'],
                  'g': grant, 's': signature}
        payload = canonical(claims)
        return 'acct1.' + encode(payload) + '.' + encode(self.federation.key.sign(payload, b'ssh-ed25519'))

    def authenticate(self, token, blocked=(), require_record=True):
        AUTHENTICATION_FAILURE.set(None)
        def reject(reason):
            AUTHENTICATION_FAILURE.set(reason)
            return None
        if isinstance(token, str):
            token = token.split('~', 1)[0]
        if not isinstance(token, str) or len(token) > 2048 or not token.startswith('acct1.'):
            return reject('credential-format')
        try:
            encoded, signature = token[6:].split('.')
            payload = decode(encoded)
            claims = json.loads(payload)
            account, issuer = claims['a'], claims['i']
            record = (self.replication.get(account) or self.replication.directory.get(account)) if isinstance(account, str) else None
            if not isinstance(account, str) or not isinstance(issuer, str):
                return reject('account-identity-format')
            if require_record and not record:
                return reject('account-record-unavailable')
            if account in blocked:
                return reject('account-blocked')
            if issuer in self.federation.blocked or (record and record['certificate']['home'] in self.federation.blocked):
                return reject('account-node-blocked')
            if type(claims['e']) is not int:
                return reject('account-expiry-format')
            if not time.time() < claims['e']:
                return reject('account-expired-claim')
            if not claims['e'] <= time.time() + 7 * 86400 + 60:
                return reject('account-expiry-out-of-range')
            if not hmac.compare_digest(claims['n'], self.federation.proof('account-audience')[:24]):
                return reject('account-audience-mismatch')
            profile = self.federation.profile if issuer == self.federation.id else self.federation.seeds.get(issuer)
            if not profile:
                return reject('account-issuer-unavailable')
            if not asyncssh.import_public_key(profile['server_public_key']).verify(payload, decode(signature)):
                return reject('account-node-signature-invalid')
            grant = claims['g']
            device = isinstance(grant, dict) and grant.get('p') == 'buggcraft-launcher-device-v1'
            if (not isinstance(grant, dict) or any(grant.get(k) != claims[k] for k in ('a', 'i', 'n')) or
                    (not device and (grant.get('p') != 'buggcraft-account-login-v2' or grant.get('e') != claims['e']))):
                return reject('account-grant-mismatch')
            if device:
                if (type(grant.get('b')) is not int or type(grant.get('e')) is not int or
                        not grant['b'] <= time.time() + 5 or not 0 < grant['e'] - grant['b'] <= 180 * 86400 or
                        claims['e'] > grant['e'] or not self.launcher_devices.allowed(grant)):
                    return reject('account-device-inactive')
            if record:
                try:
                    verify(record['certificate']['public_key'], canonical(grant), claims['s'])
                except (ValueError, KeyError, TypeError):
                    return reject('account-holder-signature-invalid')
            return account
        except (ValueError, KeyError, TypeError, asyncssh.Error):
            return reject('credential-malformed')
