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

from neutron.api import extensions
from neutron.api.v2 import base
from neutron.common import constants as const
from neutron.common import exceptions as nexception
from neutron import manager
from neutron.quota import resource_registry


supported_protocols = [const.PROTO_NAME_TCP,
                       const.PROTO_NAME_UDP, const.PROTO_NAME_ICMP]
PROTO_NAME_TO_NUM = {
    'tcp': 6,
    'udp': 17,
    'icmp': 1
}


class ExternalSgRuleInvalidPortValue(nexception.InvalidInput):
    message = _("Invalid value for port %(port)s")


class ExternalSgRuleInvalidProtocol(nexception.InvalidInput):
    message = _("External sg rule protocol %(protocol)s not supported. "
                "Only protocol values %(values)s and their integer "
                "representation (0 to 142) are supported.")


def convert_protocol(value):
    if value is None:
        return
    try:
        val = int(value)
        if val >= 0 and val <= 142:
            # Set value of protocol number to string due to bug 1381379,
            # PostgreSQL fails when it tries to compare integer with string,
            # that exists in db.
            return str(value)
        raise ExternalSgRuleInvalidProtocol(
            protocol=value, values=supported_protocols)
    except (ValueError, TypeError):
        if value.lower() in supported_protocols:
            return PROTO_NAME_TO_NUM[value.lower()]
        raise ExternalSgRuleInvalidProtocol(
            protocol=value, values=supported_protocols)
    except AttributeError:
        raise ExternalSgRuleInvalidProtocol(
            protocol=value, values=supported_protocols)


def convert_validate_port_value(port):
    if port is None:
        return port
    try:
        val = int(port)
    except (ValueError, TypeError):
        raise ExternalSgRuleInvalidPortValue(port=port)

    # VSD requires port number 0 not valid
    if val >= 1 and val <= 65535:
        return val
    else:
        raise ExternalSgRuleInvalidPortValue(port=port)


# Attribute Map
RESOURCE_ATTRIBUTE_MAP = {
    'nuage_external_security_groups': {
        'id': {'allow_post': False, 'allow_put': False,
               'validate': {'type:uuid': None},
               'is_visible': True,
               'primary_key': True},
        'name': {'allow_post': True, 'allow_put': False,
                 'is_visible': True, 'default': '',
                 'validate': {'type:name_not_default': None}},
        'description': {'allow_post': True, 'allow_put': False,
                        'is_visible': True, 'default': '',
                        'validate': {'type:string_or_none': None}},
        'extended_community_id': {'allow_post': True, 'allow_put': False,
                                  'is_visible': True, 'default': None,
                                  'validate': {'type:string': None}},
        'subnet_id': {'allow_post': True, 'allow_put': False,
                      'is_visible': True, 'default': None,
                      'validate': {'type:uuid_or_none': None}},
        'router_id': {'allow_post': True, 'allow_put': False,
                      'is_visible': True, 'default': None,
                      'validate': {'type:uuid_or_none': None}},
        'tenant_id': {'allow_post': True, 'allow_put': False,
                      'required_by_policy': True,
                      'is_visible': True},
    },
    'nuage_external_security_group_rules': {
        'id': {'allow_post': False, 'allow_put': False,
               'validate': {'type:uuid': None},
               'is_visible': True,
               'primary_key': True},
        'direction': {'allow_post': True, 'allow_put': True,
                      'is_visible': True,
                      'validate': {'type:values': ['ingress', 'egress']}},
        'remote_external_group_id': {'allow_post': True, 'allow_put': False,
                                     'default': None, 'is_visible': True},
        'origin_group_id': {'allow_post': True, 'allow_put': False,
                            'default': None, 'is_visible': True},
        'protocol': {'allow_post': True, 'allow_put': False,
                     'is_visible': True, 'default': None,
                     'convert_to': convert_protocol},
        'port_range_min': {'allow_post': True, 'allow_put': False,
                           'convert_to': convert_validate_port_value,
                           'default': None, 'is_visible': True},
        'port_range_max': {'allow_post': True, 'allow_put': False,
                           'convert_to': convert_validate_port_value,
                           'default': None, 'is_visible': True},
        'tenant_id': {'allow_post': True, 'allow_put': False,
                      'required_by_policy': True,
                      'is_visible': True},
    }
}


class Nuage_external_security_group(extensions.ExtensionDescriptor):
    """Extension class supporting External Security Group."""

    @classmethod
    def get_name(cls):
        return "Nuage Externalsecuritygroup"

    @classmethod
    def get_alias(cls):
        return "nuage-external-security-group"

    @classmethod
    def get_description(cls):
        return "Nuage Externalsecuritygroup"

    @classmethod
    def get_namespace(cls):
        return "http://nuagenetworks.net/ext/externalsecuritygroup/api/v1.0"

    @classmethod
    def get_updated(cls):
        return "2014-01-01T10:00:00-00:00"

    @classmethod
    def get_resources(cls):
        """Returns Ext Resources."""
        exts = []
        plugin = manager.NeutronManager.get_plugin()
        for resource_name in ['nuage_external_security_group',
                              'nuage_external_security_group_rule']:
            collection_name = resource_name.replace('_', '-') + "s"
            params = RESOURCE_ATTRIBUTE_MAP.get(resource_name + "s", dict())
            resource_registry.register_resource_by_name(resource_name)
            controller = base.create_resource(collection_name,
                                              resource_name,
                                              plugin, params, allow_bulk=True)
            ex = extensions.ResourceExtension(collection_name,
                                              controller)
            exts.append(ex)

        return exts

    @classmethod
    def get_extended_resources(self, version):
        if version == "2.0":
            return dict(RESOURCE_ATTRIBUTE_MAP.items())
        else:
            return {}
