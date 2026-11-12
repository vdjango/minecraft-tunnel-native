"""One social routing owner for directory clients, trusted retries and centers."""
import asyncio
import time
import asyncssh

from .room_lobby import manager_account


def context(fed, event=None, account=None):
    """Carry signed public identity and relationship prerequisites, never ciphertext."""
    if event is None and account is None:
        return {}
    actor, target = (event['grant']['a'], event['action']['target']) if event else (account, account)
    social = fed.accounts.social
    return {'identities': [fed.accounts.replication.directory[key] for key in {actor, target}
                           if key in fed.accounts.replication.directory],
            'relationships': [social.edges[key] for key in ((actor, target), (target, actor))
                              if key in social.edges]}


def accept_context(fed, value):
    if not isinstance(value, dict):
        raise ValueError('Invalid social context')
    identities, relationships = value.get('identities', []), value.get('relationships', [])
    if not isinstance(identities, list) or len(identities) > 2 or not isinstance(relationships, list) or len(relationships) > 2:
        raise ValueError('Social context exceeds budget')
    changed = False
    for entry in identities:
        home = entry['certificate']['home']
        profile = fed.profile if home == fed.id else fed.seeds.get(home)
        if (not profile or home in fed.blocked or
                asyncssh.import_public_key(profile['server_public_key']).export_public_key() !=
                asyncssh.import_public_key(entry['certificate']['home_key']).export_public_key()):
            raise ValueError('Unknown identity issuer')
        changed = fed.accounts.replication.accept(entry) or changed
    if changed:
        fed.accounts.replication.save()
    fed.accounts.social.ingest(relationships)


async def route(fed, account, event, *, local_only=False, fresh=True):
    """Apply once at room owner; centers retain the acknowledged signed event."""
    social = fed.accounts.social
    control = getattr(fed, 'control', None)
    if control and control.mode == 'edge' and control.bundle:
        raise OSError('节点正在加入控制中心，请稍后重试。')
    if event is None:
        return social.state(account)
    actor, action = social.validate(event, fresh=fresh)
    if actor != account or fed.relay.account_blocked(account):
        raise ValueError('Account access denied')
    if social.expires(event) <= time.time():
        raise ValueError('操作已过期，请刷新后重试。')
    if event['grant'].get('p') == 'buggcraft-launcher-device-v1' and not fed.accounts.launcher_devices.allowed(event['grant']):
        raise ValueError('设备授权已失效，请重新登录。')
    if action['op'] == 'join':
        invitation = social.invitations.get((action['target'], actor))
        room_id = invitation['action']['room'] if invitation else ''
    else:
        room_id = action.get('room', '')
    if room_id and room_id not in fed.relay.rooms:
        target = fed.locate(room_id)
        if local_only or not target:
            raise ValueError('好友的房间暂不可用。')
        async with asyncio.timeout(4):
            result = await fed.account_rpc(target, 'social_deliver', account=account, event=event,
                                           context=context(fed, event))
        social.ingest([event])
        return result
    if action['op'] in ('invite', 'accept_join'):
        room = fed.relay.rooms.get(room_id)
        if not room or manager_account(room) != actor or not room.port:
            raise ValueError('只能邀请好友加入你创建的可用房间。')
    if action['op'] == 'presence' and room_id:
        room = fed.relay.rooms.get(room_id)
        owner = bool(room and manager_account(room) == actor)
        member = any(getattr(peer, 'account', None) == actor and getattr(peer, 'room', None) == room_id
                     for peer in fed.relay.peers)
        if not room or (action['room_role'] == 'host' and not owner) or (not owner and not member):
            raise ValueError('当前房间连接已结束，请刷新状态。')
    return social.apply(account, event, fresh=fresh)


async def deliver(fed, account, event):
    control = getattr(fed, 'control', None)
    if control and control.mode == 'edge':
        from .federation import AccountRPCRejected
        for profile in control.profiles():
            try:
                async with asyncio.timeout(4):
                    response = await fed.account_rpc(profile, 'social_control', account=account,
                                                     event=event, context=context(fed, event, account))
                accept_context(fed, response['context'])
                result = response['result']
                if event is not None and event['action']['op'] != 'join':
                    fed.accounts.social.ingest([event])
                return result
            except AccountRPCRejected:
                raise
            except (OSError, TimeoutError, ValueError, asyncssh.Error):
                continue
        raise OSError('控制中心暂不可用，操作已排队或可稍后重试。')
    return await route(fed, account, event, fresh=False)


async def submit(fed, account, event):
    """Commit before transmission; only a successful remote reply removes outbox."""
    social = fed.accounts.social
    control = getattr(fed, 'control', None)
    if control and control.mode == 'edge' and control.bundle:
        raise OSError('节点正在加入控制中心，请稍后重试。')
    if event is None:
        return await deliver(fed, account, None)
    actor, action = social.validate(event, fresh=True)
    if actor != account or fed.relay.account_blocked(account):
        raise ValueError('Account access denied')
    durable = action['op'] not in ('presence', 'join')
    if durable:
        social.queue(event)
    try:
        result = await deliver(fed, account, event)
    except (OSError, TimeoutError, asyncssh.Error):
        if not durable:
            raise
        result = social.state(account)
        result['delivery'] = {'id': action['id'], 'state': 'pending'}
        return result
    except ValueError:
        if durable:
            social.acknowledge(action['id'])
        raise
    if durable:
        social.acknowledge(action['id'])
        result['delivery'] = {'id': action['id'], 'state': 'accepted'}
    return result


async def retry_pending(fed):
    """One bounded batch per background round; cancellation propagates on shutdown."""
    social = fed.accounts.social
    for key, event in list(social.pending.items()):
        if social.expires(event) <= time.time():
            social.acknowledge(key)
    # Rotate before awaiting so an unreachable first recipient cannot starve peers.
    due = [key for key in social.pending if social.retry_at.get(key, 0) <= time.monotonic()]
    for key in due[:4]:
        event = social.pending.pop(key)
        social.pending[key] = event
        if social.expires(event) <= time.time():
            social.acknowledge(key)
            continue
        try:
            async with asyncio.timeout(5):
                await deliver(fed, event['grant']['a'], event)
            social.acknowledge(key)
        except (OSError, TimeoutError, ValueError, asyncssh.Error):
            failures = min(5, social.retry_failures.get(key, 0) + 1)
            social.retry_failures[key] = failures
            social.retry_at[key] = time.monotonic() + min(30, 2 ** failures)
