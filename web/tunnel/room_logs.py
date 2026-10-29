"""Ephemeral, recipient-scoped consent for preparation troubleshooting logs."""
import re
import secrets
import time


def redacted(text):
    text = re.sub(r'(?i)(?:buggnode1-|bugg1-|acct1\.)[^\s]+', '[credential]', text)
    text = re.sub(r'(?i)\b(?:bearer\s+|(?:token|password|secret|authorization)[\s"\x27:=]+)[^\s,;]+', '[credential]', text)
    text = re.sub(r'(?i)[a-z]:[\\/][^\s\r\n]*|/(?:home|Users|tmp|var|opt)/[^\s\r\n]*', '[local path]', text)
    text = re.sub(r'https?://[^\s]+', '[download address]', text)
    return ''.join(c for c in text if c.isprintable())[:500]


class RoomLogs:
    def __init__(self, lobby):
        self.lobby = lobby
        self.requests, self.grants, self.logs = {}, {}, {}

    def clear(self, member=None):
        if member is None or member == self.lobby.manager:
            self.requests.clear(); self.grants.clear(); self.logs.clear()
        else:
            self.requests.pop(member, None); self.grants.pop(member, None); self.logs.pop(member, None)

    def view(self, peer):
        request = self.requests.get(peer.member_id)
        if request and request['expires'] < time.time():
            self.requests.pop(peer.member_id, None)
            request = None
        return dict(request=request, sharing=self.grants.get(peer.member_id) == self.lobby.manager,
            allowed=list(self.grants) if peer.member_id == self.lobby.manager else [])

    def dispatch(self, peer, request):
        op, actor = request['op'], peer.member_id
        target = request.get('member')
        if op == 'logs_request':
            self.lobby.require_manager(peer)
            if not isinstance(target, str) or target == actor or not self.lobby.active(target):
                raise ValueError('Choose another online member')
            self.requests[target] = dict(id=secrets.token_hex(12), **{'from': actor},
                name=self.lobby.members[actor]['name'], expires=int(time.time()) + 90)
            self.lobby.emit()
            return dict(requested=True)
        if op == 'logs_answer':
            consent = self.requests.get(actor)
            if (not consent or consent['id'] != request.get('request') or consent['expires'] < time.time() or
                    consent['from'] != self.lobby.manager or not self.lobby.active(consent['from'])):
                raise ValueError('Log access request expired')
            allow = request.get('allow')
            if type(allow) is not bool:
                raise ValueError('Explicit consent required')
            self.clear(actor)
            if allow:
                self.grants[actor] = self.lobby.manager
            self.lobby.emit()
            return dict(sharing=allow)
        if op == 'logs_revoke':
            self.clear(actor)
            self.lobby.emit()
            return dict(sharing=False)
        if op == 'logs_append':
            if self.grants.get(actor) != self.lobby.manager:
                raise ValueError('Player consent required')
            rows = request.get('entries')
            if not isinstance(rows, list) or len(rows) > 10:
                raise ValueError('Invalid log batch')
            # Validate the whole batch before publishing any of it.
            for row in rows:
                if not isinstance(row, dict) or type(row.get('seq')) is not int or not 0 <= row['seq'] < 2**53:
                    raise ValueError('Invalid log sequence')
                if not isinstance(row.get('text'), str) or len(row['text']) > 1000:
                    raise ValueError('Invalid log text')
                if row.get('category') not in ('game', 'java', 'assets', 'libraries', 'loader', 'mods', 'resources', 'configuration', 'verify') or row.get('level') not in ('info', 'warning', 'error'):
                    raise ValueError('Invalid log category')
            log = self.logs.setdefault(actor, dict(next=0, entries=[]))
            for row in rows:
                if row['seq'] < log['next']:
                    continue
                log['entries'].append(dict(seq=row['seq'], text=redacted(row['text']),
                    category=row['category'], level=row['level'], time=int(time.time())))
                log['next'] = row['seq'] + 1
            del log['entries'][:-200]
            return dict(next=log['next'])
        if op == 'logs_read':
            self.lobby.require_manager(peer)
            if self.grants.get(target) != actor:
                raise ValueError('Player consent required')
            offset = request.get('offset', 0)
            if type(offset) is not int or not 0 <= offset < 2**53:
                raise ValueError('Invalid log offset')
            log = self.logs.get(target, dict(next=0, entries=[]))
            rows = [row for row in log['entries'] if row['seq'] >= offset][:16]
            return dict(member=target, entries=rows, next=rows[-1]['seq'] + 1 if rows else offset,
                        first=log['entries'][0]['seq'] if log['entries'] else 0)
        raise ValueError('Unknown log operation')
