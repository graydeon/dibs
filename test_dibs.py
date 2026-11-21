#!/usr/bin/env python3
"""Unit tests for dibs scheduler logic. No daemon, no systemd: everything mocked."""
import asyncio
import importlib.machinery
import json
import os
import tempfile
import unittest
from unittest import mock

_here = os.path.dirname(os.path.abspath(__file__))
dibs = importlib.machinery.SourceFileLoader("dibs", os.path.join(_here, "dibs")).load_module()

GiB = 1 << 30


class W:  # fake asyncio StreamWriter
    def __init__(self):
        self.msgs = []

    def write(self, b):
        self.msgs.append(b)

    async def drain(self):
        pass

    def close(self):
        pass

    def text(self):
        return b"".join(self.msgs).decode()


class SchedTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.now = 1_000_000.0
        self.avail = 64 * GiB
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ctl = mock.Mock(return_value=True)
        for p in (mock.patch.object(dibs.time, "time", lambda: self.now),
                  mock.patch.multiple(dibs, CPU_CAP=4, MEM_RESERVE=6 * GiB, SHORT_MAX=900, AGE_PROMOTE=1800,
                                      FREEZE_AFTER=20, FREEZE_BUDGET=3600, mem_available=lambda: self.avail,
                                      user_systemctl=self.ctl, LOG=os.path.join(self.tmp.name, "jobs.jsonl"))):
            p.start()
            self.addCleanup(p.stop)
        self.sched = dibs.Sched()

    def add(self, cpus, est=60, long=False, mem=GiB, state="queued", started=None):
        req = {"cpus": cpus, "mem": mem, "est": est, "long": long,
               "label": "t", "cmd": "true", "cwd": "/tmp"}
        j = dibs.Job(self.sched.next_id, req, W())
        self.sched.next_id += 1
        j.state, j.started = state, started
        self.sched.jobs[j.id] = j
        return j

    async def test_1_short_before_long(self):
        a = self.add(4, state="running", started=self.now)
        l = self.add(4, est=3600)
        self.now += 10
        s = self.add(4, est=60)
        del self.sched.jobs[a.id]
        self.assertTrue(l.long)
        await self.sched.tick()
        self.assertEqual(s.state, "granted")
        self.assertEqual(l.state, "queued")

    async def test_2_backfill(self):
        self.add(2, state="running", started=self.now)
        h = self.add(4)
        self.now += 10
        b = self.add(2)
        await self.sched.tick()
        self.assertEqual(b.state, "granted")
        self.assertEqual(h.state, "queued")

    async def test_2b_head_waited_blocks_backfill(self):
        self.add(2, state="running", started=self.now)
        h = self.add(4)
        self.now += 601
        b = self.add(2)
        await self.sched.tick()
        self.assertEqual(b.state, "queued")
        self.assertEqual(h.state, "queued")

    async def test_3_long_promoted(self):
        a = self.add(4, state="running", started=self.now)
        l = self.add(4, est=3600)
        self.now += 10
        s = self.add(4)
        self.assertFalse(l.short_now())
        self.now += dibs.AGE_PROMOTE + 1
        self.assertTrue(l.short_now())
        del self.sched.jobs[a.id]
        await self.sched.tick()
        self.assertEqual(l.state, "granted")
        self.assertEqual(s.state, "queued")

    async def test_4_overrun(self):
        j = self.add(2, est=60, state="running", started=self.now)
        self.now += 60 * 1.5 + 121
        await self.sched.tick()
        self.assertTrue(j.long)
        self.assertIn("long lane", j.writer.text())

    async def test_5_freeze_newest_long(self):
        l1 = self.add(2, est=3600, state="running", started=self.now)
        self.now += 10
        l2 = self.add(2, est=3600, state="running", started=self.now)
        self.now += 10
        s = self.add(2)
        self.now += dibs.FREEZE_AFTER + 1
        await self.sched.tick()
        self.assertEqual(l2.state, "frozen")
        self.assertEqual(l1.state, "running")
        self.assertIn(("freeze", f"dibs-{l2.id}.scope"), [c.args for c in self.ctl.mock_calls])
        await self.sched.tick()
        self.assertEqual(s.state, "granted")
        self.assertEqual(l2.state, "frozen")
        s.state = "done"
        del self.sched.jobs[s.id]
        await self.sched.tick()
        self.assertEqual(l2.state, "running")

    async def test_6_freeze_budget(self):
        self.add(2, est=3600, state="running", started=self.now)
        l2 = self.add(2, est=3600, state="frozen", started=self.now)
        l2.frozen_since = self.now
        self.add(2)
        self.now += dibs.FREEZE_AFTER + 1  # short job now "stuck"; cap still full
        await self.sched.tick()
        self.assertEqual(l2.state, "frozen")  # sanity: still within budget
        self.now += dibs.FREEZE_BUDGET + 1
        await self.sched.tick()
        self.assertEqual(l2.state, "running")  # thawed, not refrozen in same tick

    async def test_7_mem_reserve(self):
        self.avail = 8 * GiB
        big = self.add(1, mem=4 * GiB)
        small = self.add(1, mem=1 * GiB)
        await self.sched.tick()
        self.assertEqual(big.state, "queued")
        self.assertEqual(small.state, "granted")

    async def test_9_done_frozen_cleanup(self):
        r = asyncio.StreamReader()
        req = {"op": "submit", "cmd": "sleep 9", "cwd": "/tmp", "cpus": 1, "mem": GiB,
               "est": 60, "long": False, "label": "t"}
        r.feed_data((json.dumps(req) + "\n").encode())
        r.feed_data(b'{"op":"started"}\n')
        task = asyncio.ensure_future(self.sched.client(r, W()))
        for _ in range(50):
            await asyncio.sleep(0.01)
            if self.sched.jobs and all(j.state == "running" for j in self.sched.jobs.values()):
                break
        (j,) = self.sched.jobs.values()
        j.state, j.frozen_since = "frozen", self.now  # CMD died while frozen
        r.feed_data(b'{"op":"done","code":-15}\n')
        r.feed_eof()
        await asyncio.wait_for(task, 5)
        calls = [c.args for c in self.ctl.mock_calls]
        self.assertIn(("thaw", f"dibs-{j.id}.scope"), calls)
        self.assertIn(("stop", "--no-block", f"dibs-{j.id}.scope"), calls)
        self.assertNotIn(j.id, self.sched.jobs)

    def test_8_config(self):
        cfg = os.path.join(self.tmp.name, "config.toml")
        with open(cfg, "w") as f:
            f.write("cpu_cap = 0\n")
        with mock.patch.object(dibs, "CONFIG", cfg):
            with self.assertRaises(SystemExit) as cm:
                dibs.load_config()
            self.assertIn("cpu_cap", str(cm.exception))
        with open(cfg, "w") as f:
            f.write('cpu_cap = 3\nmem_reserve = "2G"\nfreeze_after = "5s"\n')
        with mock.patch.object(dibs, "CONFIG", cfg):
            dibs.load_config()
        self.assertEqual(dibs.CPU_CAP, 3)
        self.assertEqual(dibs.MEM_RESERVE, 2 * GiB)
        self.assertEqual(dibs.FREEZE_AFTER, 5)

    def test_11_no_bus_exits(self):
        with mock.patch.object(dibs.os.path, "exists", return_value=False):
            with self.assertRaises(SystemExit) as cm:
                asyncio.run(dibs.daemon())
        self.assertIn("dbus-user-session", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
