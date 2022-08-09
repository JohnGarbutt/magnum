# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.

import enum
import json
import pathlib

import jinja2
from oslo_log import log as logging

from magnum.common import clients
from magnum.common import utils
from magnum.drivers.common import driver
from magnum.objects import fields

LOG = logging.getLogger(__name__)


class ClusterStatus(enum.Enum):
    READY = 1
    PENDING = 2
    ERROR = 3
    NOT_FOUND = 4


class Driver(driver.Driver):

    @property
    def provides(self):
        return [
            {'server_type': 'vm',
             # NOTE(johngarbutt) we could support any cluster api
             # supported image, but lets start with ubuntu for now.
             # TODO(johngarbutt) os list should probably come from config?
             'os': 'ubuntu',
             'coe': 'kubernetes'},
        ]

    def update_cluster_status(self, context, cluster):
        previous_state = cluster.status
        k8s_status = self._get_cluster_status(cluster)

        if previous_state == fields.ClusterStatus.CREATE_IN_PROGRESS:
            LOG.info("Checking on a create for %s", cluster.uuid)
            if k8s_status == ClusterStatus.READY:
                cluster.status = fields.ClusterStatus.CREATE_COMPLETE
                cluster.status_reason = "cluster is ready"
                cluster.save()
            if k8s_status == ClusterStatus.ERROR:
                cluster.status = fields.ClusterStatus.CREATE_FAILED
                cluster.status_reason = "cluster is in error state"
                cluster.save()
            # if pending or not found, we just leave it in create in progress
            return

        if previous_state == fields.ClusterStatus.UPDATE_IN_PROGRESS:
            LOG.info("Checking on a create for %s", cluster.uuid)
            if k8s_status == ClusterStatus.READY:
                cluster.status = fields.ClusterStatus.UPDATE_COMPLETE
                cluster.status_reason = "cluster is ready"
                cluster.save()
            if k8s_status == ClusterStatus.ERROR:
                cluster.status = fields.ClusterStatus.UPDATE_FAILED
                cluster.status_reason = "cluster is in error state"
                cluster.save()
            # if pending or not found, we just leave it in create in progress
            return

        if previous_state == fields.ClusterStatus.DELETE_IN_PROGRESS:
            LOG.info("Checking on a delete for %s", cluster.uuid)
            if k8s_status == ClusterStatus.NOT_FOUND:
                cluster.status = fields.ClusterStatus.DELETE_COMPLETE
                cluster.status_reason = "cluster is not found"
                cluster.save()
            if k8s_status == ClusterStatus.ERROR:
                cluster.status = fields.ClusterStatus.CREATE_FAILED
                cluster.status_reason = "cluster is in error state"
                cluster.save()
            # otherwise we are still waiting for the delete
            return

        # what should we do by default here?
        # do we need to copy aggregate_nodegroup_statuses?
        cluster.status = fields.ClusterStatus.UPDATE_FAILED
        cluster.status_reason = "unexpected state!"
        cluster.save()

    def _get_cluster_status(self, cluster):
        resource = self._get_resource(
            "cluster.azimuth.stackhpc.com", cluster.uuid)
        if not resource:
            # We might not have created the CRD yet, show pending
            # or a delete might have completed, so we can't find it
            return ClusterStatus.NOT_FOUND

        phase = resource.get("status", {}).get("phase")
        if not phase:
            # CRD created but operator not yet added a status
            return ClusterStatus.PENDING

        last_handled_str = resource.get("metadata", {})\
            .get("annotations", {})\
            .get("azimuth.stackhpc.com/last-handled-configuration")
        last_handled_spec = None
        if last_handled_str:
            last_handled_spec = json.loads(last_handled_str)
        if last_handled_spec != resource.get("spec"):
            # operator hasn't yet seen our updates
            return ClusterStatus.PENDING

        LOG.debug("Current status for cluster: %s", phase)
        if phase == "Ready":
            return ClusterStatus.READY

        if phase == "Failed" or phase == "Unhealthy":
            return ClusterStatus.ERROR

        # Reconciling, Upgrading, Deleting, Unknown
        return ClusterStatus.PENDING

    def _get_resource(self, resource_type, name):
        # FIXME(johngarbutt): deal with not found errors
        stdout, stderr = utils.execute(
            "kubectl", "get", resource_type, name,
            "-o", "json", timeout=120)
        return json.loads(stdout)

    def _apply_resources(self, resources_string):
        LOG.debug("Needs applying in k8s: %s", resources_string)
        utils.execute(
            "kubectl", "apply", "-f", "-",
            timeout=120, process_input=resources_string)

    def _delete_resources(self, resources_string):
        LOG.debug("Delete in k8s: %s", resources_string)
        utils.execute(
            "kubectl", "delete", "--ignore-not-found=false", "-f", "-",
            timeout=120, process_input=resources_string)

    def _generate_resources(self, context, cluster, cluster_template=None):
        # This creates CRDs for use with:
        # https://github.com/stackhpc/azimuth-capi-operator
        # For details on installing that please see:
        # https://stackhpc.github.io/azimuth-config/try/
        template_dir = pathlib.Path(__file__).parent / "templates"
        cluster_jinja_file = template_dir / "cluster.yaml.jinja2"
        template_jinja_file = template_dir / "clustertemplate.yaml.jinja2"

        output = ""
        if cluster_template:
            template_jinja_source = template_jinja_file.read_text()
            template_jinja = jinja2.Template(template_jinja_source)
            output = template_jinja.render(dict(
                template=cluster_template)) + "\n"

        cluster_jinja_source = cluster_jinja_file.read_text()
        cluster_jinja = jinja2.Template(cluster_jinja_source)
        osc = clients.OpenStackClients(context)
        output += cluster_jinja.render(dict(
            cluster=cluster, auth_url=osc.auth_url))

        return output

    def create_cluster(self, context, cluster, cluster_create_timeout):
        LOG.info("Starting to create cluster %s", cluster.uuid)

        # usually the stack_id is being set, but we don't use heat
        cluster.stack_id = None

        resources = self._generate_resources(
            context, cluster, cluster.cluster_template)
        self._apply_resources(resources)

        # who polls to check the status in heat API?

    def update_cluster(self, context, cluster, scale_manager=None,
                       rollback=False):
        LOG.info("Starting to update cluster %s", cluster.uuid)
        resources = self._generate_resources(
            context, cluster, cluster.cluster_template)
        self._apply_resources(resources)

    def delete_cluster(self, context, cluster):
        LOG.info("Starting to delete cluster %s", cluster.uuid)
        # NOTE(johngarbutt): no one deletes old templates
        resources = self._generate_resources(context, cluster)
        self._delete_resources(resources)

    def resize_cluster(self, context, cluster, resize_manager, node_count,
                       nodes_to_remove, nodegroup=None):
        raise Exception("don't support removing nodes this way yet")

    def upgrade_cluster(self, context, cluster, cluster_template,
                        max_batch_size, nodegroup, scale_manager=None,
                        rollback=False):
        raise NotImplementedError("don't support upgrade yet")

    def create_nodegroup(self, context, cluster, nodegroup):
        raise Exception("we don't support node groups yet")

    def update_nodegroup(self, context, cluster, nodegroup):
        raise Exception("we don't support node groups yet")

    def delete_nodegroup(self, context, cluster, nodegroup):
        raise Exception("we don't support node groups yet")

    def create_federation(self, context, federation):
        return NotImplementedError("Will not implement 'create_federation'")

    def update_federation(self, context, federation):
        return NotImplementedError("Will no implement 'update_federation'")

    def delete_federation(self, context, federation):
        return NotImplementedError("Will not implement 'delete_federation'")
