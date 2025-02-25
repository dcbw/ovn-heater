from collections import namedtuple
from ovn_context import Context
from ovn_workload import Namespace
from ovn_ext_cmd import ExtCmd
import ovn_load_balancer as lb
import ovn_exceptions
import netaddr


# By default simulate a deployment with 2 pods, one of which is the
# backend of a service.
DENSITY_PODS_VIP_RATIO = 2


DensityCfg = namedtuple(
    'DensityCfg', ['n_pods', 'n_startup', 'batch', 'pods_vip_ratio']
)

DEFAULT_VIP_SUBNET = netaddr.IPNetwork('100.0.0.0/16')
DEFAULT_VIP_SUBNET6 = netaddr.IPNetwork('100::/32')
DEFAULT_VIP_PORT = 80
DEFAULT_BACKEND_PORT = 8080


class DensityHeavy(ExtCmd):
    def __init__(self, config, central_node, worker_nodes, global_cfg):
        super(DensityHeavy, self).__init__(config, central_node, worker_nodes)
        test_config = config.get('density_heavy', dict())
        pods_vip_ratio = test_config.get(
            'pods_vip_ratio', DENSITY_PODS_VIP_RATIO
        )
        self.config = DensityCfg(
            n_pods=test_config.get('n_pods', 0),
            n_startup=test_config.get('n_startup', 0),
            batch=test_config.get('batch', pods_vip_ratio),
            pods_vip_ratio=pods_vip_ratio,
        )
        if (
            self.config.pods_vip_ratio > self.config.batch
            or self.config.batch % self.config.pods_vip_ratio
            or self.config.n_pods % self.config.batch
        ):
            raise ovn_exceptions.OvnInvalidConfigException()
        self.lb_list = []

    def create_lb(self, cluster, name, vip_subnet, backend_lists):
        load_balancer = lb.OvnLoadBalancer(f'lb_{name}', cluster.nbctl)
        cluster.provision_lb(load_balancer)

        vip_net = DEFAULT_VIP_SUBNET.next(len(self.lb_list))
        vip_ip = vip_net.ip.__add__(1)

        prefix = '[' if vip_subnet.version == 6 else ''
        suffix = ']' if vip_subnet.version == 6 else ''

        vips = {
            f'{prefix}{vip_ip + i}{suffix}:{DEFAULT_VIP_PORT}': [
                f'{prefix}{p.ip}{suffix}:{DEFAULT_BACKEND_PORT}' for p in ports
            ]
            for i, ports in enumerate(backend_lists)
        }
        load_balancer.add_vips(vips)
        self.lb_list.append(load_balancer)

    def run(self, ovn, global_cfg):
        if self.config.pods_vip_ratio == 0:
            return

        ns = Namespace(ovn, 'ns_density_heavy', global_cfg)

        with Context(ovn, 'density_heavy_startup', brief_report=True) as ctx:
            ports = ovn.provision_ports(self.config.n_startup, passive=True)
            ns.add_ports(ports)
            for i in range(
                0, self.config.n_startup, self.config.pods_vip_ratio
            ):
                if global_cfg.run_ipv4:
                    self.create_lb(
                        ovn,
                        'density_heavy_' + str(i),
                        DEFAULT_VIP_SUBNET,
                        [[ports[i]]],
                    )
                if global_cfg.run_ipv6:
                    self.create_lb(
                        ovn,
                        'density_heavy6_' + str(i),
                        DEFAULT_VIP_SUBNET6,
                        [[ports[i]]],
                    )

        with Context(
            ovn,
            'density_heavy',
            (self.config.n_pods - self.config.n_startup) / self.config.batch,
            test=self,
        ) as ctx:
            for i in ctx:
                ports = ovn.provision_ports(self.config.batch)
                ns.add_ports(ports)
                for j in range(
                    0, self.config.batch, self.config.pods_vip_ratio
                ):
                    if global_cfg.run_ipv4:
                        name = 'density_heavy_' + str(
                            self.config.n_startup + i * self.config.batch + j
                        )
                        self.create_lb(
                            ovn, name, DEFAULT_VIP_SUBNET, [[ports[j]]]
                        )
                    if global_cfg.run_ipv6:
                        name = 'density_heavy6_' + str(
                            self.config.n_startup + i * self.config.batch + j
                        )
                        self.create_lb(
                            ovn, name, DEFAULT_VIP_SUBNET6, [[ports[j]]]
                        )
                ovn.ping_ports(ports)

        if not global_cfg.cleanup:
            return
        with Context(ovn, 'density_heavy_cleanup', brief_report=True) as ctx:
            ovn.unprovision_vips()
            ns.unprovision()
