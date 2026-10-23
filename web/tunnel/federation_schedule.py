"""Fixed gossip budgets and fair, jittered retries independent of member count."""
import random

INTERVAL = 1.0
FANOUT = 3
EXCHANGE_TIMEOUT = 4
RETRY_MAX = 60


class Schedule:
    def __init__(self, rng=None):
        self.rng = rng or random.Random()
        self.attempted, self.retry_at, self.failures = {}, {}, {}

    def select(self, members, inflight, healthy, now, *, immediate=False):
        available = FANOUT - len(inflight)
        if available <= 0:
            return []
        due = [node for node in members if node not in inflight and
               self.retry_at.get(node, 0) <= now and
               (immediate or now - self.attempted.get(node, -1e9) >= INTERVAL * .75)]
        # Reserve capacity for both established links and discovery. A dead
        # seed must not occupy every slot or monopolize reconnection attempts.
        self.rng.shuffle(due)
        due.sort(key=lambda node: self.attempted.get(node, -1e9))
        established = [node for node in due if node in healthy]
        discovery = [node for node in due if node not in healthy]
        selected = established[:min(2, available)]
        if discovery and len(selected) < available:
            selected.append(discovery[0])
        selected += [node for node in due if node not in selected][:available - len(selected)]
        for node in selected:
            self.attempted[node] = now
        return selected

    def success(self, node):
        self.failures.pop(node, None)
        self.retry_at.pop(node, None)

    def failure(self, node, now):
        failures = min(6, self.failures.get(node, 0) + 1)
        self.failures[node] = failures
        delay = min(RETRY_MAX, 2 ** failures)
        self.retry_at[node] = now + self.rng.uniform(delay * .8, delay)

    def delay(self):
        return self.rng.uniform(INTERVAL * .8, INTERVAL * 1.2)
