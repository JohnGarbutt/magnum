#    Licensed under the Apache License, Version 2.0 (the "License");
#    you may not use this file except in compliance with the License.
#    You may obtain a copy of the License at
#
#        http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS,
#    WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#    See the License for the specific language governing permissions and
#    limitations under the License.

import datetime

import pecan
from pecan import rest
import wsme
from wsme import types as wtypes
import wsmeext.pecan as wsme_pecan

from magnum.api.controllers import link
from magnum.api.controllers.v1 import base as v1_base
from magnum.api.controllers.v1 import collection
from magnum.api.controllers.v1 import types
from magnum.api.controllers.v1 import utils as api_utils
from magnum.common import exception
from magnum.common import k8s_manifest
from magnum import objects


# NOTE(dims): We don't depend on oslo*i18n yet
_ = _LI = _LW = _LE = _LC = lambda x: x


class ServicePatchType(v1_base.K8sPatchType):

    @staticmethod
    def internal_attrs():
        defaults = v1_base.K8sPatchType.internal_attrs()
        return defaults + ['/selector', '/port', '/ip']


class Service(v1_base.K8sResourceBase):

    uuid = types.uuid
    """Unique UUID for this service"""

    selector = wsme.wsattr({wtypes.text: wtypes.text}, readonly=True)
    """Selector of this service"""

    ip = wtypes.text
    """IP of this service"""

    port = wsme.wsattr(wtypes.IntegerType(), readonly=True)
    """Port of this service"""

    links = wsme.wsattr([link.Link], readonly=True)
    """A list containing a self link and associated service links"""

    def __init__(self, **kwargs):
        super(Service, self).__init__()

        self.fields = []
        for field in objects.Service.fields:
            # Skip fields we do not expose.
            if not hasattr(self, field):
                continue
            self.fields.append(field)
            setattr(self, field, kwargs.get(field, wtypes.Unset))

    @staticmethod
    def _convert_with_links(service, url, expand=True):
        if not expand:
            service.unset_fields_except(['uuid', 'name', 'bay_uuid', 'labels',
                                         'selector', 'ip', 'port'])

        service.links = [link.Link.make_link('self', url,
                                             'services', service.uuid),
                         link.Link.make_link('bookmark', url,
                                             'services', service.uuid,
                                             bookmark=True)
                         ]
        return service

    @classmethod
    def convert_with_links(cls, rpc_service, expand=True):
        service = Service(**rpc_service.as_dict())
        return cls._convert_with_links(service, pecan.request.host_url, expand)

    @classmethod
    def sample(cls, expand=True):
        sample = cls(uuid='fe78db47-9a37-4e9f-8572-804a10abc0aa',
                     name='MyService',
                     bay_uuid='7ae81bb3-dec3-4289-8d6c-da80bd8001ae',
                     labels={'label1': 'foo'},
                     selector={'label1': 'foo'},
                     ip='172.17.2.2',
                     port=80,
                     manifest_url='file:///tmp/rc.yaml',
                     manifest='''{
                         "id": "service_foo",
                         "kind": "Service",
                         "apiVersion": "v1beta1",
                         "port": 88,
                         "selector": {
                             "bar": "foo"
                         },
                         "labels": {
                             "bar": "foo"
                         }
                     }''',
                     created_at=datetime.datetime.utcnow(),
                     updated_at=datetime.datetime.utcnow())
        return cls._convert_with_links(sample, 'http://localhost:9511', expand)

    def parse_manifest(self):
        try:
            manifest = k8s_manifest.parse(self._get_manifest())
        except ValueError as e:
            raise exception.InvalidParameterValue(message=str(e))
        try:
            self.name = manifest["id"]
        except KeyError:
            raise exception.InvalidParameterValue(
                "'id' can't be empty in manifest.")
        if "port" in manifest:
            self.port = manifest["port"]
        if "selector" in manifest:
            self.selector = manifest["selector"]
        if "labels" in manifest:
            self.labels = manifest["labels"]


class ServiceCollection(collection.Collection):
    """API representation of a collection of services."""

    services = [Service]
    """A list containing services objects"""

    def __init__(self, **kwargs):
        self._type = 'services'

    @staticmethod
    def convert_with_links(rpc_services, limit, url=None,
                           expand=False, **kwargs):
        collection = ServiceCollection()
        collection.services = [Service.convert_with_links(p, expand)
                               for p in rpc_services]
        collection.next = collection.get_next(limit, url=url, **kwargs)
        return collection

    @classmethod
    def sample(cls):
        sample = cls()
        sample.services = [Service.sample(expand=False)]
        return sample


