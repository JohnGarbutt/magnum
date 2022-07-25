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

from unittest import mock

from magnum.common import utils
from magnum.drivers.cluster_api import driver
from magnum.objects import fields
from magnum.tests.unit.db import base
from magnum.tests.unit.objects import utils as obj_utils


class ClusterAPIDriverTest(base.DbTestCase):

    def setUp(self):
        super(ClusterAPIDriverTest, self).setUp()
        self.driver = driver.Driver()
        self.cluster_obj = obj_utils.create_test_cluster(
            self.context, name='cluster_example_A',
            master_flavor_id="flavor_small",
            flavor_id="flavor_medium")
        self.cluster_template = self.cluster_obj.cluster_template
        self.cluster_template.labels = {'kube_tag': 'v1.24.3'}

    @mock.patch.object(driver.Driver, "_generate_values")
    @mock.patch.object(driver.Driver, "_helm_install")
    def test_create_cluster(self, mock_install, mock_generate):
        mock_generate.return_value = "resources"
        self.driver.create_cluster(self.context, self.cluster_obj, 100)
        mock_install.assert_called_once_with(
                self.cluster_obj.uuid, "resources")

    @mock.patch.object(driver.Driver, "_generate_values")
    @mock.patch.object(driver.Driver, "_helm_uninstall")
    def test_delete_cluster(self, mock_uninstall, mock_generate):
        mock_generate.return_value = "resources"
        self.driver.delete_cluster(self.context, self.cluster_obj)
        mock_uninstall.assert_called_once_with(self.cluster_obj.uuid)

    def test_generate_values(self):
        # TODO(johngarbutt): why is the default group count 9 not 3?
        obj_utils.create_test_nodegroup(
            self.context, name='test_ng"3', cluster_id=self.cluster_obj.uuid,
            uuid='27e3153e-d5bf-4b7e-b517-fb518e17f34c',
            project_id=self.cluster_obj.project_id, is_default=False,
            image_id='test-image3',
            flavor_id='flavor_large')
        obj_utils.create_test_nodegroup(
            self.context, name='test_asdf', cluster_id=self.cluster_obj.uuid,
            uuid='17e3153e-d5bf-4b7e-b517-fb518e17f34d',
            project_id=self.cluster_obj.project_id, is_default=False,
            image_id='test-image2', id=3,
            flavor_id='flavor_xlarge')
        actual = self.driver._generate_values(
            self.context, self.cluster_obj, self.cluster_template)
        expected = '''---
# Helm values for
# https://stackhpc.github.io/capi-helm-charts/openstack-cluster-0.1.0.tgz
# or something else that supports the same interface

# From the template
kubernetesVersion: "v1.24.3"
machineImage: "ubuntu"

# Cluster specific, or inherited
cloudCredentialsSecretName: "project-fake_project"

controlPlane:
  # TODO: check at least 2 CPU, 4GB RAM
  machineFlavor: "flavor_small"
  machineCount: 3

nodeGroups:
  - name: default
    machineFlavor: "flavor_medium"
    machineCount: 9
    autoscale: false
    # machineCountMin
    # machineCountMax
  - name: "17e3153e-d5bf-4b7e-b517-fb518e17f34d"
    machineFlavor: "flavor_xlarge"
    machineCount: 3
    autoscale: false
  - name: "27e3153e-d5bf-4b7e-b517-fb518e17f34c"
    machineFlavor: "flavor_large"
    machineCount: 3
    autoscale: false'''
        self.assertEqual(expected, actual)

    @mock.patch.object(utils, "execute")
    def test_helm_install(self, mock_execute):
        mock_execute.return_value = ("{}", None)
        self.driver._helm_install("uuid1", "asdf")
        mock_execute.assert_called_once_with(
            'helm', 'install', 'uuid1',
            'https://stackhpc.github.io/capi-helm-charts/'
            'openstack-cluster-0.1.0.tgz',
            '--output', 'json', '--timeout', '5m', '--values', '-',
            timeout=310, process_input='asdf')

    @mock.patch.object(utils, "execute")
    def test_helm_uninstall(self, mock_execute):
        mock_execute.return_value = ("{}", None)
        self.driver._helm_uninstall("uuid1")
        mock_execute.assert_called_once_with(
            'helm', 'uninstall', 'uuid1',
            '--output', 'json', '--timeout', '5m',
            timeout=310)

    @mock.patch.object(utils, "execute")
    def test_apply_resources(self, mock_execute):
        self.driver._apply_resources("asdf")
        mock_execute.assert_called_once_with(
            'kubectl', 'apply', '-f', '-',
            timeout=120, process_input='asdf')

    @mock.patch.object(utils, "execute")
    def test_delete_resources(self, mock_execute):
        self.driver._delete_resources("asdf")
        mock_execute.assert_called_once_with(
            'kubectl', 'delete', '--ignore-not-found=true', '-f', '-',
            timeout=120, process_input='asdf')

    def test_upgrade_raises(self):
        def_ng = self.cluster_obj.default_ng_worker
        self.assertRaises(
            NotImplementedError, self.driver.upgrade_cluster,
            self.context, self.cluster_obj,
            self.cluster_template, 1, def_ng)

    def test_update_cluster_status_create_failed(self):
        self.cluster_obj.status = fields.ClusterStatus.CREATE_IN_PROGRESS

        self.driver.update_cluster_status(self.context, self.cluster_obj)

        self.assertEqual(fields.ClusterStatus.CREATE_FAILED,
                         self.cluster_obj.status)
        self.assertEqual("cluster is in error state",
                         self.cluster_obj.status_reason)

    @mock.patch.object(driver.Driver, "_get_cluster_status")
    def test_update_cluster_status_create_complete(self, mock_status):
        self.cluster_obj.status = fields.ClusterStatus.CREATE_IN_PROGRESS
        mock_status.return_value = driver.ClusterStatus.READY

        self.driver.update_cluster_status(self.context, self.cluster_obj)

        self.assertEqual(fields.ClusterStatus.CREATE_COMPLETE,
                         self.cluster_obj.status)
        self.assertEqual("cluster is ready",
                         self.cluster_obj.status_reason)

    @mock.patch.object(driver.Driver, "_get_cluster_status")
    def test_update_cluster_status_create_in_progress(self, mock_status):
        self.cluster_obj.status = fields.ClusterStatus.CREATE_IN_PROGRESS
        mock_status.return_value = driver.ClusterStatus.PENDING

        self.driver.update_cluster_status(self.context, self.cluster_obj)

        self.assertEqual(fields.ClusterStatus.CREATE_IN_PROGRESS,
                         self.cluster_obj.status)

    @mock.patch.object(driver.Driver, "_get_cluster_status")
    def test_update_cluster_status_update_complete(self, mock_status):
        self.cluster_obj.status = fields.ClusterStatus.UPDATE_IN_PROGRESS
        mock_status.return_value = driver.ClusterStatus.READY

        self.driver.update_cluster_status(self.context, self.cluster_obj)

        self.assertEqual(fields.ClusterStatus.UPDATE_COMPLETE,
                         self.cluster_obj.status)
        self.assertEqual("cluster is ready",
                         self.cluster_obj.status_reason)

    @mock.patch.object(driver.Driver, "_get_cluster_status")
    def test_update_cluster_status_update_failed(self, mock_status):
        self.cluster_obj.status = fields.ClusterStatus.UPDATE_IN_PROGRESS
        mock_status.return_value = driver.ClusterStatus.ERROR

        self.driver.update_cluster_status(self.context, self.cluster_obj)

        self.assertEqual(fields.ClusterStatus.UPDATE_FAILED,
                         self.cluster_obj.status)
        self.assertEqual("cluster is in error state",
                         self.cluster_obj.status_reason)

    @mock.patch.object(driver.Driver, "_get_cluster_status")
    def test_update_cluster_status_delete_complete(self, mock_status):
        self.cluster_obj.status = fields.ClusterStatus.DELETE_IN_PROGRESS
        mock_status.return_value = driver.ClusterStatus.NOT_FOUND

        self.driver.update_cluster_status(self.context, self.cluster_obj)

        self.assertEqual(fields.ClusterStatus.DELETE_COMPLETE,
                         self.cluster_obj.status)
        self.assertEqual("cluster deleted",
                         self.cluster_obj.status_reason)

    @mock.patch.object(driver.Driver, "_get_cluster_status")
    def test_update_cluster_status_delete_failed(self, mock_status):
        self.cluster_obj.status = fields.ClusterStatus.DELETE_IN_PROGRESS
        mock_status.return_value = driver.ClusterStatus.ERROR

        self.driver.update_cluster_status(self.context, self.cluster_obj)

        self.assertEqual(fields.ClusterStatus.DELETE_FAILED,
                         self.cluster_obj.status)
        self.assertEqual("cluster is in error state",
                         self.cluster_obj.status_reason)

    @mock.patch.object(driver.Driver, "_get_cluster_status")
    def test_update_cluster_status_catch_all(self, mock_status):
        self.cluster_obj.status = fields.ClusterStatus.DELETE_COMPLETE
        mock_status.return_value = driver.ClusterStatus.ERROR

        self.driver.update_cluster_status(self.context, self.cluster_obj)

        self.assertEqual(fields.ClusterStatus.UPDATE_FAILED,
                         self.cluster_obj.status)
        self.assertEqual("unexpected state!",
                         self.cluster_obj.status_reason)
