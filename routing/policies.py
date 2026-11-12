"""Congestion trigger and hysteresis/cooldown state used by source agents."""
from __future__ import annotations

class HysteresisPolicy:
    def __init__(self, congestion_threshold=.85, minimum_improvement=.1,
                 consecutive_samples=3, cooldown_seconds=2):
        if consecutive_samples < 1 or cooldown_seconds < 0:
            raise ValueError("invalid trigger or cooldown")
        self.threshold=congestion_threshold
        self.minimum_improvement=minimum_improvement
        self.consecutive_samples=consecutive_samples
        self.cooldown_seconds=cooldown_seconds
        self.congested_samples=0
        self.last_switch_at=None
    def observe(self, current_utilization, candidate_utilization, now):
        if current_utilization >= self.threshold:
            self.congested_samples += 1
        else:
            self.congested_samples=0
        if self.congested_samples < self.consecutive_samples:
            return {"eligible":False,"reason":"congestion_not_sustained"}
        if self.last_switch_at is not None and now-self.last_switch_at < self.cooldown_seconds:
            return {"eligible":False,"reason":"cooldown"}
        if current_utilization-candidate_utilization < self.minimum_improvement:
            return {"eligible":False,"reason":"insufficient_improvement"}
        return {"eligible":True,"reason":"hysteresis_satisfied"}
    def switched(self, now):
        self.last_switch_at=now
        self.congested_samples=0
