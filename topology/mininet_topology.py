"""Four-source topology with two shared 10 Mbps paths and a management LAN."""
from __future__ import annotations
import argparse
from mininet.link import TCLink
from mininet.net import Mininet
from mininet.node import Host, OVSBridge
from mininet.topo import Topo

DEST = "10.255.0.1"
class LinuxRouter(Host):
    """Mininet host class enabling Linux forwarding in its network namespace."""
    def config(self, **params):
        super().config(**params)
        self.cmd("sysctl -w net.ipv4.ip_forward=1")
        self.cmd("sysctl -w net.ipv4.conf.all.rp_filter=0")
    def terminate(self):
        self.cmd("sysctl -w net.ipv4.ip_forward=0")
        super().terminate()

class AntiHerdingTopo(Topo):
    def build(self, capacity_mbps=10):
        self.addSwitch("s1", cls=OVSBridge)
        hosts=[self.addHost(f"h{i}",ip=None) for i in range(1,5)]
        routers=[self.addHost(f"r{i}", cls=LinuxRouter, ip=None) for i in range(1,5)]
        pa=self.addHost("pa", cls=LinuxRouter, ip=None); pb=self.addHost("pb", cls=LinuxRouter, ip=None); dest=self.addHost("dst", ip=None)
        for i,(h,r) in enumerate(zip(hosts,routers),1):
            self.addLink(h,r)
            self.addLink(r,pa)
            self.addLink(r,pb)
            self.addLink(r,"s1")
        self.addLink(pa,dest,bw=capacity_mbps,delay="2ms",cls=TCLink)
        self.addLink(pb,dest,bw=capacity_mbps,delay="2ms",cls=TCLink)
        self.addLink(pa,"s1"); self.addLink(pb,"s1")

def build_network(capacity_mbps=10):
    net=Mininet(topo=AntiHerdingTopo(capacity_mbps=capacity_mbps),controller=None,link=TCLink,autoSetMacs=True,
                build=False,waitConnected=False)
    net.build(); net.start(); configure_network(net)
    return net

def configure_network(net):
    for i in range(1,5):
        h,r=net.get(f"h{i}"),net.get(f"r{i}")
        h.cmd(f"ip addr add 10.2.{i}.2/24 dev h{i}-eth0")
        r.cmd(f"ip addr add 10.2.{i}.1/24 dev r{i}-eth0")
        r.cmd(f"ip addr add 10.0.{i}.1/30 dev r{i}-eth1")
        net.get("pa").cmd(f"ip addr add 10.0.{i}.2/30 dev pa-eth{i-1}")
        r.cmd(f"ip addr add 10.1.{i}.1/30 dev r{i}-eth2")
        net.get("pb").cmd(f"ip addr add 10.1.{i}.2/30 dev pb-eth{i-1}")
        h.cmd(f"ip route replace {DEST}/32 via 10.2.{i}.1")
        r.cmd(f"ip route replace {DEST}/32 via 10.0.{i}.2 dev r{i}-eth1")
        net.get("pa").cmd(f"ip route replace 10.2.{i}.0/24 via 10.0.{i}.1 dev pa-eth{i-1}")
        net.get("pb").cmd(f"ip route replace 10.2.{i}.0/24 via 10.1.{i}.1 dev pb-eth{i-1}")
    pa,pb,dst=net.get("pa"),net.get("pb"),net.get("dst")
    pa.cmd("ip addr add 10.3.0.1/24 dev pa-eth4")
    dst.cmd("ip addr add 10.3.0.2/24 dev dst-eth0")
    pb.cmd("ip addr add 10.4.0.1/24 dev pb-eth4")
    dst.cmd("ip addr add 10.4.0.2/24 dev dst-eth1")
    dst.cmd(f"ip addr add {DEST}/32 dev lo")
    for i in range(1,5):
        dst.cmd(f"ip route replace 10.2.{i}.0/24 via 10.3.0.1 dev dst-eth0")
    pa.cmd(f"ip route replace {DEST}/32 via 10.3.0.2 dev pa-eth4")
    pb.cmd(f"ip route replace {DEST}/32 via 10.4.0.2 dev pb-eth4")
    for node,addr in [(pa,"172.30.0.2"),(pb,"172.30.0.3")]:
        node.cmd(f"ip addr add {addr}/24 dev {node.name}-eth5")
    for i in range(1,5): net.get(f"r{i}").cmd(f"ip addr add 172.30.0.{10+i}/24 dev r{i}-eth3")
    # Data path defaults and selected route both start on Path A.
    for i in range(1,5):
        r=net.get(f"r{i}"); r.cmd(f"ip route replace {DEST}/32 via 10.0.{i}.2 dev r{i}-eth1")

def main():
    p=argparse.ArgumentParser(); p.add_argument('--capacity',type=float,default=10); p.add_argument('--smoke',action='store_true')
    a=p.parse_args(); net=build_network(a.capacity)
    try:
        if a.smoke:
            for i in range(1,5):
                r=net.get(f"r{i}")
                for path,via,dev in [("A",f"10.0.{i}.2",f"r{i}-eth1"),("B",f"10.1.{i}.2",f"r{i}-eth2")]:
                    r.cmd(f"ip route replace {DEST}/32 via {via} dev {dev}")
                    result=net.get(f"h{i}").cmd(f"ping -c 1 -W 2 {DEST}")
                    if "1 received" not in result: raise RuntimeError(f"h{i} failed via path {path}: {result}")
                    print(f"h{i} via path {path}: reachable")
                    r.cmd(f"ip route replace {DEST}/32 via 10.0.{i}.2 dev r{i}-eth1")
    finally: net.stop()
if __name__=='__main__': main()