class ServicesController(rest.RestController):
    """REST controller for Services."""

    def __init__(self):
        super(ServicesController, self).__init__()

    from_services = False
    """A flag to indicate if the requests to this controller are coming
    from the top-level resource Services."""

    _custom_actions = {
        'detail': ['GET'],
    }

    def _get_services_collection(self, marker, limit,
                                 sort_key, sort_dir, expand=False,
                                 resource_url=None):

        limit = api_utils.validate_limit(limit)
        sort_dir = api_utils.validate_sort_dir(sort_dir)

        marker_obj = None
        if marker:
            marker_obj = objects.Service.get_by_uuid(pecan.request.context,
                                                     marker)

        services = pecan.request.rpcapi.service_list(pecan.request.context,
                                                 limit,
                                                 marker_obj,
                                                 sort_key=sort_key,
                                                 sort_dir=sort_dir)

        return ServiceCollection.convert_with_links(services, limit,
                                                    url=resource_url,
                                                    expand=expand,
                                                    sort_key=sort_key,
                                                    sort_dir=sort_dir)

    @wsme_pecan.wsexpose(ServiceCollection, types.uuid,
                         types.uuid, int, wtypes.text, wtypes.text)
    def get_all(self, service_uuid=None, marker=None, limit=None,
                sort_key='id', sort_dir='asc'):
        """Retrieve a list of services.

        :param marker: pagination marker for large data sets.
        :param limit: maximum number of resources to return in a single result.
        :param sort_key: column to sort results by. Default: id.
        :param sort_dir: direction to sort. "asc" or "desc". Default: asc.
        """
        return self._get_services_collection(marker, limit, sort_key,
                                             sort_dir)

    @wsme_pecan.wsexpose(ServiceCollection, types.uuid,
                         types.uuid, int, wtypes.text, wtypes.text)
    def detail(self, service_uuid=None, marker=None, limit=None,
               sort_key='id', sort_dir='asc'):
        """Retrieve a list of services with detail.

        :param service_uuid: UUID of a service, to get only
               services for that service.
        :param marker: pagination marker for large data sets.
        :param limit: maximum number of resources to return in a single result.
        :param sort_key: column to sort results by. Default: id.
        :param sort_dir: direction to sort. "asc" or "desc". Default: asc.
        """
        # NOTE(lucasagomes): /detail should only work agaist collections
        parent = pecan.request.path.split('/')[:-1][-1]
        if parent != "services":
            raise exception.HTTPNotFound

        expand = True
        resource_url = '/'.join(['services', 'detail'])
        return self._get_services_collection(marker, limit,
                                             sort_key, sort_dir, expand,
                                             resource_url)

    @wsme_pecan.wsexpose(Service, types.uuid_or_name)
    def get_one(self, service_ident):
        """Retrieve information about the given service.

        :param service_ident: UUID or logical name of the service.
        """
        if self.from_services:
            raise exception.OperationNotPermitted

        rpc_service = api_utils.get_rpc_resource('Service', service_ident)

        return Service.convert_with_links(rpc_service)

    @wsme_pecan.wsexpose(Service, body=Service, status_code=201)
    def post(self, service):
        """Create a new service.

        :param service: a service within the request body.
        """
        if self.from_services:
            raise exception.OperationNotPermitted

        service.parse_manifest()
        service_dict = service.as_dict()
        context = pecan.request.context
        auth_token = context.auth_token_info['token']
        service_dict['project_id'] = auth_token['project']['id']
        service_dict['user_id'] = auth_token['user']['id']
        service_obj = objects.Service(context, **service_dict)
        new_service = pecan.request.rpcapi.service_create(service_obj)
        if new_service is None:
            raise exception.InvalidState()

        # Set the HTTP Location Header
        pecan.response.location = link.build_url('services', new_service.uuid)
        return Service.convert_with_links(new_service)

    @wsme.validate(types.uuid, [ServicePatchType])
    @wsme_pecan.wsexpose(Service, types.uuid, body=[ServicePatchType])
    def patch(self, service_uuid, patch):
        """Update an existing service.

        :param service_uuid: UUID of a service.
        :param patch: a json PATCH document to apply to this service.
        """
        if self.from_services:
            raise exception.OperationNotPermitted

        rpc_service = objects.Service.get_by_uuid(pecan.request.context,
                                                  service_uuid)
        try:
            service_dict = rpc_service.as_dict()
            service = Service(**api_utils.apply_jsonpatch(service_dict, patch))
        except api_utils.JSONPATCH_EXCEPTIONS as e:
            raise exception.PatchError(patch=patch, reason=e)

        # Update only the fields that have changed
        for field in objects.Service.fields:
            # ignore manifest_url as it was used for create service
            if field == 'manifest_url':
                continue
            if field == 'manifest':
                continue
            try:
                patch_val = getattr(service, field)
            except AttributeError:
                # Ignore fields that aren't exposed in the API
                continue
            if patch_val == wtypes.Unset:
                patch_val = None
            if rpc_service[field] != patch_val:
                rpc_service[field] = patch_val

        rpc_service.save()
        return Service.convert_with_links(rpc_service)

    @wsme_pecan.wsexpose(None, types.uuid_or_name, status_code=204)
    def delete(self, service_ident):
        """Delete a service.

        :param service_ident: UUID or logical name of a service.
        """
        if self.from_services:
            raise exception.OperationNotPermitted

        rpc_service = api_utils.get_rpc_resource('Service', service_ident)

        pecan.request.rpcapi.service_delete(rpc_service.uuid)
