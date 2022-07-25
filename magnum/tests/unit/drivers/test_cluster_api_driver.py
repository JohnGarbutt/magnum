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
            self.context, name='cluster_example_A', image_id='test-image1',
            trust_id="trustid", trustee_password="pass",
            trustee_username="user")
        self.cluster_obj.refresh()
        self.cluster_template = self.cluster_obj.cluster_template
        self.cluster_template.labels = {'kube_tag': 'v1.24.3'}
        self.nodegroup_obj = obj_utils.create_test_nodegroup(
            self.context, name='test_ng"3', cluster_id=self.cluster_obj.uuid,
            uuid='27e3153e-d5bf-4b7e-b517-fb518e17f34c',
            project_id=self.cluster_obj.project_id, is_default=False,
            image_id='test-image1')
        self.nodegroup_obj.refresh()

    @mock.patch.object(driver.Driver, "_generate_resources")
    @mock.patch.object(driver.Driver, "_apply_resources")
    def test_create_cluster(self, mock_apply, mock_generate):
        mock_generate.return_value = "resources"
        self.driver.create_cluster(self.context, self.cluster_obj, 100)
        mock_apply.assert_called_once_with("resources")

    @mock.patch.object(driver.Driver, "_generate_resources")
    @mock.patch.object(driver.Driver, "_delete_resources")
    def test_delete_cluster(self, mock_delete, mock_generate):
        mock_generate.return_value = "resources"
        self.driver.delete_cluster(self.context, self.cluster_obj)
        mock_delete.assert_called_once_with("resources")

    @mock.patch.object(driver.Driver, "_generate_resources")
    @mock.patch.object(driver.Driver, "_apply_resources")
    def test_update_cluster(self, mock_apply, mock_generate):
        mock_generate.return_value = "resources"
        self.driver.update_cluster(self.context, self.cluster_obj)
        mock_apply.assert_called_once_with("resources")

    def test_generate_resources_no_template(self):
        actual = self.driver._generate_resources(
            self.context, self.cluster_obj)
        expected = '''---
apiVersion: v1
kind: Secret
metadata:
  name: "cluster-5d12f6fd-a196-4bf0-ae4c-1f639a523a52"
type: Opaque
stringData:
  clouds.yaml:
      clouds:
          openstack:
              identity_api_version: 3
              interface: "public"
              auth:
                  auth_url: "None"
                  project_id: "fake_project"
                  trust_id: "trustid"
                  username: "user"
                  password: "pass"
---
apiVersion: azimuth.stackhpc.com/v1alpha1
kind: Cluster
metadata:
  name: "5d12f6fd-a196-4bf0-ae4c-1f639a523a52"
spec:
  addons:
    apps: true
    certManager: false
    dashboard: true
    ingress: false
    monitoring: true
  autohealing: true
  cloudCredentialsSecretName: "cluster-5d12f6fd-a196-4bf0-ae4c-1f639a523a52"
  controlPlaneMachineSize: "None"
  label: "cluster_example_A"
  machineRootVolumeSize: 0
  nodeGroups:
  - autoscale: false
    count: "6"
    machineSize: "None"
    name: "default"
  - autoscale: false
    count: "3"
    machineSize: "None"
    name: "test_ng%223"
  templateName: "e74c40e0-d825-11e2-a28f-0800200c9a66"'''
        self.assertEqual(expected, actual)

    def test_generate_resources_with_template(self):
        actual = self.driver._generate_resources(
            self.context, self.cluster_obj, self.cluster_template)
        expected = '''---
apiVersion: azimuth.stackhpc.com/v1alpha1
kind: ClusterTemplate
metadata:
  name: "e74c40e0-d825-11e2-a28f-0800200c9a66"
spec:
  deprecated: "False"
  label: "clustermodel1"
  values:
    global:
      kubernetesVersion: "v1.24.3"
    machineImageId: "ubuntu"
---
apiVersion: v1
kind: Secret
metadata:
  name: "cluster-5d12f6fd-a196-4bf0-ae4c-1f639a523a52"
type: Opaque
stringData:
  clouds.yaml:
      clouds:
          openstack:
              identity_api_version: 3
              interface: "public"
              auth:
                  auth_url: "None"
                  project_id: "fake_project"
                  trust_id: "trustid"
                  username: "user"
                  password: "pass"
---
apiVersion: azimuth.stackhpc.com/v1alpha1
kind: Cluster
metadata:
  name: "5d12f6fd-a196-4bf0-ae4c-1f639a523a52"
spec:
  addons:
    apps: true
    certManager: false
    dashboard: true
    ingress: false
    monitoring: true
  autohealing: true
  cloudCredentialsSecretName: "cluster-5d12f6fd-a196-4bf0-ae4c-1f639a523a52"
  controlPlaneMachineSize: "None"
  label: "cluster_example_A"
  machineRootVolumeSize: 0
  nodeGroups:
  - autoscale: false
    count: "6"
    machineSize: "None"
    name: "default"
  - autoscale: false
    count: "3"
    machineSize: "None"
    name: "test_ng%223"
  templateName: "e74c40e0-d825-11e2-a28f-0800200c9a66"'''
        self.assertEqual(expected, actual)

    @mock.patch.object(utils, "execute")
    def test_update_cluster_status_fallback(self, mock_execute):
        mock_execute.return_value = ("{}", None)
        mock_cluster = mock.MagicMock()
        mock_cluster.uuid = "uuid1"

        self.driver.update_cluster_status(self.context, mock_cluster)

        self.assertEqual("UPDATE_FAILED", mock_cluster.status)
        self.assertEqual("unexpected state!", mock_cluster.status_reason)
        mock_cluster.save.assert_called_once_with()
        mock_execute.assert_called_once_with(
            'kubectl', 'get', 'cluster.azimuth.stackhpc.com', 'uuid1',
            '-o', 'json', timeout=120)

    @mock.patch.object(driver.Driver, "_get_resource")
    def test_update_cluster_status_create_not_complete(self, mock_get):
        mock_get.return_value = {"status": {"phase": "Reconciling"}}
        mock_cluster = mock.MagicMock()
        mock_cluster.uuid = "uuid1"
        mock_cluster.status = fields.ClusterStatus.CREATE_IN_PROGRESS

        self.driver.update_cluster_status(self.context, mock_cluster)

        self.assertEqual("CREATE_IN_PROGRESS", mock_cluster.status)
        mock_cluster.save.assert_not_called()

    @mock.patch.object(driver.Driver, "_get_resource")
    def test_update_cluster_status_create_complete(self, mock_get):
        mock_get.return_value = {"status": {"phase": "Ready"}}
        mock_cluster = mock.MagicMock()
        mock_cluster.uuid = "uuid1"
        mock_cluster.status = fields.ClusterStatus.CREATE_IN_PROGRESS

        self.driver.update_cluster_status(self.context, mock_cluster)

        self.assertEqual("CREATE_COMPLETE", mock_cluster.status)
        self.assertEqual("cluster is ready", mock_cluster.status_reason)
        mock_cluster.save.assert_called_once_with()

    @mock.patch.object(driver.Driver, "_get_resource")
    def test_update_cluster_status_create_failed(self, mock_get):
        mock_get.return_value = {"status": {"phase": "Failed"}}
        mock_cluster = mock.MagicMock()
        mock_cluster.uuid = "uuid1"
        mock_cluster.status = fields.ClusterStatus.CREATE_IN_PROGRESS

        self.driver.update_cluster_status(self.context, mock_cluster)

        self.assertEqual("CREATE_FAILED", mock_cluster.status)
        self.assertEqual("cluster is in error state",
                         mock_cluster.status_reason)
        mock_cluster.save.assert_called_once_with()

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
            'kubectl', 'delete', '-f', '-',
            timeout=120, process_input='asdf')

    def test_upgrade_raises(self):
        def_ng = self.cluster_obj.default_ng_worker
        self.assertRaises(
            NotImplementedError, self.driver.upgrade_cluster,
            self.context, self.cluster_obj,
            self.cluster_template, 1, def_ng)
