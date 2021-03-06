# Copyright 2015 Alcatel-Lucent USA Inc.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.
from nuage_neutron.plugins.common.exceptions import SubnetMappingNotFound

from oslo_log import log as logging
from oslo_utils import excutils

from neutron.api.v2 import attributes as attr
from neutron.callbacks import resources
from neutron.extensions import allowedaddresspairs as addr_pair
from nuage_neutron.plugins.common.base_plugin import BaseNuagePlugin
from nuage_neutron.plugins.common import constants
from nuage_neutron.plugins.common import nuagedb

LOG = logging.getLogger(__name__)


class NuageAddressPair(BaseNuagePlugin):

    def __init__(self):
        super(NuageAddressPair, self).__init__()
        self.nuage_callbacks.subscribe(self.post_port_update,
                                       resources.PORT, constants.AFTER_UPDATE)
        self.nuage_callbacks.subscribe(self.post_port_create,
                                       resources.PORT, constants.AFTER_CREATE)

    def _create_vips(self, nuage_subnet_id, port, nuage_vport):
        nuage_vip_dict = dict()
        for allowed_addr_pair in port[addr_pair.ADDRESS_PAIRS]:
            vip = allowed_addr_pair['ip_address']
            mac = allowed_addr_pair['mac_address']

            params = {
                'vip': vip,
                'mac': mac,
                'subnet_id': nuage_subnet_id,
                'vport_id': nuage_vport['nuage_vport_id'],
                'port_ip': port['fixed_ips'][0]['ip_address'],
                'port_mac': port['mac_address']
            }

            try:
                self.nuageclient.create_vip(params)
                nuage_vip_dict[params['vip']] = params['mac']

            except Exception as e:
                with excutils.save_and_reraise_exception():
                    LOG.error("Error in creating vip for ip %(vip)s and mac "
                              "%(mac)s: %(err)s", {'vip': vip,
                                                   'mac': mac,
                                                   'err': e.message})
                    self.nuageclient.delete_vips(nuage_vport['nuage_vport_id'],
                                                 nuage_vip_dict,
                                                 nuage_vip_dict.keys())

    def _update_vips(self, nuage_subnet_id, port, nuage_vport,
                     deleted_addr_pairs):
        if deleted_addr_pairs:
            # If some addr pairs were deleted we might have to undo some
            # action on VSD
            for addrpair in deleted_addr_pairs:
                params = {
                    'vip': addrpair['ip_address'],
                    'mac': addrpair['mac_address'],
                    'subnet_id': nuage_subnet_id,
                    'vport_id': nuage_vport['nuage_vport_id'],
                    'port_ip': port['fixed_ips'][0]['ip_address'],
                    'port_mac': port['mac_address']
                }
                self.nuageclient.process_deleted_addr_pair(params)

        # Get all the vips on vport
        nuage_vips = self.nuageclient.get_vips(nuage_vport['nuage_vport_id'])

        nuage_vip_dict = dict()
        for nuage_vip in nuage_vips:
            nuage_vip_dict[nuage_vip['vip']] = nuage_vip['mac']

        os_vip_dict = dict()
        if addr_pair.ADDRESS_PAIRS in port:
            for allowed_addr_pair in port[addr_pair.ADDRESS_PAIRS]:
                # OS allows addr pairs with same ip and different mac,
                # which does not make sense in VSD. We will create only one
                # of the pair in VSD
                if allowed_addr_pair['ip_address'] in os_vip_dict:
                    LOG.warning("Duplicate ip found in allowed address "
                                "pairs, so %s will be ignored",
                                allowed_addr_pair)
                    continue
                os_vip_dict[allowed_addr_pair['ip_address']] = (
                    allowed_addr_pair['mac_address'])

        vips_add_list = []
        vips_delete_set = set()
        for vip, mac in os_vip_dict.iteritems():
            if vip in nuage_vip_dict:
                # Check if mac is same
                if mac != nuage_vip_dict.get(vip):
                    vips_add_dict = {
                        'ip_address': vip,
                        'mac_address': mac
                    }
                    vips_add_list.append(vips_add_dict)
            else:
                vips_add_dict = {
                    'ip_address': vip,
                    'mac_address': mac
                }
                vips_add_list.append(vips_add_dict)

        for vip, mac in nuage_vip_dict.iteritems():
            if vip in os_vip_dict:
                # Check if mac is same
                if mac != os_vip_dict.get(vip):
                    vips_delete_set.add(vip)
            else:
                vips_delete_set.add(vip)

        if vips_delete_set:
            try:
                self.nuageclient.delete_vips(nuage_vport['nuage_vport_id'],
                                             nuage_vip_dict,
                                             vips_delete_set)
            except Exception as e:
                with excutils.save_and_reraise_exception:
                    LOG.error("Error in deleting vips on vport %(port)s: %("
                              "err)s", {'port': nuage_vport['nuage_vport_id'],
                                        'err': e})

        if vips_add_list:
            port_dict = {
                addr_pair.ADDRESS_PAIRS: vips_add_list,
                'fixed_ips': port['fixed_ips'],
                'mac_address': port['mac_address']
            }
            self._create_vips(nuage_subnet_id, port_dict, nuage_vport)

    def _process_allowed_address_pairs(self, context, port, vport,
                                       create=False, delete_addr_pairs=None):
        subnet_id = port['fixed_ips'][0]['subnet_id']
        subnet_mapping = nuagedb.get_subnet_l2dom_by_id(context.session,
                                                        subnet_id)
        if subnet_mapping:
            if vport:
                if create:
                    self._create_vips(subnet_mapping['nuage_subnet_id'],
                                      port, vport)
                else:
                    self._update_vips(subnet_mapping['nuage_subnet_id'],
                                      port, vport, delete_addr_pairs)

    def _verify_allowed_address_pairs(self, port, port_data):
        empty_allowed_address_pairs = (
            addr_pair.ADDRESS_PAIRS in port_data and (
                not (port_data[addr_pair.ADDRESS_PAIRS] or
                     port[addr_pair.ADDRESS_PAIRS])))
        if ((addr_pair.ADDRESS_PAIRS not in port_data) or (
                not attr.is_attr_set(port_data[addr_pair.ADDRESS_PAIRS])) or
                empty_allowed_address_pairs):
            # No change is required if port_data doesn't have addr pairs
            LOG.info('No allowed address pairs update required for port %s',
                     port['id'])
            return False
        return True

    def create_allowed_address_pairs(self, context, port, port_data, vport):
        verify = self._verify_allowed_address_pairs(port, port_data)
        if not verify:
            return

        self._process_allowed_address_pairs(context, port, vport, True)

    def update_allowed_address_pairs(self, context, original_port,
                                     port_data, updated_port, vport):
        verify = self._verify_allowed_address_pairs(original_port, port_data)
        if not verify:
            return

        if addr_pair.ADDRESS_PAIRS in original_port:
            if not cmp(port_data[addr_pair.ADDRESS_PAIRS],
                       original_port[addr_pair.ADDRESS_PAIRS]):
                # No change is required if addr pairs in port and port_data are
                # same
                LOG.info('Allowed address pairs to update %(upd)s and one '
                         'in db %(db)s are same, so no change is required',
                         {'upd': port_data[addr_pair.ADDRESS_PAIRS],
                          'db': original_port[addr_pair.ADDRESS_PAIRS]})
                return

        old_addr_pairs = original_port[addr_pair.ADDRESS_PAIRS]
        new_addr_pairs = port_data[addr_pair.ADDRESS_PAIRS]
        delete_addr_pairs = self._get_deleted_addr_pairs(old_addr_pairs,
                                                         new_addr_pairs)
        self._process_allowed_address_pairs(context, updated_port, vport,
                                            False, delete_addr_pairs)

    def _get_deleted_addr_pairs(self, old_addr_pairs, new_addr_pairs):
        addr_pair_dict = dict()
        deleted_addr_pairs = []
        for addrpair in new_addr_pairs:
            addr_pair_dict[addrpair['ip_address']] = addrpair['mac_address']

        for addrpair in old_addr_pairs:
            if addrpair['ip_address'] in addr_pair_dict:
                # check if mac is also same, if not add it to deleted list
                if (addr_pair_dict[addrpair['ip_address']] !=
                        addrpair['mac_address']):
                    deleted_addr_pairs.append(addrpair)
            else:
                deleted_addr_pairs.append(addrpair)

        return deleted_addr_pairs

    def post_port_create(self, resource, event, plugin, **kwargs):
        port = kwargs.get('port')
        request_port = kwargs.get('request_port')
        vport = kwargs.get('vport')
        context = kwargs.get('context')
        try:
            nuagedb.get_subnet_l2dom_by_port_id(context.session, port['id'])
            self.create_allowed_address_pairs(context, port, request_port,
                                              vport)
        except SubnetMappingNotFound:
            pass

    def post_port_update(self, resource, event, plugin, **kwargs):
        updated_port = kwargs.get('updated_port')
        vport = kwargs.get('vport')
        original_port = kwargs.get('original_port')
        request_port = kwargs.get('request_port')
        context = kwargs.get('context')
        self.update_allowed_address_pairs(context, original_port, request_port,
                                          updated_port, vport)
