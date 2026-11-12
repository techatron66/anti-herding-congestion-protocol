from __future__ import annotations
import threading, unittest
from agents.ledger import ReservationLedger
from agents.source_agent import SourceRouterAgent
from agents.protocol import decode, encode, message
from routing.policies import HysteresisPolicy

class LedgerTests(unittest.TestCase):
    def test_concurrent_requests_are_admitted_atomically(self):
        ledger=ReservationLedger(10,.85); barrier=threading.Barrier(5); results=[]
        def request(index):
            barrier.wait(); results.append(ledger.admit(str(index),str(index),3,0,5)["granted"])
        threads=[threading.Thread(target=request,args=(i,)) for i in range(1,5)]
        for thread in threads: thread.start()
        barrier.wait()
        for thread in threads: thread.join()
        self.assertEqual(sum(results),2)
        self.assertEqual(ledger.snapshot()["pending_mbps"],6)

    def test_duplicate_intent_does_not_reserve_twice(self):
        ledger=ReservationLedger(10,.85)
        first=ledger.admit("same","f",3,0,5); duplicate=ledger.admit("same","f",3,0,5)
        self.assertTrue(first["granted"]); self.assertTrue(duplicate["idempotent"])
        self.assertEqual(ledger.snapshot()["pending_mbps"],3)

    def test_rejects_over_capacity_including_measured_and_pending(self):
        ledger=ReservationLedger(10,.85)
        ledger.admit("one","f1",3,0,5)
        answer=ledger.admit("two","f2",3,3,5)
        self.assertFalse(answer["granted"])

    def test_committed_reservations_cover_measurement_lag(self):
        ledger=ReservationLedger(10,.85)
        ledger.admit("one","f1",3,0,5,now=10); ledger.commit("one",now=10)
        ledger.admit("two","f2",3,0,5,now=10)
        answer=ledger.admit("three","f3",3,0,5,now=10)
        self.assertFalse(answer["granted"])

    def test_cancel_and_expiry_free_pending_capacity(self):
        ledger=ReservationLedger(10,.85)
        ledger.admit("cancel","f1",3,0,5,now=10)
        self.assertTrue(ledger.cancel("cancel"))
        ledger.admit("expired","f2",3,0,1,now=10)
        self.assertEqual(ledger.snapshot(now=12)["pending_count"],0)

    def test_commit_and_release_track_lifecycle(self):
        ledger=ReservationLedger(10,.85)
        ledger.admit("r","f",3,0,5,now=10)
        self.assertTrue(ledger.commit("r",now=11)); self.assertEqual(ledger.snapshot(now=11)["active_count"],1)
        self.assertTrue(ledger.release("r")); self.assertEqual(ledger.snapshot(now=11)["active_count"],0)

class TransactionTests(unittest.TestCase):
    def setUp(self):
        self.link={"link_id":"B","host":"127.0.0.1","port":9999}
        self.paths={"A":{"next_hop":"a","interface":"eth1"},
                    "B":{"next_hop":"b","interface":"eth2","admission_agents":[self.link]}}
    def agent(self, route_ok=True, reject=False):
        agent=SourceRouterAgent("flow","A",self.paths,3,"intent",.85,.1,route_command=lambda path:route_ok)
        events=[]
        def request(endpoint,kind,rid,**fields):
            events.append(kind)
            if kind=="INTENT" and reject: return {"type":"REJECT","reason":"capacity"}
            return {"type":"GRANT"}
        agent._request=request
        return agent,events
    def test_route_failure_rolls_back_committed_reservation(self):
        agent,events=self.agent(route_ok=False)
        result=agent.attempt({"A":.95,"B":.1})
        self.assertFalse(result["switched"]); self.assertEqual(result["reason"],"route_update_failed")
        self.assertEqual(events,["INTENT","COMMIT","RELEASE"])
        self.assertEqual(agent.current_path,"A")
    def test_rejected_intent_keeps_old_route(self):
        agent,events=self.agent(reject=True)
        result=agent.attempt({"A":.95,"B":.1})
        self.assertFalse(result["switched"]); self.assertEqual(agent.current_path,"A")
        self.assertEqual(events,["INTENT"])

class PolicyTests(unittest.TestCase):
    def test_hysteresis_requires_sustained_congestion_and_improvement(self):
        policy=HysteresisPolicy(.85,.1,3,2)
        self.assertFalse(policy.observe(.9,.2,0)["eligible"])
        self.assertFalse(policy.observe(.9,.2,.25)["eligible"])
        self.assertTrue(policy.observe(.9,.2,.5)["eligible"])
        policy.switched(.5)
        self.assertEqual(policy.observe(.95,.2,1)["reason"],"congestion_not_sustained")
        policy.observe(.95,.2,1.25)
        self.assertEqual(policy.observe(.95,.2,2)["reason"],"cooldown")
        self.assertTrue(policy.observe(.95,.2,2.6)["eligible"])

    def test_protocol_round_trip_and_version_validation(self):
        encoded=encode(message("INTENT",request_id="r"))
        self.assertEqual(decode(encoded)["request_id"],"r")
        with self.assertRaises(ValueError): decode(b'{"version":2,"type":"INTENT"}')

if __name__=="__main__": unittest.main()
