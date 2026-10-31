"""Member identity is authenticated; changing a game role never swaps rows."""
import sys
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'web'))
from tunnel.room_lobby import RoomLobby
from tunnel.native_relay import NativeChannel


class MemberIdentityTests(unittest.TestCase):
    def test_game_names_are_keyed_by_member_and_account_name_cannot_be_spoofed(self):
        peers = [SimpleNamespace(member_id=label, account=account, authenticated=True,
                    conn=SimpleNamespace(is_closed=lambda: False), room='room')
                 for label, account in [('host', 'one'), ('guest', 'two')]]
        relay = SimpleNamespace(peers=peers, federation=SimpleNamespace(accounts=SimpleNamespace(
            social=SimpleNamespace(name=lambda account: {'one':'NodeHost', 'two':'NodeGuest'}[account]))))
        room = SimpleNamespace(owner=peers[0], chat_sessions=[], game=dict(version='1.21.1'))
        lobby = RoomLobby(relay, room)
        for peer in peers:
            lobby.join(peer)
            lobby.dispatch(peer, dict(op='member_update', config_id=lobby.configuration,
                seq=1, name='Role-' + peer.member_id, account_name='Spoofed', state='playing'))
        snapshot = lobby.snapshot(peers[1], {})
        rows = {row['id']: row for row in snapshot['members']}
        self.assertEqual((rows['host']['name'], rows['host']['account_name']), ('Role-host', 'NodeHost'))
        self.assertEqual((rows['guest']['name'], rows['guest']['account_name']), ('Role-guest', 'NodeGuest'))
        lobby.dispatch(peers[1], dict(op='member_update', config_id=lobby.configuration,
            seq=2, name='NewRole', state='ready'))
        rows = {row['id']: row for row in lobby.snapshot(peers[0], {})['members']}
        self.assertEqual(rows['guest']['name'], 'NewRole')
        self.assertEqual(rows['host']['name'], 'Role-host')
        self.assertEqual(snapshot['self_id'], 'guest')

    def test_native_upload_channel_supports_idempotent_read_backpressure(self):
        relay = SimpleNamespace(send=Mock())
        channel = NativeChannel(relay, SimpleNamespace(conn=SimpleNamespace(identity='session')), 'upload')
        channel.resume_reading()  # Receiving the header must not raise AttributeError.
        relay.send.assert_not_called()
        channel.pause_reading()
        channel.pause_reading()
        channel.resume_reading()
        channel.resume_reading()
        self.assertEqual([c.args[0]['paused'] for c in relay.send.call_args_list], [True, False])
        self.assertTrue(all(c.args[0]['op'] == 'channel_read_pause' for c in relay.send.call_args_list))
