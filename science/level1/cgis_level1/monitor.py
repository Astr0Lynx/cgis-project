"""Peak-RSS / disk / time measurement for the process tree, with an enforced memory budget."""

from __future__ import annotations

import os
import shutil
import threading
import time
from pathlib import Path

import psutil


class ResourceMonitor:
    def __init__(self, budget_gb: float, interval: float = 0.25, watch_dir: Path | None = None):
        self.budget = budget_gb * 1024 ** 3
        self.interval = interval
        self.watch_dir = watch_dir
        self.peak_rss = 0
        self.peak_children = 0
        self.min_free_disk = None
        self.exceeded = False
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._run, daemon=True)
        self.t0 = time.time()
        self.cpu0 = psutil.Process().cpu_times()

    def _sample(self) -> int:
        me = psutil.Process(os.getpid())
        total = 0
        procs = [me]
        try:
            procs += me.children(recursive=True)
        except psutil.Error:
            pass
        for p in procs:
            try:
                total += p.memory_info().rss
            except psutil.Error:
                pass
        self.peak_children = max(self.peak_children, len(procs) - 1)
        return total

    def _run(self) -> None:
        while not self._stop.is_set():
            rss = self._sample()
            self.peak_rss = max(self.peak_rss, rss)
            if rss > self.budget:
                self.exceeded = True
            if self.watch_dir is not None and self.watch_dir.exists():
                free = shutil.disk_usage(self.watch_dir).free
                self.min_free_disk = free if self.min_free_disk is None else min(self.min_free_disk, free)
            self._stop.wait(self.interval)

    def __enter__(self) -> "ResourceMonitor":
        self._t.start()
        return self

    def __exit__(self, *exc) -> None:
        self._stop.set()
        self._t.join()

    def report(self) -> dict:
        return {
            "elapsed_s": round(time.time() - self.t0, 2),
            "peak_rss_bytes": self.peak_rss,
            "peak_rss_gib": round(self.peak_rss / 1024 ** 3, 3),
            "rss_budget_gib": round(self.budget / 1024 ** 3, 2),
            "budget_exceeded": self.exceeded,
            "max_worker_processes": self.peak_children,
            "min_free_disk_gib": None if self.min_free_disk is None else round(self.min_free_disk / 1024 ** 3, 2),
            "sampling_interval_s": self.interval,
        }
