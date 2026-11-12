"""Delegate existing room membership to the resource origin; never proxy bytes."""
import base64
import hashlib
import hmac
import json
import os
import time
from urllib.parse import urlsplit

from .room_lobby import config_id


def configuration(relay):
    """Return the effective local settings, including legacy environment config."""
    configured = getattr(relay, 'content_cache_config', None)
    if configured is not None:
        return dict(configured) if configured.get('enabled') else dict(enabled=False)
    endpoint = os.environ.get('BUGGCRAFT_ROOM_CACHE_URL', '').rstrip('/')
    if not endpoint:
        return dict(enabled=False)
    return dict(enabled=True, url=endpoint,
                secret=os.environ.get('BUGGCRAFT_ROOM_CACHE_SECRET', ''),
                issuer=os.environ.get('BUGGCRAFT_ROOM_CACHE_ISSUER', 'default'), name='')


def grant(peer, room, request):
    if (not room or peer.role not in ('host', 'client') or not peer.authenticated
            or peer.conn.is_closed() or peer.room in peer.relay.closed_rooms
            or room.lobby and peer.member_id in room.lobby.blocked):
        raise ValueError('Room authentication required')
    configured = configuration(peer.relay)
    endpoint, secret, issuer = (configured.get(key, '') for key in ('url', 'secret', 'issuer'))
    url = urlsplit(endpoint)
    if (not endpoint or url.scheme != 'https' or not url.hostname
            or url.username or url.password or url.path or url.query or url.fragment
            or url.port not in (None, 443) or not 1 <= len(issuer) <= 64):
        raise ValueError('Room file fallback is not configured on this node')
    owner = room.owner is peer
    now = int(time.time())
    if not owner:
        # Downloading is an ordinary public mirror operation, with no bearer.
        return dict(protocol='room-content-cache-v1', url=endpoint, expires_at=now + 300)
    if len(secret) < 32:
        raise ValueError('Room upload authorization is not configured on this node')
    if 'share' in request:
        if not owner or type(request['share']) is not bool:
            raise ValueError('Only the file provider may enable sharing')
        room.content_sharing = request['share']
    if not getattr(room, 'content_sharing', False):
        raise ValueError('The room provider has not enabled file sharing')
    claims = dict(v=1, iss=issuer, room=peer.room, config=config_id(room.game),
                  write=owner, iat=now, exp=now + 300,
                  **{key: room.game.get(key, '' if key.endswith('hash') else 0)
                     for key in ('mods_hash', 'mods_count', 'resources_hash', 'resources_count')})
    raw = json.dumps(claims, sort_keys=True, separators=(',', ':')).encode()
    body = base64.urlsafe_b64encode(raw).rstrip(b'=')
    signature = base64.urlsafe_b64encode(hmac.digest(secret.encode(), body, 'sha256')).rstrip(b'=')
    return dict(protocol='room-content-cache-v1', url=endpoint,
                token=(body + b'.' + signature).decode(), expires_at=claims['exp'])
