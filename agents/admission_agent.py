"""UDP admission server; each instance owns only one link's capacity ledger."""
from __future__ import annotations
import argparse, json, socket, threading, time
from agents.ledger import ReservationLedger
from agents.protocol import decode, encode, message
class AdmissionServer:
    def __init__(self, host, port, link_id, capacity, threshold, ttl, interface=None):
        self.host, self.port, self.link_id, self.ttl, self.interface = host, port, link_id, ttl, interface
        self.ledger = ReservationLedger(capacity, threshold)
        self.log_lock = threading.Lock()
    def measured_load_mbps(self):
        if not self.interface: return 0.0
        path = f"/sys/class/net/{self.interface}/statistics/tx_bytes"
        try:
            with open(path, encoding="ascii") as f: first = int(f.read().strip())
            time.sleep(0.1)
            with open(path, encoding="ascii") as f: second = int(f.read().strip())
            return max(0.0, (second-first)*8/100_000)
        except (OSError, ValueError): return 0.0
    def log(self, event):
        with self.log_lock: print(json.dumps({"timestamp": time.time(), "link_id": self.link_id, **event}), flush=True)
    def handle(self, packet, address, sock):
        try:
            req = decode(packet); kind, rid = req["type"], req["request_id"]
            if kind == "INTENT":
                measured = self.measured_load_mbps()
                result = self.ledger.admit(rid, req["flow_id"], float(req["requested_rate_mbps"]), measured, self.ttl)
                if result["granted"]:
                    response = message("GRANT", request_id=rid, link_id=self.link_id,
                        expires_at=time.time()+self.ttl, measured_load_mbps=measured,
                        projected_mbps=result.get("projected_mbps", measured))
                else:
                    response = message("REJECT", request_id=rid, link_id=self.link_id,
                        reason=result["reason"], projected_mbps=result["projected_mbps"], utilization=result["utilization"])
            elif kind == "COMMIT":
                ok = self.ledger.commit(rid)
                response = message("GRANT" if ok else "REJECT", request_id=rid, link_id=self.link_id,
                                   phase="committed" if ok else "unknown_request")
            elif kind == "CANCEL":
                self.ledger.cancel(rid); response = message("GRANT", request_id=rid, link_id=self.link_id, phase="cancelled")
            elif kind == "RELEASE":
                self.ledger.release(rid); response = message("GRANT", request_id=rid, link_id=self.link_id, phase="released")
            else: raise ValueError(f"unexpected request type {kind}")
            self.log({"type": kind, "request_id": rid, "response": response})
        except Exception as exc:
            response = message("REJECT", request_id="invalid", link_id=self.link_id, reason=f"invalid_request:{exc}")
        sock.sendto(encode(response), address)
    def serve(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); sock.bind((self.host, self.port))
        self.log({"type": "ready", "address": [self.host, self.port]})
        while True:
            packet, address = sock.recvfrom(65535)
            threading.Thread(target=self.handle, args=(packet,address,sock), daemon=True).start()
def main():
    p=argparse.ArgumentParser(); p.add_argument('--host',required=True); p.add_argument('--port',type=int,required=True)
    p.add_argument('--link-id',required=True); p.add_argument('--capacity',type=float,required=True)
    p.add_argument('--threshold',type=float,default=.85); p.add_argument('--ttl',type=float,default=2.)
    p.add_argument('--interface'); a=p.parse_args()
    AdmissionServer(a.host,a.port,a.link_id,a.capacity,a.threshold,a.ttl,a.interface).serve()
if __name__ == '__main__': main()
