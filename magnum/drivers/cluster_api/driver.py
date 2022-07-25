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
from oslo_concurrency import processutils
from oslo_log import log as logging

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
            elif k8s_status == ClusterStatus.ERROR:
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
            elif k8s_status == ClusterStatus.ERROR:
                cluster.status = fields.ClusterStatus.UPDATE_FAILED
                cluster.status_reason = "cluster is in error state"
                cluster.save()
            # if pending or not found, we just leave it in create in progress
            return

        if previous_state == fields.ClusterStatus.DELETE_IN_PROGRESS:
            LOG.info("Checking on a delete for %s", cluster.uuid)
            if k8s_status == ClusterStatus.NOT_FOUND:
                cluster.status = fields.ClusterStatus.DELETE_COMPLETE
                cluster.status_reason = "cluster deleted"
                cluster.save()
            elif k8s_status == ClusterStatus.ERROR:
                # anything else, delete failed, lets go to error
                cluster.status = fields.ClusterStatus.DELETE_FAILED
                cluster.status_reason = "cluster is in error state"
                cluster.save()
            return

        # what should we do by default here?
        # do we need to copy aggregate_nodegroup_statuses?
        cluster.status = fields.ClusterStatus.UPDATE_FAILED
        cluster.status_reason = "unexpected state!"
        cluster.save()

    def _get_cluster_status(self, cluster):
        # TODO(johngarbutt): try to find the cluster and see if its ready
        # return ClusterStatus.NOT_FOUND
        # return ClusterStatus.PENDING
        # return ClusterStatus.READY
        return ClusterStatus.ERROR

    def _get_resource(self, resource_type, name):
        try:
            stdout, stderr = utils.execute(
                "kubectl", "get", resource_type, name,
                "-o", "json", timeout=120)
        except processutils.ProcessExecutionError as e:
            if e.exit_code == 1 and "Error from server (NotFound)" in e.stderr:
                # Return None when we can't find the resource
                return None
            raise
        # try to parse the json response
        return json.loads(stdout)

    def _apply_resources(self, resources_string):
        LOG.debug("applying in k8s: %s", resources_string)
        utils.execute(
            "kubectl", "apply", "-f", "-",
            timeout=120, process_input=resources_string)

    def _delete_resources(self, resources_string):
        LOG.debug("Delete in k8s: %s", resources_string)
        utils.execute(
            "kubectl", "delete", "--ignore-not-found=true", "-f", "-",
            timeout=120, process_input=resources_string)

    def _generate_values(self, context, cluster, cluster_template):
        # This creates CRDs for use with:
        # https://github.com/stackhpc/azimuth-capi-operator
        # For details on installing that please see:
        # https://stackhpc.github.io/azimuth-config/try/
        template_dir = pathlib.Path(__file__).parent / "templates"
        cluster_jinja_file = template_dir / "values.jinja2.yaml"

        template_jinja_source = cluster_jinja_file.read_text()
        template_jinja = jinja2.Template(template_jinja_source)
        return template_jinja.render(dict(
            template=cluster_template, cluster=cluster))

    def create_cluster(self, context, cluster, cluster_create_timeout):
        LOG.info("Starting to create cluster %s", cluster.uuid)

        # usually the stack_id is being set, but we don't use heat
        cluster.stack_id = None

        # TODO(johngarbutt): create an app cred, then upload as a
        # secret, then remove only after cluster is deleted.
        # ... but for now we assume there is a pre-existing project
        # secret
        values = self._generate_values(
            context, cluster, cluster.cluster_template)
        # TODO(johngarbutt): release name should use sanitised name
        self._helm_install(cluster.uuid, values)

    def _helm_install(self, release_name, values):
        # TODO(johngarbutt) add config for chart_ref
        chart_ref = ("https://stackhpc.github.io/capi-helm-charts/"
                     + "openstack-cluster-0.1.0.tgz")
        LOG.debug("install in helm: %s %s", release_name, values)
        stdout, stderr = utils.execute(
            "helm", "install",
            release_name,
            chart_ref,
            "--output", "json",
            "--timeout", "5m",
            "--values", "-",
            timeout=310,
            process_input=values)
        return json.loads(stdout)

    def _helm_uninstall(self, release_name):
        # TODO(johngarbutt) add config for chart_ref
        LOG.debug("uninstall in helm: %s", release_name)
        stdout, stderr = utils.execute(
            "helm", "uninstall",
            release_name,
            "--output", "json",
            "--timeout", "5m",
            timeout=310)
        return json.loads(stdout)

    def update_cluster(self, context, cluster, scale_manager=None,
                       rollback=False):
        LOG.info("Starting to update cluster %s", cluster.uuid)
        raise Exception("not implemented yet!")

    def delete_cluster(self, context, cluster):
        LOG.info("Starting to delete cluster %s", cluster.uuid)
        self._helm_uninstall(cluster.uuid)

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
