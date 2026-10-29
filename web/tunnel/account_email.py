"""Private, signed mailbox associations; never part of the public user directory.

Mail ownership locates identities. It does not replace their encrypted keys.
Partitioned registrations converge without allocating a global username lock.
"""
import hashlib
import hmac
import json
import re

import asyncssh

from .accounts import MAX_ACCOUNTS, canonical, decode, encode


class EmailAccounts:
    def __init__(self, store):
        self.store = store
        self.path = store.path.with_name('accounts-email.json')
        self.entries = {}
        if self.path.exists():
            self.ingest(json.loads(self.path.read_text(encoding='utf-8')), save=False)

    def mailbox(self, email):
        return hmac.new(self.store.federation.network_key.encode(),
                        b'buggcraft-email-v1\0' + email.strip().casefold().encode(), hashlib.sha256).hexdigest()

    @staticmethod
    def entry_key(body):
        return body['mailbox'], body['account'] if body['kind'] == 'link' else 'choice'

    @staticmethod
    def order(entry):
        return entry['body']['revision'], hashlib.sha256(canonical(entry)).hexdigest()

    def accept(self, entry):
        from .federation import identity
        if not isinstance(entry, dict) or set(entry) != {'body', 'signature', 'proof'} or len(canonical(entry)) > 2048:
            raise ValueError('Invalid mailbox association')
        body = entry['body']
        if (not isinstance(body, dict) or set(body) != {'kind', 'mailbox', 'account', 'revision', 'issuer_key'} or
                body['kind'] not in ('link', 'choice') or
                not isinstance(body['mailbox'], str) or not re.fullmatch('[0-9a-f]{64}', body['mailbox']) or
                not isinstance(body['account'], str) or not re.fullmatch(r'[0-9a-f]{32}\.[0-9a-f]{32}', body['account']) or
                type(body['revision']) is not int or not 1 <= body['revision'] <= 2147483647):
            raise ValueError('Invalid mailbox association')
        fed = self.store.federation
        issuer = identity(body['issuer_key'])
        if (issuer in fed.blocked or not isinstance(entry['proof'], str) or
                not hmac.compare_digest(entry['proof'], fed.proof(issuer)) or
                not asyncssh.import_public_key(body['issuer_key']).verify(canonical(body), decode(entry['signature']))):
            raise ValueError('Invalid mailbox issuer')
        key = self.entry_key(body)
        old = self.entries.get(key)
        if old and self.order(entry) <= self.order(old):
            return False
        if not old and len(self.entries) >= MAX_ACCOUNTS * 2:
            raise ValueError('Mailbox directory full')
        self.entries[key] = entry
        return True

    def ingest(self, entries, save=True):
        if not isinstance(entries, list) or len(entries) > MAX_ACCOUNTS * 2:
            raise ValueError('Too many mailbox associations')
        changed = False
        for entry in entries:
            try:
                changed = self.accept(entry) or changed
            except (ValueError, TypeError, KeyError, asyncssh.Error):
                continue
        if changed and save:
            self.save()

    def save(self):
        from .portal import atomic_json
        atomic_json(self.path, list(self.entries.values()))

    def snapshot(self):
        from .federation import identity
        fed = self.store.federation
        return [entry for _, entry in sorted(self.entries.items())
                if identity(entry['body']['issuer_key']) not in fed.blocked
                and hmac.compare_digest(entry['proof'], fed.proof(identity(entry['body']['issuer_key'])))]

    def write(self, kind, mailbox, account):
        fed = self.store.federation
        revision = max((e['body']['revision'] for e in self.entries.values()
                        if e['body']['mailbox'] == mailbox), default=0) + 1
        body = dict(kind=kind, mailbox=mailbox, account=account, revision=revision,
                    issuer_key=fed.profile['server_public_key'])
        self.accept(dict(body=body, signature=encode(fed.key.sign(canonical(body), b'ssh-ed25519')),
                         proof=fed.proof(fed.id)))
        self.save()

    def link(self, email, account):
        self.write('link', self.mailbox(email), account)

    def choose(self, mailbox, account):
        if not any(item['account'] == account for item in self.candidates(mailbox)):
            raise ValueError('请选择已验证邮箱所属的账号。')
        self.write('choice', mailbox, account)

    def candidates(self, mailbox):
        from .federation import identity
        fed = self.store.federation
        entries = [e for e in self.entries.values() if e['body']['mailbox'] == mailbox
                   and identity(e['body']['issuer_key']) not in fed.blocked
                   and hmac.compare_digest(e['proof'], fed.proof(identity(e['body']['issuer_key'])))]
        preferred = next((e['body']['account'] for e in entries if e['body']['kind'] == 'choice'), None)
        headers = dict(self.store.replication.directory)
        headers.update(self.store.records)
        accounts = {e['body']['account'] for e in entries if e['body']['kind'] == 'link'}
        return [dict(account=a, username=headers[a]['certificate']['username'],
                     home=headers[a]['certificate']['home'], preferred=a == preferred)
                for a in sorted(accounts, key=lambda a: (a != preferred, a))
                if a in headers and not fed.relay.account_blocked(a)
                and headers[a]['certificate']['home'] not in fed.blocked]
