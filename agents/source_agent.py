"""Local source-router policy and reservation transaction client."""
from __future__ import annotations
import argparse, json, socket, subprocess, time
from agents.protocol import decode, encode, message, request_id

class SourceRouterAgent:
    def __init__(self, flow_id, current_path, candidate_paths, offered_rate_mbps,
                 policy="intent", admission_threshold=.85, minimum_improvement=.1,
                 route_command=None, timeout=1.0):
        self.flow_id, self.current_path, self.candidate_paths = flow_id, current_path, candidate_paths
        self.offered_rate_mbps, self.policy = offered_rate_mbps, policy
        self.admission_threshold, self.minimum_improvement = admission_threshold, minimum_improvement
        self.route_command = route_command or self._replace_route
        self.timeout = timeout
    def _request(self, endpoint, kind, rid, **fields):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); sock.settimeout(self.timeout)
        try:
            sock.sendto(encode(message(kind, request_id=rid, flow_id=self.flow_id, **fields)),
                        (endpoint["host"], int(endpoint["port"])))
            response, _ = sock.recvfrom(65535)
            return decode(response)
        finally: sock.close()
    def _replace_route(self, path):
        proc = subprocess.run(["ip", "route", "replace", "10.255.0.1/32", "via", path["next_hop"],
                               "dev", path["interface"]], capture_output=True, text=True)
        if proc.returncode: return False
        check = subprocess.run(["ip", "route", "get", "10.255.0.1"], capture_output=True, text=True)
        return check.returncode == 0 and path["next_hop"] in check.stdout
    def _cleanup(self, rid, links, committed=()):
        committed = set(committed)
        for link in links:
            try: self._request(link, "RELEASE" if link["link_id"] in committed else "CANCEL", rid)
            except (OSError, ValueError): pass
    def attempt(self, utilization_by_path, now=None):
        now = time.monotonic() if now is None else now
        current = self.candidate_paths[self.current_path]
        candidates = sorted((name, float(utilization_by_path.get(name, 1.0)))
                            for name in self.candidate_paths if name != self.current_path)
        if not candidates: return {"switched": False, "reason": "no_alternative"}
        candidate_name, candidate_util = min(candidates, key=lambda x: x[1])
        current_util = float(utilization_by_path.get(self.current_path, 0.0))
        if current_util < self.admission_threshold:
            return {"switched": False, "reason": "below_congestion_threshold"}
        if self.policy in ("hysteresis", "intent") and current_util - candidate_util < self.minimum_improvement:
            return {"switched": False, "reason": "insufficient_improvement"}
        candidate = self.candidate_paths[candidate_name]
        if self.policy != "intent":
            ok = bool(self.route_command(candidate))
            if ok: self.current_path = candidate_name
            return {"switched": ok, "path": candidate_name if ok else self.current_path,
                    "reason": "route_updated" if ok else "route_update_failed"}
        rid, granted = request_id(), []
        links = candidate.get("admission_agents", [])
        if not links: return {"switched": False, "reason": "no_admission_agents"}
        try:
            for link in links:
                response = self._request(link, "INTENT", rid, source_router=self.flow_id,
                    candidate_path=candidate_name, requested_rate_mbps=self.offered_rate_mbps,
                    expires_at=time.time()+link.get("ttl_seconds", 2), link_id=link["link_id"])
                if response["type"] != "GRANT":
                    self._cleanup(rid, granted)
                    return {"switched": False, "reason": response.get("reason", "admission_rejected"),
                            "rejected_link": link["link_id"]}
                granted.append(link)
            committed = []
            for link in granted:
                response = self._request(link, "COMMIT", rid, link_id=link["link_id"])
                if response["type"] != "GRANT":
                    self._cleanup(rid, granted, committed)
                    return {"switched": False, "reason": "commit_failed", "committed_links": committed}
                committed.append(link["link_id"])
            if not self.route_command(candidate):
                self._cleanup(rid, granted, committed)
                return {"switched": False, "reason": "route_update_failed"}
            self.current_path = candidate_name
            return {"switched": True, "path": candidate_name, "reason": "committed",
                    "request_id": rid, "granted_links": [x["link_id"] for x in granted]}
        except (OSError, ValueError, KeyError) as exc:
            self._cleanup(rid, granted)
            return {"switched": False, "reason": f"control_error:{exc}"}

def main():
    p=argparse.ArgumentParser(); p.add_argument('--flow-id',required=True); p.add_argument('--current-path',required=True)
    p.add_argument('--candidate-paths',required=True); p.add_argument('--utilization',required=True)
    p.add_argument('--rate',type=float,required=True); p.add_argument('--policy',choices=['reactive','hysteresis','intent'],required=True)
    p.add_argument('--threshold',type=float,default=.85); p.add_argument('--improvement',type=float,default=.1)
    a=p.parse_args(); paths=json.loads(a.candidate_paths); utilization=json.loads(a.utilization)
    agent=SourceRouterAgent(a.flow_id,a.current_path,paths,a.rate,a.policy,a.threshold,a.improvement)
    result=agent.attempt(utilization); print(json.dumps({"timestamp":time.time(),"flow_id":a.flow_id,**result}),flush=True)
    raise SystemExit(0 if result.get('switched') or result.get('reason') in ('below_congestion_threshold','insufficient_improvement') else 2)
if __name__ == '__main__': main()
