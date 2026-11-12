"""Print namespace addresses, routes, and adjacent-hop reachability for debugging."""
from mininet_topology import build_network

net=build_network()
try:
    for name in ("h1","r1","pa","pb","dst"):
        node=net.get(name)
        print(f"NODE {name}\n{node.cmd('ip -br addr')}\n{node.cmd('ip route')}")
    for name,peer in (("h1","10.2.1.1"),("r1","10.0.1.2"),
                      ("pa","10.3.0.2"),("dst","10.3.0.1")):
        print(f"PING {name} {peer}\n{net.get(name).cmd(f'ping -c1 -W1 {peer}')}")
finally:
    net.stop()
