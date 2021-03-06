# Copyright 2016 Alcatel-Lucent USA Inc.
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

from oslo_log import helpers as log_helpers

from neutron.callbacks import resources
from neutron import policy

from nuage_neutron.plugins.common import constants
from nuage_neutron.plugins.common import exceptions
from nuage_neutron.plugins.common.extensions.nuagefloatingip \
    import NUAGE_FLOATINGIP
from nuage_neutron.plugins.common import nuagedb
from nuage_neutron.plugins.common.service_plugins \
    import vsd_passthrough_resource
from nuage_neutron.plugins.common import utils as nuage_utils
from nuage_neutron.plugins.common.validation import require
from nuagenetlib.restproxy import ResourceNotFoundException


class NuageFloatingip(vsd_passthrough_resource.VsdPassthroughResource):
    vsd_to_os = {
        'ID': 'id',
        'address': 'floating_ip_address',
        'assigned': 'assigned'
    }
    os_to_vsd = {
        'id': 'ID',
        'floating_ip_address': 'address',
        'assigned': 'assigned'
    }
    vsd_filterables = ['id', 'floating_ip_address', 'assigned']
    extra_filters = ['for_port', 'for_subnet']

    def __init__(self):
        super(NuageFloatingip, self).__init__()
        self.nuage_callbacks.subscribe(self.post_port_update,
                                       resources.PORT, constants.AFTER_UPDATE)
        self.nuage_callbacks.subscribe(self.post_port_create,
                                       resources.PORT, constants.AFTER_CREATE)
        self.nuage_callbacks.subscribe(self._post_port_show,
                                       resources.PORT, constants.AFTER_SHOW)

    @nuage_utils.handle_nuage_api_errorcode
    @log_helpers.log_method_call
    def get_nuage_floatingip(self, context, id, fields=None):
        try:
            floatingip = self.nuageclient.get_nuage_floatingip(id,
                                                               externalID=None)
            if not floatingip:
                raise exceptions.NuageNotFound(resource="nuage-floatingip",
                                               resource_id=id)
            return self.map_vsd_to_os(floatingip, fields=fields)
        except ResourceNotFoundException:
            raise exceptions.NuageNotFound(resource="nuage_floatingip",
                                           resource_id=id)

    @nuage_utils.handle_nuage_api_errorcode
    @log_helpers.log_method_call
    def get_nuage_floatingips(self, context, filters=None, fields=None):
        if 'for_port' in filters and 'for_subnet' in filters:
            msg = _("Can't combine both 'for_port' and 'for_subnet' filter")
            raise exceptions.NuageBadRequest(msg=msg)

        if 'for_port' in filters:
            getter = self.get_port_available_nuage_floatingips
        elif 'for_subnet' in filters:
            getter = self.get_subnet_available_nuage_floatingips
        else:
            policy.enforce(context, 'get_nuage_floatingip_all', None)
            getter = self.get_all_nuage_floatingips
        floatingips = getter(context, filters=filters)
        return [self.map_vsd_to_os(floatingip, fields=fields)
                for floatingip in floatingips]

    def get_port_available_nuage_floatingips(self, context, filters=None):
        port_id = filters.pop('for_port')[0]
        vsd_mapping = nuagedb.get_subnet_l2dom_by_port_id(context.session,
                                                          port_id)
        return self._get_available_nuage_floatingips(vsd_mapping, filters)

    def get_subnet_available_nuage_floatingips(self, context, filters=None):
        subnet_id = filters.pop('for_subnet')[0]
        vsd_mapping = nuagedb.get_subnet_l2dom_by_id(context.session,
                                                     subnet_id)
        require(vsd_mapping, 'vsd subnet mapping for subnet', subnet_id)
        return self._get_available_nuage_floatingips(vsd_mapping, filters)

    def get_all_nuage_floatingips(self, context, filters=None):
        vsd_filters = self.osfilters_to_vsdfilters(filters)
        return self.nuageclient.get_nuage_floatingips(externalID=None,
                                                      **vsd_filters)

    def _get_available_nuage_floatingips(self, vsd_mapping, filters):
        vsd_filters = self.osfilters_to_vsdfilters(filters)
        vsd_id = vsd_mapping['nuage_subnet_id']
        vsd_subnet = self.nuageclient.get_subnet_or_domain_subnet_by_id(vsd_id)
        if not vsd_subnet:
            raise exceptions.VsdSubnetNotFound(id=vsd_id)
        if vsd_subnet['type'] == constants.L2DOMAIN:
            return []

        domain_id = self.nuageclient.get_router_by_domain_subnet_id(
            vsd_subnet['subnet_id'])
        return self.nuageclient.get_nuage_domain_floatingips(
            domain_id, assigned=False, externalID=None, **vsd_filters)

    def post_port_update(self, resource, event, trigger, **kwargs):
        self.process_port_nuage_floatingip(resource, event, trigger, **kwargs)

    def post_port_create(self, resource, event, trigger, **kwargs):
        self.process_port_nuage_floatingip(resource, event, trigger, **kwargs)
        if kwargs['vport']:
            kwargs['port'][NUAGE_FLOATINGIP] = kwargs['request_port'].get(
                NUAGE_FLOATINGIP)
        else:
            kwargs['port'][NUAGE_FLOATINGIP] = None

    def process_port_nuage_floatingip(self, resource, event,
                                      trigger, **kwargs):
        request_port = kwargs['request_port']
        vport = kwargs['vport']
        if not vport or NUAGE_FLOATINGIP not in request_port:
            return
        self._process_port_nuage_floatingip(
            event, request_port, kwargs['rollbacks'], vport)

    @nuage_utils.handle_nuage_api_errorcode
    def _process_port_nuage_floatingip(self, event, request_port, rollbacks,
                                       vport):
        request_fip = request_port[NUAGE_FLOATINGIP] or {}
        if request_fip:
            floatingip = self.nuageclient.get_nuage_floatingip(
                request_fip.get('id'), required=True)
            if floatingip['externalID']:
                msg = _("Floatingip %s has externalID, it can't be used with "
                        "this API.") % floatingip['ID']
                raise exceptions.NuageBadRequest(msg=msg)
        if event == constants.AFTER_UPDATE:
            rollbacks.append(
                (self.nuageclient.update_vport,
                 [vport['nuage_vport_id'],
                  {'associatedFloatingIPID': vport['nuage_floating_ip']}],
                 {})
            )
        self.nuageclient.update_vport(
            vport['nuage_vport_id'],
            {'associatedFloatingIPID': request_fip.get('id')})

    def _post_port_show(self, resource, event, trigger, **kwargs):
        port = kwargs.get('port')
        vport = kwargs.get('vport')
        fields = kwargs.get('fields')
        if fields and NUAGE_FLOATINGIP not in fields:
            return
        if not vport:
            port[NUAGE_FLOATINGIP] = None
            return

        floatingip = self.nuageclient.get_nuage_floatingip(
            vport['nuage_floating_ip'], externalID=None)
        if floatingip:
            port[NUAGE_FLOATINGIP] = {'id': floatingip['ID'],
                                      'ip_address': floatingip['address']}
        else:
            port[NUAGE_FLOATINGIP] = None
