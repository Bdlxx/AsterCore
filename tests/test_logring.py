# 环形日志缓冲 测试
import logging
import unittest

from astercore.core.logring import LogRing, _LEVEL_MAP


class LogRingTest(unittest.TestCase):
    def test_recent_and_capacity(self):
        ring = LogRing(capacity=50)
        for i in range(60):
            ring.add("info", "test", f"msg-{i}")
        self.assertEqual(len(ring), 50)  # 环形截断
        self.assertEqual(ring.recent(1)[0]["msg"], "msg-59")
        self.assertEqual(len(ring.recent(10)), 10)

    def test_tail_since(self):
        ring = LogRing()
        ring.add("info", "a", "x", ts=100.0)
        ring.add("warn", "b", "y", ts=200.0)
        newer = ring.tail_since(150.0)
        self.assertEqual(len(newer), 1)
        self.assertEqual(newer[0]["source"], "b")
        self.assertEqual(newer[0]["level"], "warn")

    def test_subscribe(self):
        ring = LogRing()
        got = []
        ring.subscribe(lambda e: got.append(e["msg"]))
        ring.add("info", "a", "hello")
        self.assertEqual(got, ["hello"])

    def test_handler(self):
        ring = LogRing()
        h = logging.Handler.__new__(type("H", (object,), {}))  # 占位不可用，走真实 handler
        from astercore.core.logring import RingHandler
        rh = RingHandler(ring)
        logger = logging.getLogger("astercore.test.logring")
        logger.addHandler(rh)
        logger.setLevel(logging.DEBUG)
        logger.info("一条日志 %d", 42)
        logger.removeHandler(rh)
        self.assertTrue(any(e["msg"] == "一条日志 42" for e in ring.recent(10)))
        self.assertEqual(_LEVEL_MAP[logging.INFO], "info")


if __name__ == "__main__":
    unittest.main()
