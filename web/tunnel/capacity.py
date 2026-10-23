"""Conservative admission budgets; decimal MB/s, never a bandwidth guarantee."""
import math
import time


DEFAULTS = dict(max_rooms=128, room_capacity=100, bandwidth_mb_s=0.0,
                reserve_percent=35, per_player_mb_s=0.128)


def policy(value=None):
    if value is not None and not isinstance(value, dict):
        raise ValueError('Invalid capacity policy')
    result = dict(DEFAULTS, **(value or {}))
    for key, low, high in [('max_rooms', 1, 128), ('room_capacity', 1, 1000),
                           ('reserve_percent', 20, 80)]:
        if type(result[key]) is not int or not low <= result[key] <= high:
            raise ValueError('Invalid capacity policy: ' + key)
    for key, low, high in [('bandwidth_mb_s', 0, 125000), ('per_player_mb_s', .001, 100)]:
        if type(result[key]) not in (int, float) or not math.isfinite(result[key]) or not low <= result[key] <= high:
            raise ValueError('Invalid bandwidth policy: ' + key)
    return {key: result[key] for key in DEFAULTS}


class Capacity:
    def __init__(self, relay):
        self.relay = relay
        self.policy = policy(dict(max_rooms=min(relay.max_rooms, 128)))
        self.started = time.monotonic()
        self.bytes = 0
        self.samples = []

    def configure(self, value):
        self.policy = policy(value)
        self.relay.max_rooms = self.policy['max_rooms']

    def limit(self):
        p = self.policy
        if p['bandwidth_mb_s'] == 0:
            return None
        # 35% default spare capacity covers joins, chunk bursts and shared usage.
        # A further 15% allowance covers encryption and packet overhead.
        return math.floor(p['bandwidth_mb_s'] * (1 - p['reserve_percent'] / 100) /
                          (p['per_player_mb_s'] * 1.15))

    def record(self, length):
        self.bytes += length
        now = time.monotonic()
        if self.samples and int(self.samples[-1][0]) == int(now):
            self.samples[-1] = (now, self.samples[-1][1] + length)
        else:
            self.samples.append((now, length))
        self.samples = self.samples[-121:]

    def visitors(self):
        return sum(p.authenticated and p.role == 'client' and p.room in self.relay.rooms
                   and not p.conn.is_closed() for p in self.relay.peers)

    def members(self, room):
        return sum(p.authenticated and p.room == room and p.role in ('host', 'client')
                   and not p.conn.is_closed() for p in self.relay.peers)

    def room_limit(self, room):
        limit = self.limit()
        return min(room.capacity, self.policy['room_capacity'], limit + 1 if limit is not None else 1000)

    def can_join(self, key, exclude=None):
        room = self.relay.rooms.get(key)
        if not room:
            return False
        own = bool(exclude and exclude.authenticated and exclude.role == 'client' and exclude.room == key)
        limit = self.limit()
        return (self.members(key) - own < self.room_limit(room) and
                (limit is None or self.visitors() - own < limit))

    def snapshot(self):
        p = self.policy
        now = time.monotonic()
        recent = sum(length for when, length in self.samples if when >= now - 10)
        rate = recent / max(1, min(10, now - self.started)) / 1000000
        limit = self.limit()
        return dict(p, recommended_visitors=limit, admitted_visitors=self.visitors(),
                    remaining_visitors=max(0, limit - self.visitors()) if limit is not None else None,
                    payload_mb_s=round(rate, 4), forwarded_bytes=self.bytes, overhead_percent=15,
                    basis='operator_budget', unit='MB/s', bytes_per_mb=1000000)
