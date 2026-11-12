"""Run one reproducible Mininet herding or policy-comparison experiment."""
from __future__ import annotations
import argparse, json, os, pathlib, re, subprocess, sys, time, uuid

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from topology.mininet_topology import DEST, build_network
from routing.policies import HysteresisPolicy

def tx_bytes(node, interface):
    try:
        return int(node.cmd(f"cat /sys/class/net/{interface}/statistics/tx_bytes").strip())
    except (ValueError, OSError): return 0

def node_process(node, argv, log_path, env=None):
    log = open(log_path, "w", encoding="utf-8")
    proc = node.popen(argv, stdout=log, stderr=subprocess.STDOUT, env=env)
    proc._codex_log = log
    return proc

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--policy",choices=["static","reactive","hysteresis","intent"],required=True)
    parser.add_argument("--scenario",choices=["herd"],default="herd")
    parser.add_argument("--config",default=str(ROOT/"config/default.json"))
    args=parser.parse_args()
    if os.name != "posix" or os.geteuid() != 0:
        raise SystemExit("Mininet experiments require root on Linux; run this command with sudo in Ubuntu.")
    cfg=json.loads(pathlib.Path(args.config).read_text(encoding="utf-8"))
    run_id=time.strftime("%Y%m%d-%H%M%S")+"-"+uuid.uuid4().hex[:6]
    out=ROOT/cfg["results_dir"]/run_id; out.mkdir(parents=True,exist_ok=False)
    event_path=out/"events.jsonl"
    def log_event(**event):
        event={"timestamp":time.time(),"run_id":run_id,"policy":args.policy,**event}
        with event_path.open("a",encoding="utf-8") as f: f.write(json.dumps(event)+"\n")
    net=build_network(cfg["capacity_mbps"])
    processes=[]; clients=[]; probes=[]
    try:
        # Record route selection and prove both alternatives before sending load.
        for i in range(1,5):
            router=net.get(f"r{i}")
            for path,via,dev in [("A",f"10.0.{i}.2",f"r{i}-eth1"),("B",f"10.1.{i}.2",f"r{i}-eth2")]:
                router.cmd(f"ip route replace {DEST}/32 via {via} dev {dev}")
                response=net.get(f"h{i}").cmd(f"ping -c 1 -W 2 {DEST}")
                if "1 received" not in response: raise RuntimeError(f"h{i} cannot reach destination via path {path}")
                log_event(type="connectivity",source=f"h{i}",path=path,reachable=True,
                          route=router.cmd(f"ip route get {DEST}").strip())
            router.cmd(f"ip route replace {DEST}/32 via 10.0.{i}.2 dev r{i}-eth1")
        counters={"A":tx_bytes(net.get("pa"),"pa-eth4"),"B":tx_bytes(net.get("pb"),"pb-eth4")}
        server=net.get("dst")
        for i in range(1,5):
            port=cfg["udp_port_base"]+i
            proc=node_process(server,["iperf3","-s","-B",DEST,"-p",str(port)],out/f"server-{i}.log")
            processes.append(proc)
        time.sleep(1)
        for i in range(1,5):
            probes.append(node_process(net.get(f"h{i}"),["ping","-n","-i","1",DEST],out/f"ping-h{i}.log"))
        if args.policy=="intent":
            env=os.environ.copy(); env["PYTHONPATH"]=str(ROOT)
            for path,node,addr,interface in [("A",net.get("pa"),"172.30.0.2","pa-eth4"),("B",net.get("pb"),"172.30.0.3","pb-eth4")]:
                proc=node_process(node,[sys.executable,"-m","agents.admission_agent","--host",addr,
                    "--port","7301","--link-id",path,"--capacity",str(cfg["capacity_mbps"]),
                    "--threshold",str(cfg["admission_threshold"]),"--ttl",str(cfg["intent_ttl_seconds"]),
                    "--interface",interface],out/f"admission-{path}.jsonl",env)
                processes.append(proc)
            time.sleep(.3)
        for i in range(1,5):
            host=net.get(f"h{i}"); port=cfg["udp_port_base"]+i
            clients.append(node_process(host,["iperf3","-c",DEST,"-p",str(port),"-u","-b",
                f"{cfg['offered_rate_mbps']}M","-t",str(cfg["duration_seconds"]),"-J"],out/f"flow-h{i}.json"))
        start=time.monotonic(); previous=counters; previous_time=start
        samples={"A":[],"B":[]}; moved=False; above_seconds=0; sample_count=0; route_changes=[]; decisions=[]
        consecutive_a_congested=0
        trigger_at=cfg["route_change_delay_seconds"]
        hysteresis=HysteresisPolicy(cfg["congestion_threshold"],cfg["minimum_improvement"],
            cfg["congestion_consecutive_samples"],cfg["cooldown_seconds"])
        while time.monotonic()-start < cfg["duration_seconds"]:
            time.sleep(cfg["sample_interval_seconds"])
            now=time.monotonic(); elapsed=now-start; current={"A":tx_bytes(net.get("pa"),"pa-eth4"),
                                                              "B":tx_bytes(net.get("pb"),"pb-eth4")}
            dt=max(.001,now-previous_time)
            loads={path:max(0,(current[path]-previous[path])*8/1_000_000/dt) for path in ("A","B")}
            utilization={path:loads[path]/cfg["capacity_mbps"] for path in loads}
            for path in samples: samples[path].append(utilization[path])
            overloaded=any(value>=cfg["congestion_threshold"] for value in utilization.values())
            above_seconds+=dt if overloaded else 0
            consecutive_a_congested = consecutive_a_congested + 1 if utilization["A"] >= cfg["congestion_threshold"] else 0
            hysteresis_decision=hysteresis.observe(utilization["A"],utilization["B"],elapsed)
            log_event(type="link_sample",elapsed_seconds=elapsed,load_mbps=loads,utilization=utilization)
            previous,previous_time=current,now; sample_count+=1
            if not moved and args.policy!="static" and elapsed>=trigger_at:
                eligible=(consecutive_a_congested>=cfg["congestion_consecutive_samples"] if args.policy=="reactive"
                          else hysteresis_decision["eligible"])
                if eligible:
                    paths={}
                    for i in range(1,5):
                        paths["A"]={"next_hop":f"10.0.{i}.2","interface":f"r{i}-eth1",
                            "admission_agents":[{"host":"172.30.0.2","port":7301,"link_id":"A","ttl_seconds":cfg["intent_ttl_seconds"]}]}
                        paths["B"]={"next_hop":f"10.1.{i}.2","interface":f"r{i}-eth2",
                            "admission_agents":[{"host":"172.30.0.3","port":7301,"link_id":"B","ttl_seconds":cfg["intent_ttl_seconds"]}]}
                        router=net.get(f"r{i}")
                        cmd=[sys.executable,"-m","agents.source_agent","--flow-id",f"h{i}","--current-path","A",
                            "--candidate-paths",json.dumps(paths,separators=(",",":")),"--utilization",
                            json.dumps(utilization,separators=(",",":")),"--rate",str(cfg["offered_rate_mbps"]),
                            "--policy","reactive" if args.policy=="reactive" else args.policy,
                            "--threshold",str(cfg["congestion_threshold"]),"--improvement",str(cfg["minimum_improvement"])]
                        env=os.environ.copy(); env["PYTHONPATH"]=str(ROOT)
                        p=node_process(router,cmd,out/f"source-agent-h{i}.jsonl",env); route_changes.append((i,p))
                    results=[]
                    for i,p in route_changes:
                        code=p.wait(timeout=5); p._codex_log.close()
                        record=json.loads((out/f"source-agent-h{i}.jsonl").read_text().strip())
                        results.append(record); log_event(type="route_decision",source=f"h{i}",**record)
                    decisions.extend(results)
                    if any(item.get("switched") for item in results): hysteresis.switched(elapsed)
                    moved=True
            if all(p.poll() is not None for p in clients): break
        for i,p in enumerate(clients,1):
            if p.poll() is None: p.wait(timeout=10)
            p._codex_log.close()
        # Flows have ended; release their committed capacity leases explicitly.
        for decision in decisions:
            if not decision.get("switched") or not decision.get("request_id"): continue
            for link_id in decision.get("granted_links",[]):
                endpoint={"A":"172.30.0.2","B":"172.30.0.3"}[link_id]
                try:
                    source_router=net.get("r"+decision["flow_id"][1:])
                    code=("from agents.protocol import decode,encode,message; import socket,sys; "
                          "s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM); s.settimeout(1); "
                          f"s.sendto(encode(message('RELEASE',request_id='{decision['request_id']}',flow_id='{decision['flow_id']}')),('{endpoint}',7301)); "
                          "print(decode(s.recvfrom(65535)[0]).get('phase'))")
                    env=os.environ.copy(); env["PYTHONPATH"]=str(ROOT)
                    release=source_router.popen([sys.executable,"-c",code],stdout=subprocess.PIPE,
                                                stderr=subprocess.PIPE,text=True,env=env)
                    response,_=release.communicate(timeout=3)
                    if release.returncode: raise RuntimeError(response.strip() or "release command failed")
                    log_event(type="reservation_release",flow_id=decision["flow_id"],link_id=link_id,
                              response=response.strip())
                except (OSError,ValueError,subprocess.TimeoutExpired,RuntimeError) as exc:
                    log_event(type="reservation_release_error",flow_id=decision.get("flow_id"),link_id=link_id,reason=str(exc))
        for p in processes:
            if p.poll() is None: p.terminate()
            try: p.wait(timeout=2)
            except subprocess.TimeoutExpired: p.kill()
            p._codex_log.close()
        ping_rtt={}
        for i,p in enumerate(probes,1):
            if p.poll() is None: p.terminate()
            try: p.wait(timeout=2)
            except subprocess.TimeoutExpired: p.kill()
            p._codex_log.close()
            output=(out/f"ping-h{i}.log").read_text(encoding="utf-8",errors="replace")
            values=[float(value) for value in re.findall(r"time[=<]([0-9.]+)\s*ms",output)]
            ping_rtt[f"h{i}"]={"samples":len(values),"mean_ms":sum(values)/len(values) if values else None,
                                 "p95_ms":sorted(values)[max(0,int(.95*len(values)+.999)-1)] if values else None}
        flow_results=[]
        for i in range(1,5):
            data=json.loads((out/f"flow-h{i}.json").read_text(encoding="utf-8"))
            end=data.get("end",{}).get("sum_received",data.get("end",{}).get("sum",{}))
            flow_results.append({"source":f"h{i}","throughput_mbps":end.get("bits_per_second",0)/1e6,
                "lost_packets":end.get("lost_packets",0),"packets":end.get("packets",0),
                "loss_percent":end.get("lost_percent",0)})
        final={"run_id":run_id,"policy":args.policy,"scenario":args.scenario,"offered_per_source_mbps":cfg["offered_rate_mbps"],
            "aggregate_offered_mbps":4*cfg["offered_rate_mbps"],"path_capacity_mbps":cfg["capacity_mbps"],
            "congestion_reproduced_by_offered_load":4*cfg["offered_rate_mbps"]>cfg["capacity_mbps"],
            "time_above_threshold_seconds":above_seconds,"max_sampled_utilization":{k:max(v,default=0) for k,v in samples.items()},
            "route_change_attempted":moved,"successful_route_changes":sum(bool(x.get("switched")) for x in decisions),
            "route_decisions":decisions,"flows":flow_results,"ping_rtt":ping_rtt,"connectivity_checks":8,
            "results_dir":str(out.relative_to(ROOT))}
        (out/"summary.json").write_text(json.dumps(final,indent=2),encoding="utf-8")
        print(json.dumps(final,indent=2))
    finally:
        for p in processes+clients+probes:
            if p.poll() is None: p.terminate()
        net.stop()
if __name__=="__main__": main()
