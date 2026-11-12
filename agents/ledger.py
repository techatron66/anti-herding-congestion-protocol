"""Thread-safe, idempotent per-link lease ledger."""
from __future__ import annotations
import threading, time
class ReservationLedger:
    def __init__(self, capacity_mbps: float, threshold: float = 0.85):
        if capacity_mbps <= 0 or not 0 < threshold <= 1: raise ValueError("invalid capacity or threshold")
        self.capacity_mbps, self.threshold = float(capacity_mbps), float(threshold)
        self.pending, self.active = {}, {}
        self.lock = threading.RLock()
    def _expire(self, now):
        for key in [k for k,v in self.pending.items() if v["expires_at"] <= now]: del self.pending[key]
    def admit(self, request_id, flow_id, rate_mbps, measured_load_mbps, ttl_seconds, now=None):
        now = time.monotonic() if now is None else now
        if rate_mbps <= 0 or ttl_seconds <= 0: raise ValueError("rate and lease TTL must be positive")
        with self.lock:
            self._expire(now)
            existing = self.pending.get(request_id) or self.active.get(request_id)
            if existing: return {"granted": True, "idempotent": True, **existing}
            # Use committed intent rates as a floor while interface counters lag a newly installed route.
            # Once measured traffic rises, max(measured, active) avoids counting it twice.
            committed_baseline = sum(x["rate_mbps"] for x in self.active.values())
            baseline = max(max(0.0, measured_load_mbps), committed_baseline)
            projected = baseline + sum(x["rate_mbps"] for x in self.pending.values()) + rate_mbps
            if projected > self.capacity_mbps * self.threshold:
                return {"granted": False, "reason": "capacity", "projected_mbps": projected,
                        "utilization": projected / self.capacity_mbps}
            item = {"flow_id": flow_id, "rate_mbps": float(rate_mbps), "expires_at": now + ttl_seconds}
            self.pending[request_id] = item
            return {"granted": True, "idempotent": False, "projected_mbps": projected, **item}
    def commit(self, request_id, now=None):
        with self.lock:
            self._expire(time.monotonic() if now is None else now)
            item = self.pending.pop(request_id, None)
            if item is None: return request_id in self.active
            self.active[request_id] = item
            return True
    def cancel(self, request_id):
        with self.lock: return self.pending.pop(request_id, None) is not None
    def release(self, request_id):
        with self.lock:
            removed = self.active.pop(request_id, None)
            self.pending.pop(request_id, None)
            return removed is not None
    def snapshot(self, now=None):
        with self.lock:
            self._expire(time.monotonic() if now is None else now)
            return {"capacity_mbps": self.capacity_mbps, "threshold": self.threshold,
                    "pending_mbps": sum(x["rate_mbps"] for x in self.pending.values()),
                    "pending_count": len(self.pending), "active_count": len(self.active)}
