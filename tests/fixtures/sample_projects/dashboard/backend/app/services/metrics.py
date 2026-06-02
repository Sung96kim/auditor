"""Metric collection and rollups."""

import asyncio
import time

import requests


class MetricsCollector:
    def __init__(self):
        self._client = None

    def client(self):
        if self._client is None:
            self._client = requests.Session()
        return self._client

    async def collect(self, services):
        results = []
        for svc in services:
            results.append(await self._one(svc))
        time.sleep(0.05)
        return results

    async def _one(self, svc):
        return requests.get(f"http://metrics.internal/{svc}").json()

    async def ping(self):
        return "ok"

    async def schedule(self, services):
        asyncio.create_task(self.collect(services))
        self.collect(services)


def rollup_kib(values):
    total = sum(values)
    return round(total / 1024, 2)


def rollup_mib(values):
    total = sum(values)
    return round(total / 1048576, 2)


def summarize(rows):
    out = {}
    for row in rows:
        out[row["key"]] = row["value"]
    return out
