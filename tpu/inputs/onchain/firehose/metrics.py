import time


class _FirehoseMetrics:
    def __init__(self):
        self.reset()

    def reset(self):
        self.last_second = int(time.time())
        self._count_this_second = 0
        self.tps = 0.0
        self.total_trades = 0

    def on_trade(self, trade):
        now = int(time.time())
        if now != self.last_second:
            self.tps = self._count_this_second
            self._count_this_second = 0
            self.last_second = now
        self._count_this_second += 1
        self.total_trades += 1

firehose_metrics = _FirehoseMetrics()
