# Distributed Anti-Herding Routing

A reproducible Mininet research prototype for reproducing synchronized congestion herding, comparing routing baselines, and evaluating short-lived distributed reroute reservations.

## Runtime

Run on Ubuntu 22.04/24.04 with Mininet, Open vSwitch, `iperf3`, `iproute2`, and Python 3.10+. Mininet needs Linux network namespaces and root privileges; Windows does not run it natively. This was run under Ubuntu 24.04 on WSL2 with Mininet 2.3.0, OVS 3.3.9, and iperf3 3.16; `mn --test pingall` and the eight source/path checks passed.

Install the runtime dependencies (Ubuntu):

```bash
sudo apt update
sudo apt install -y mininet openvswitch-switch iperf3 iproute2 python3
sudo systemctl enable --now openvswitch-switch
sudo mn --test pingall
```

## First reproduction

```bash
sudo python3 experiments/run_experiment.py --policy static --scenario herd
sudo python3 experiments/run_experiment.py --policy reactive --scenario herd
sudo python3 experiments/run_experiment.py --policy hysteresis --scenario herd
```

All four UDP sources offer 3 Mbps. The initial synchronized scenario puts them on a single 10 Mbps bottleneck and then makes all source routers eligible to move at once. Each run writes JSONL events, iperf3 JSON, and a summary JSON beneath `results/raw/<run-id>/`. Inspect the event log and per-path interface counters to confirm the selected path.

Connectivity only:

```bash
sudo python3 topology/mininet_topology.py --smoke
```

## Reservation policy

```bash
sudo python3 experiments/run_experiment.py --policy intent --scenario herd
```

Each source router agent requests provisional capacity from every admission agent listed for the candidate path. It changes its route only after all grants and successful commit; rejected, expired, or failed attempts are cancelled. Admission agents have independent ledgers and do not select routes. UDP JSON control messages use the separate management network.

## Configuration and evidence

Edit `config/default.json` for offered load, link rates, sampling, thresholds, cooldown, and lease duration. Run IDs and verified gates are recorded in `planned_scenarios.json`; raw JSON and event logs are under `results/raw/` (ignored by Git). `planned_scenarios.json` also tracks follow-up experiments not yet run. The initial topology uses one admission agent per path and supports a list of agents per path for future constrained-link extensions.

## Limitations

Mininet, not hardware, is the evaluation environment. UDP flows use configured offered rates. Link utilization is sampled from Linux interface byte counters. The reservation mechanism is a lightweight lease transaction, not consensus; lost messages and agent restarts can temporarily leave leases until expiry.
