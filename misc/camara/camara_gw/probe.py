"""Active measurements towards a UE over the N6 side (ogstun): ICMP (latency/jitter/loss) and
optional iperf3 (throughput; needs `iperf3 -s` on the UE host and iperf3 on the VM).

The path probed (gateway/N6 -> UPF -> gNB -> UE) is the operator-controlled segment that the
CAMARA packet delay budget refers to ("one-way latency between the device and the gateway"),
so one-way delay is estimated as RTT/2.
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
from dataclasses import dataclass, field

from .config import Ci

_TIME_RE = re.compile(r"icmp_seq=(\d+).*time=([\d.]+) ms")


@dataclass
class PingResult:
    sent: int
    rtts_ms: list[float] = field(default_factory=list)

    @property
    def received(self) -> int:
        return len(self.rtts_ms)

    @property
    def loss_rate(self) -> float:
        return 1.0 - self.received / self.sent if self.sent else 1.0

    @property
    def one_way_ms(self) -> float | None:
        """Median RTT / 2."""
        if not self.rtts_ms:
            return None
        s = sorted(self.rtts_ms)
        mid = len(s) // 2
        med = s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) / 2
        return med / 2

    @property
    def jitter_ms(self) -> float | None:
        """p99 of |RTT(i) - RTT(i-1)| (round-trip delay variation)."""
        if len(self.rtts_ms) < 2:
            return None
        d = sorted(abs(b - a) for a, b in zip(self.rtts_ms, self.rtts_ms[1:]))
        return d[min(len(d) - 1, int(round(0.99 * (len(d) - 1))))]


class Prober:
    def __init__(self, cfg: Ci):
        self.cfg = cfg
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock(self, ip: str) -> asyncio.Lock:
        return self._locks.setdefault(ip, asyncio.Lock())

    async def ping(self, ip: str) -> PingResult:
        async with self._lock(ip):
            proc = await asyncio.create_subprocess_exec(
                "ping", "-n", "-c", str(self.cfg.ping_count), "-i", str(self.cfg.ping_interval), "-W", "1", ip,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            out, _ = await proc.communicate()
        rtts = [float(m.group(2)) for m in _TIME_RE.finditer(out.decode(errors="replace"))]
        return PingResult(sent=self.cfg.ping_count, rtts_ms=rtts)

    async def throughput_bps(self, ip: str) -> tuple[float | None, float | None]:
        """(downstream, upstream) bps from iperf3, or (None, None) if disabled/unavailable."""
        if not self.cfg.iperf_enabled or not shutil.which("iperf3"):
            return None, None

        async def run(reverse: bool) -> float | None:
            args = ["iperf3", "-c", ip, "-p", str(self.cfg.iperf_port), "-t", str(self.cfg.iperf_seconds), "-J"]
            if reverse:
                args.append("-R")
            proc = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE,
                                                        stderr=asyncio.subprocess.PIPE)
            out, _ = await proc.communicate()
            try:
                return float(json.loads(out)["end"]["sum_received"]["bits_per_second"])
            except (ValueError, KeyError, TypeError):
                return None

        async with self._lock(ip):
            down = await run(reverse=False)     # VM -> UE
            up = await run(reverse=True)        # UE -> VM
        return down, up
