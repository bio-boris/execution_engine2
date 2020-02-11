import unittest
from configparser import ConfigParser
import os
from lib.execution_engine2.SDKMethodRunner import SDKMethodRunner
import copy


class ee2_SDKMethodRunner_test(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        config_file = os.environ.get("KB_DEPLOYMENT_CONFIG", "test/deploy.cfg")
        config_parser = ConfigParser()
        config_parser.read(config_file)

        cls.cfg = {}
        for nameval in config_parser.items("execution_engine2"):
            cls.cfg[nameval[0]] = nameval[1]

        mongo_in_docker = cls.cfg.get("mongo-in-docker-compose", None)
        if mongo_in_docker is not None:
            cls.cfg["mongo-host"] = cls.cfg["mongo-in-docker-compose"]

        cls.user_id = "wsadmin"
        cls.ws_id = 9999
        cls.token = "token"

        cls.method_runner = SDKMethodRunner(
            cls.cfg, user_id=cls.user_id, token=cls.token
        )

    def getRunner(self) -> SDKMethodRunner:
        return copy.deepcopy(self.__class__.method_runner)

    def test_cg(self):
        runner = self.getRunner()
        method = "simple_app.simple_add"
        app_settings = runner._get_client_groups(method)
        self.assertEquals(app_settings["client_group"], "njs")
        client_group = app_settings.get("client_group", None)

        print(client_group)

        # These are for saving into job inputs. Maybe its best to pass this into condor as well?
        # extracted_resources = self.get_condor().extract_resources(cgrr=app_settings)


# def test_get_client_groups(self):
#     runner = self.getRunner()
#
#     client_groups = runner._get_client_groups(
#         "kb_uploadmethods.import_sra_from_staging"
#     )
#
#     expected_groups = "kb_upload"  # expected to fail if CI catalog is updated
#     self.assertCountEqual(expected_groups, client_groups)
#     client_groups = runner._get_client_groups("MEGAHIT.run_megahit")
#     self.assertEqual(0, len(client_groups))
#
#     with self.assertRaises(ValueError) as context:
#         runner._get_client_groups("kb_uploadmethods")
#
#     self.assertIn("unrecognized method:", str(context.exception.args))
#
#
# def test_get_module_git_commit(self):
#     runner = self.getRunner()
#
#     git_commit_1 = runner._get_module_git_commit("MEGAHIT.run_megahit", "2.2.1")
#     self.assertEqual(
#         "048baf3c2b76cb923b3b4c52008ed77dbe20292d", git_commit_1
#     )  # TODO: works only in CI
#
#     git_commit_2 = runner._get_module_git_commit("MEGAHIT.run_megahit")
#     self.assertTrue(isinstance(git_commit_2, str))
#     self.assertEqual(len(git_commit_1), len(git_commit_2))
#     self.assertNotEqual(git_commit_1, git_commit_2)
