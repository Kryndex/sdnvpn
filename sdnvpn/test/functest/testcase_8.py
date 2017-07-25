#!/usr/bin/python
#
# Copyright (c) 2017 All rights reserved
# This program and the accompanying materials
# are made available under the terms of the Apache License, Version 2.0
# which accompanies this distribution, and is available at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
"""
Test whether router assoc can coexist with floating IP
- Create VM1 in net1 with a subnet which is connected to a router
    which is connected with the gateway
- Create VM2 in net2 with a subnet without a router attached.
- Create bgpvpn with iRT=eRT
- Assoc the router of net1 with bgpvpn and assoc net 2 with the bgpvpn
- Try to ping from one VM to the other
- Assign a floating IP to the VM in the router assoc network
- Ping it the floating ip
"""
import argparse
import logging
import sys

from functest.utils import openstack_utils as os_utils
from sdnvpn.lib import config as sdnvpn_config
from sdnvpn.lib import utils as test_utils
from sdnvpn.lib.results import Results

parser = argparse.ArgumentParser()

parser.add_argument("-r", "--report",
                    help="Create json result file",
                    action="store_true")

args = parser.parse_args()

logger = logging.getLogger('sdnvpn-testcase-8')

COMMON_CONFIG = sdnvpn_config.CommonConfig()
TESTCASE_CONFIG = sdnvpn_config.TestcaseConfig('testcase_8')


def main():
    results = Results(COMMON_CONFIG.line_length)

    results.add_to_summary(0, "=")
    results.add_to_summary(2, "STATUS", "SUBTEST")
    results.add_to_summary(0, "=")

    nova_client = os_utils.get_nova_client()
    neutron_client = os_utils.get_neutron_client()
    glance_client = os_utils.get_glance_client()

    (floatingip_ids, instance_ids, router_ids, network_ids, image_ids,
     subnet_ids, interfaces, bgpvpn_ids) = ([] for i in range(8))

    image_id = os_utils.create_glance_image(glance_client,
                                            TESTCASE_CONFIG.image_name,
                                            COMMON_CONFIG.image_path,
                                            disk=COMMON_CONFIG.image_format,
                                            container="bare",
                                            public='public')
    image_ids.append(image_id)

    network_1_id, subnet_1_id, router_1_id = test_utils.create_network(
        neutron_client,
        TESTCASE_CONFIG.net_1_name,
        TESTCASE_CONFIG.subnet_1_name,
        TESTCASE_CONFIG.subnet_1_cidr,
        TESTCASE_CONFIG.router_1_name)
    network_2_id = test_utils.create_net(
        neutron_client,
        TESTCASE_CONFIG.net_2_name)

    subnet_2_id = test_utils.create_subnet(
        neutron_client,
        TESTCASE_CONFIG.subnet_2_name,
        TESTCASE_CONFIG.subnet_2_cidr,
        network_2_id)

    interfaces.append(tuple((router_1_id, subnet_1_id)))
    network_ids.extend([network_1_id, network_2_id])
    router_ids.append(router_1_id)
    subnet_ids.extend([subnet_1_id, subnet_2_id])

    sg_id = os_utils.create_security_group_full(neutron_client,
                                                TESTCASE_CONFIG.secgroup_name,
                                                TESTCASE_CONFIG.secgroup_descr)
    test_utils.open_icmp(neutron_client, sg_id)
    test_utils.open_http_port(neutron_client, sg_id)

    vm_2 = test_utils.create_instance(
        nova_client,
        TESTCASE_CONFIG.instance_2_name,
        image_id,
        network_2_id,
        sg_id,
        secgroup_name=TESTCASE_CONFIG.secgroup_name)
    vm_2_ip = test_utils.get_instance_ip(vm_2)

    u1 = test_utils.generate_ping_userdata([vm_2_ip])
    vm_1 = test_utils.create_instance(
        nova_client,
        TESTCASE_CONFIG.instance_1_name,
        image_id,
        network_1_id,
        sg_id,
        secgroup_name=TESTCASE_CONFIG.secgroup_name,
        userdata=u1)
    instance_ids.extend([vm_1.id, vm_2.id])

    results.record_action("Create VPN with eRT==iRT")
    vpn_name = "sdnvpn-8"
    kwargs = {"import_targets": TESTCASE_CONFIG.targets,
              "export_targets": TESTCASE_CONFIG.targets,
              "route_distinguishers": TESTCASE_CONFIG.route_distinguishers,
              "name": vpn_name}
    bgpvpn = test_utils.create_bgpvpn(neutron_client, **kwargs)
    bgpvpn_id = bgpvpn['bgpvpn']['id']
    logger.debug("VPN created details: %s" % bgpvpn)
    bgpvpn_ids.append(bgpvpn_id)

    msg = ("Associate router '%s' and net '%s' to the VPN."
           % (TESTCASE_CONFIG.router_1_name,
              TESTCASE_CONFIG.net_2_name))
    results.record_action(msg)
    results.add_to_summary(0, "-")

    test_utils.create_router_association(
        neutron_client, bgpvpn_id, router_1_id)
    test_utils.create_network_association(
        neutron_client, bgpvpn_id, network_2_id)

    test_utils.wait_for_bgp_router_assoc(
        neutron_client, bgpvpn_id, router_1_id)
    test_utils.wait_for_bgp_net_assoc(
        neutron_client, bgpvpn_id, network_2_id)

    instances_up = test_utils.wait_for_instances_up(vm_1, vm_2)
    if not instances_up:
        logger.error("One or more instances is down")

    logger.info("Waiting for the VMs to connect to each other using the"
                " updated network configuration")
    test_utils.wait_before_subtest()

    results.get_ping_status(vm_1, vm_2, expected="PASS", timeout=200)
    results.add_to_summary(0, "=")

    msg = "Assign a Floating IP to %s" % vm_1.name
    results.record_action(msg)

    fip = os_utils.create_floating_ip(neutron_client)

    fip_added = os_utils.add_floating_ip(nova_client, vm_1.id, fip['fip_addr'])
    if fip_added:
        results.add_success(msg)
    else:
        results.add_failure(msg)

    results.add_to_summary(0, "=")
    results.record_action("Ping %s via Floating IP" % vm_1.name)
    results.add_to_summary(0, "-")
    results.ping_ip_test(fip['fip_addr'])

    floatingip_ids.append(fip['fip_id'])

    test_utils.cleanup_nova(nova_client, instance_ids, image_ids)
    test_utils.cleanup_neutron(neutron_client, floatingip_ids, bgpvpn_ids,
                               interfaces, subnet_ids, router_ids,
                               network_ids)

    return results.compile_summary()


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    sys.exit(main())
