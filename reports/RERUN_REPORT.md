# Distributed Anti-Herding Routing

## Mininet rerun report

Run date: 4 November 2026  
Environment: Ubuntu 24.04 on WSL2, Mininet 2.3.0, Open vSwitch 3.3.9, iperf3 3.16

## Summary

The prototype now reproduces the congestion-herding failure before applying the reservation protocol. Four sources each offer 3 Mbps, for 12 Mbps total, while either shared path has a 10 Mbps bottleneck. When all sources react to congestion together, they overload the alternate path too. With reservation admission enabled, two sources move to Path B and two remain on A. Path B peaks at 62.73% utilization in this rerun.

This demonstrates that the transaction and admission logic work in the tested topology. It is one run per policy and does not establish a general performance advantage or statistical significance.

## Topology and Mininet behavior

Each source host h1–h4 connects to its own Linux router namespace r1–r4. Every source router has a route to the destination through either path router:

- Path A: r1–r4 → pa → dst, with the shaped 10 Mbps egress on pa–dst.
- Path B: r1–r4 → pb → dst, with the shaped 10 Mbps egress on pb–dst.
- Control network: r1–r4, pa, and pb use 172.30.0.0/24 through standalone OVS bridge s1. Admission UDP traffic uses port 7301 on this management LAN.

Linux forwarding happens inside Mininet namespaces. Mininet TCLink shapes the two bottleneck links. The experiment checks every source on each path, runs four iperf3 UDP clients, samples transmit byte counters at the path egresses, and runs separate ping probes for RTT. Source agents change Linux routes with `ip route replace` and verify the resulting route before reporting success.

## Policy comparison

All policies use the same 25 second workload: four 3 Mbps UDP flows, 10 Mbps per path, and an 85% congestion trigger. Reactive routing responds after three consecutive 250 ms samples. Hysteresis also requires a 10 percentage point utilization improvement and has a configured 2 second cooldown. Intent uses the same hysteresis trigger, then requests admission before changing a route.

| Policy | Run ID | Path A peak | Path B peak | Successful route changes |
| --- | --- | ---: | ---: | ---: |
| Static | `20261104-133650-eee789` | 101.01% | 0.02% | 0 |
| Reactive | `20261104-133729-1c664f` | 100.30% | 102.33% | 4 |
| Hysteresis | `20261104-133809-5f46b5` | 102.00% | 102.88% | 4 |
| Intent | `20261104-133850-a9d8a2` | 100.51% | 62.73% | 2 admitted, 2 rejected |

In the reactive and hysteresis runs, all four sources moved from A to B. Both bottlenecks then exceeded the configured capacity in sampled counters. In the intent run, each path kept two 3 Mbps flows. Aggregate received goodput was about 11.97 Mbps. Packet loss per flow ranged from 0.09% to 0.43% in that run.

## Protocol design

Control messages are versioned JSON datagrams with a unique request ID, flow ID, candidate path, requested rate, and link ID. The implemented messages are `INTENT`, `GRANT`, `REJECT`, `COMMIT`, `CANCEL`, and `RELEASE`.

1. A source router sends `INTENT` to each admission agent on the candidate path.
2. Each agent checks and inserts provisional state atomically under a lock.
3. The source sends `COMMIT` after every required agent grants. It changes its route only after successful commits.
4. A rejection or timeout cancels earlier provisional grants. A failed route update releases committed state and keeps the old route.
5. Flow completion sends `RELEASE`. Pending leases also expire by TTL.

Each admission agent owns only its local link capacity. It does not select routes or command other routers. The path interface accepts a list of agents for future multi-link paths. The current Mininet topology has one admission point per path.

### Admission accounting

Admission starts at 85% of 10 Mbps, giving an 8.5 Mbps threshold. To account for interface counter delay just after a route change, the implementation uses:

```text
projected = max(measured_load, committed_reservation_baseline)
            + pending_reservations + requested_rate
```

After two 3 Mbps reservations are active, a third request projects 9 Mbps. The agent rejects it because 9 Mbps exceeds 8.5 Mbps. Using the maximum of measured and committed load avoids counting active traffic twice after the interface counter catches up.

## Fresh verification and terminal output

- `mn --test pingall`: passed, 0% dropped.
- `topology/mininet_topology.py --smoke`: all eight source/path checks passed.
- Unit suite: 10 passed. It covers concurrent admission, duplicate requests, capacity rejection, measurement lag, cancel, expiry, release, protocol versioning, hysteresis, and route rollback.
- Compile check: `python3 -m compileall -q agents routing tests experiments topology` passed.

Complete captured stdout is in [`results/terminal/20261104-rerun/`](../results/terminal/20261104-rerun/):

- [`mininet-pingall.txt`](../results/terminal/20261104-rerun/mininet-pingall.txt)
- [`topology-connectivity.txt`](../results/terminal/20261104-rerun/topology-connectivity.txt)
- [`static.txt`](../results/terminal/20261104-rerun/static.txt)
- [`reactive.txt`](../results/terminal/20261104-rerun/reactive.txt)
- [`hysteresis.txt`](../results/terminal/20261104-rerun/hysteresis.txt)
- [`intent.txt`](../results/terminal/20261104-rerun/intent.txt)

Machine-readable summary: [`results/rerun_validation.json`](../results/rerun_validation.json). Each run directory under `results/raw/` contains `summary.json`, timestamped `events.jsonl`, iperf3 client JSON, ping output, and, for the intent run, admission-agent logs.

## Limitations and next work

The rerun contains one sample per policy. The synchronized workload is an intentionally small demonstration. Repeat each main configuration at least ten times, report uncertainty, and add staggered starts, asymmetric capacities, background traffic, offered-load sweeps, control delay/loss/duplication, and agent-failure cases. The experiment has one reroute opportunity per flow, so it does not evaluate repeated oscillation or meaningfully exercise post-switch cooldown behavior.
