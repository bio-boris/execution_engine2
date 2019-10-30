# -*- coding: utf-8 -*-
from typing import Dict, List
import copy
import json
import logging
import os
import unittest
from configparser import ConfigParser
from datetime import datetime, timedelta
from unittest.mock import patch
import dateutil

from mock import MagicMock
from mongoengine import ValidationError

from execution_engine2.exceptions import InvalidStatusTransitionException
from execution_engine2.db.models.models import (
    Job,
    JobInput,
    Meta,
    Status,
    JobLog,
    TerminatedCode,
)
from execution_engine2.utils.Condor import submission_info
from execution_engine2.db.MongoUtil import MongoUtil
from execution_engine2.SDKMethodRunner import SDKMethodRunner
from test.mongo_test_helper import MongoTestHelper
from test.test_utils import (
    bootstrap,
    get_example_job,
    validate_job_state
)
import requests
import requests_mock

logging.basicConfig(level=logging.INFO)
bootstrap()


def _run_job_adapter(ws_perms_info: Dict = None,
                     ws_perms_global: List = [],
                     client_groups_info: Dict = None,
                     module_versions: Dict = None,
                     user_roles: List = None):
    """
    Mocks POST calls to:
        Workspace.get_permissions_mass,
        Catalog.list_client_group_configs,
        Catalog.get_module_version
    Mocks GET calls to:
        Auth (/api/V2/me)
        Auth (/api/V2/token)

    Returns an Adapter for requests_mock that deals with mocking workspace permissions.
    :param ws_perms_info: dict - keys user_id, and ws_perms
            user_id: str - the user id
            ws_perms: dict of permissions, keys are ws ids, values are permission. Example:
                {123: "a", 456: "w"} means workspace id 123 has admin permissions, and 456 has
                write permission
    :param ws_perms_global: list - list of global workspaces - gives those workspaces a global (user "*") permission of "r"
    :param client_groups_info: dict - keys client_groups (list), function_name, module_name
    :param module_versions: dict - key git_commit_hash (str), others aren't used
    :return: an adapter function to be passed to request_mock
    """
    def perm_adapter(request):
        response = requests.Response()
        response.status_code = 200
        rq_method = request.method.upper()
        if rq_method == "POST":
            params = request.json().get("params")
            method = request.json().get("method")

            result = []
            if method == "Workspace.get_permissions_mass":
                perms_req = params[0].get("workspaces")
                ret_perms = []
                user_id = ws_perms_info.get("user_id")
                ws_perms = ws_perms_info.get("ws_perms", {})
                for ws in perms_req:
                    perms = {user_id: ws_perms.get(ws["id"], "n")}
                    if ws["id"] in ws_perms_global:
                        perms["*"] = "r"
                    ret_perms.append(perms)
                result = [{"perms": ret_perms}]
                print(result)
            elif method == "Catalog.list_client_group_configs":
                result = []
                if client_groups_info is not None:
                    result = [client_groups_info]
            elif method == "Catalog.get_module_version":
                result = [{"git_commit_hash": "some_commit_hash"}]
                if module_versions is not None:
                    result = [module_versions]
            response._content = bytes(json.dumps({
                "result": result,
                "version": "1.1"
            }), "UTF-8")
        elif rq_method == "GET":
            if request.url.endswith("/api/V2/me"):
                response._content = bytes(json.dumps({
                    "customroles": user_roles
                }), "UTF-8")
        return response
    return perm_adapter


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

        cls.user_id = "fake_test_user"
        cls.ws_id = 9999
        cls.token = "token"

        cls.method_runner = SDKMethodRunner(cls.cfg, user_id=cls.user_id, token=cls.token)
        cls.mongo_util = MongoUtil(cls.cfg)
        cls.mongo_helper = MongoTestHelper(cls.cfg)

        cls.test_collection = cls.mongo_helper.create_test_db(
            db=cls.cfg["mongo-database"], col=cls.cfg["mongo-jobs-collection"]
        )

    def getRunner(self) -> SDKMethodRunner:
        return copy.deepcopy(self.__class__.method_runner)

    def create_job_rec(self):
        job = Job()

        inputs = JobInput()

        job.user = self.user_id
        job.authstrat = "kbaseworkspace"
        job.wsid = self.ws_id
        job.status = "created"

        job_params = {
            "wsid": self.ws_id,
            "method": "MEGAHIT.run_megahit",
            "app_id": "MEGAHIT/run_megahit",
            "service_ver": "2.2.1",
            "params": [
                {
                    "k_list": [],
                    "k_max": None,
                    "output_contigset_name": "MEGAHIT.contigs",
                }
            ],
            "source_ws_objects": ["a/b/c", "e/d"],
            "parent_job_id": "9998",
        }

        inputs.wsid = job.wsid
        inputs.method = job_params.get("method")
        inputs.params = job_params.get("params")
        inputs.service_ver = job_params.get("service_ver")
        inputs.app_id = job_params.get("app_id")
        inputs.source_ws_objects = job_params.get("source_ws_objects")
        inputs.parent_job_id = job_params.get("parent_job_id")

        inputs.narrative_cell_info = Meta()

        job.job_input = inputs
        job.job_output = None

        with self.mongo_util.mongo_engine_connection():
            job.save()

        return str(job.id)

    def test_init_ok(self):
        class_attri = ["config", "catalog", "workspace", "mongo_util", "condor"]
        runner = self.getRunner()
        self.assertTrue(set(class_attri) <= set(runner.__dict__.keys()))

    #TODO Think about what we want to do here, as this is an integration test and not a unit test
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
    # def test_get_module_git_commit(self):
    #
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
    #    self.assertNotEqual(git_commit_1, git_commit_2)

    def test_init_job_rec(self):
        with self.mongo_util.mongo_engine_connection():
            ori_job_count = Job.objects.count()
            runner = self.getRunner()

            job_params = {
                "wsid": self.ws_id,
                "method": "MEGAHIT.run_megahit",
                "app_id": "MEGAHIT/run_megahit",
                "service_ver": "2.2.1",
                "params": [{"workspace_name": "wjriehl:1475006266615",
                            "read_library_refs": ["18836/5/1"],
                            "output_contigset_name": "rhodo_contigs",
                            "recipe": "auto",
                            "assembler": None,
                            "pipeline": None,
                            "min_contig_len": None}],
                "source_ws_objects": ["a/b/c", "e/d"],
                "parent_job_id": "9998",
                'meta': {'tag': 'dev', 'token_id': '12345'}}

            job_id = runner._init_job_rec(self.user_id, job_params)

            self.assertEqual(ori_job_count, Job.objects.count() - 1)

            job = Job.objects.get(id=job_id)

            self.assertEqual(job.user, self.user_id)
            self.assertEqual(job.authstrat, "kbaseworkspace")
            self.assertEqual(job.wsid, self.ws_id)

            job_input = job.job_input

            self.assertEqual(job_input.wsid, self.ws_id)
            self.assertEqual(job_input.method, "MEGAHIT.run_megahit")
            self.assertEqual(job_input.app_id, "MEGAHIT/run_megahit")
            self.assertEqual(job_input.service_ver, "2.2.1")
            self.assertCountEqual(job_input.source_ws_objects, ["a/b/c", "e/d"])
            self.assertEqual(job_input.parent_job_id, "9998")

            narrative_cell_info = job_input.narrative_cell_info
            self.assertEqual(narrative_cell_info.tag, 'dev')
            self.assertEqual(narrative_cell_info.token_id, '12345')
            self.assertFalse(narrative_cell_info.status)

            self.assertFalse(job.job_output)

            self.mongo_util.get_job(job_id=job_id).delete()
            self.assertEqual(ori_job_count, Job.objects.count())

    @patch("execution_engine2.SDKMethodRunner.SDKMethodRunner", autospec=True)
    def test_cancel_job(self, runner):
        logging.info("\n\n  Test cancel job")
        sdk = copy.deepcopy(self.getRunner())

        with sdk.get_mongo_util().mongo_engine_connection():
            job = get_example_job()
            job.user = self.user_id
            job.wsid = self.ws_id
            job_id = job.save().id

        logging.info(
            f"Created job {job_id} in {job.wsid} status {job.status}. About to cancel"
        )

        sdk.cancel_job(job_id=job_id)

        self.assertEqual(
            Status(sdk.get_mongo_util().get_job(job_id=job_id).status),
            Status.terminated,
        )
        self.assertEqual(
            TerminatedCode(sdk.get_mongo_util().get_job(job_id=job_id).terminated_code),
            TerminatedCode.terminated_by_user,
        )

        with sdk.get_mongo_util().mongo_engine_connection():
            job = get_example_job()
            job.user = self.user_id
            job.wsid = self.ws_id
            job_id = job.save().id

        logging.info(
            f"Created job {job_id} in {job.wsid} status {job.status}. About to cancel"
        )

        sdk.cancel_job(
            job_id=job_id,
            terminated_code=TerminatedCode.terminated_by_automation.value,
        )

        self.assertEqual(
            Status(sdk.get_mongo_util().get_job(job_id=job_id).status),
            Status.terminated,
        )
        self.assertEqual(
            TerminatedCode(sdk.get_mongo_util().get_job(job_id=job_id).terminated_code),
            TerminatedCode.terminated_by_automation,
        )

    @patch("execution_engine2.db.MongoUtil.MongoUtil", autospec=True)
    def test_check_job_canceled(self, mongo_util):
        def generateJob(job_id):
            j = Job()
            j.status = job_id
            return j

        runner = self.getRunner()
        runner.get_mongo_util = MagicMock(return_value=mongo_util)
        mongo_util.get_job = MagicMock(side_effect=generateJob)

        call_count = 0
        rv = runner.check_job_canceled("created")
        self.assertFalse(rv["canceled"])
        self.assertFalse(rv["finished"])
        call_count += 1

        rv = runner.check_job_canceled("estimating")
        self.assertFalse(rv["canceled"])
        self.assertFalse(rv["finished"])
        call_count += 1

        rv = runner.check_job_canceled("queued")
        self.assertFalse(rv["canceled"])
        self.assertFalse(rv["finished"])
        call_count += 1

        rv = runner.check_job_canceled("running")
        self.assertFalse(rv["canceled"])
        self.assertFalse(rv["finished"])
        call_count += 1

        rv = runner.check_job_canceled("finished")
        self.assertFalse(rv["canceled"])
        self.assertTrue(rv["finished"])
        call_count += 1

        rv = runner.check_job_canceled("error")
        self.assertFalse(rv["canceled"])
        self.assertTrue(rv["finished"])
        call_count += 1

        rv = runner.check_job_canceled("terminated")
        self.assertTrue(rv["canceled"])
        self.assertTrue(rv["finished"])
        call_count += 1

        self.assertEqual(call_count, mongo_util.get_job.call_count)
        self.assertEqual(call_count, runner.get_mongo_util.call_count)

    @requests_mock.Mocker()
    @patch("lib.execution_engine2.utils.Condor.Condor", autospec=True)
    def test_run_job(self, rq_mock, condor_mock):
        rq_mock.add_matcher(_run_job_adapter(ws_perms_info={"user_id": self.user_id, "ws_perms": {self.ws_id: "a"}}))
        runner = self.getRunner()
        runner.get_condor = MagicMock(return_value=condor_mock)
        job = get_example_job(user=self.user_id, wsid=self.ws_id).to_mongo().to_dict()
        job["method"] = job["job_input"]["app_id"]
        job["app_id"] = job["job_input"]["app_id"]

        si = submission_info(clusterid="test", submit=job, error=None)
        condor_mock.run_job = MagicMock(return_value=si)

        job_id = runner.run_job(params=job)
        print(f"Job id is {job_id} ")

    @requests_mock.Mocker()
    @patch("lib.execution_engine2.utils.Condor.Condor", autospec=True)
    def test_run_job_and_add_log(self, rq_mock, condor_mock):
        """
        This test runs a job and then adds logs

        :param condor_mock:
        :return:
        """
        runner = self.getRunner()
        rq_mock.add_matcher(_run_job_adapter(ws_perms_info={"user_id": self.user_id, "ws_perms": {self.ws_id: "a"}}))
        runner.get_condor = MagicMock(return_value=condor_mock)
        job = get_example_job(user=self.user_id, wsid=self.ws_id).to_mongo().to_dict()
        job["method"] = job["job_input"]["app_id"]
        job["app_id"] = job["job_input"]["app_id"]

        si = submission_info(clusterid="test", submit=job, error=None)
        condor_mock.run_job = MagicMock(return_value=si)

        job_id = runner.run_job(params=job)
        logging.info(f"Job id is {job_id} ")

        lines = []
        for item in ["this", "is", "a", "test"]:
            line = {"error": False, "line": item}
            lines.append(line)

        log_pos_1 = runner.add_job_logs(job_id=job_id, log_lines=lines)
        logging.info(f"After insert log position is now {log_pos_1}")
        log = runner.view_job_logs(job_id=job_id, skip_lines=None)

        log_lines = log["lines"]
        for i, inserted_line in enumerate(log_lines):
            self.assertEqual(inserted_line["line"], lines[i]["line"])

        line1 = {
            "error": False,
            "line": "This is the read deal",
            "ts": str(datetime.now()),
        }
        line2 = {
            "error": False,
            "line": "This is the read deal2",
            "ts": int(datetime.now().timestamp() * 1000),
        }
        line3 = {
            "error": False,
            "line": "This is the read deal3",
            "ts": datetime.now().timestamp(),
        }
        line4 = {
            "error": False,
            "line": "This is the read deal4",
            "ts": str(datetime.now().timestamp()),
        }
        input_lines2 = [line1, line2, line3, line4]

        for line in input_lines2:
            print(line)

        log_pos2 = runner.add_job_logs(job_id=job_id, log_lines=input_lines2)
        logging.info(
            f"After inserting timestamped logs,  log position is now {log_pos2}"
        )

        log = runner.view_job_logs(job_id=job_id, skip_lines=None)
        log_lines = log["lines"]

        print("About to dump log")
        print(json.dumps(log))
        for i, inserted_line in enumerate(log_lines):
            if i < log_pos_1:
                continue

            self.assertEqual(inserted_line["line"], input_lines2[i - log_pos_1]["line"])

            time_input = input_lines2[i - log_pos_1]["ts"]
            if isinstance(time_input, str):
                if time_input.replace('.', '', 1).isdigit():
                    time_input = int(float(time_input) * 1000) if '.' in time_input else int(time_input)
                else:
                    time_input = int(dateutil.parser.parse(time_input).timestamp() * 1000)
            elif isinstance(time_input, float):
                time_input = int(time_input * 1000)

            self.assertEqual(inserted_line['ts'], time_input)

            error1 = line["error"]
            error2 = input_lines2[i - log_pos_1]["error"]
            self.assertEqual(error1, error2)

        # TODO IMPLEMENT SKIPLINES AND TEST

        log = runner.view_job_logs(job_id=job_id, skip_lines=1)
        self.assertEqual(log["lines"][0]["linepos"], 2)

        log = runner.view_job_logs(job_id=job_id, skip_lines=8)
        self.assertEqual(log, {"lines": [], "last_line_number": 8})

    @requests_mock.Mocker()
    def test_add_job_logs_ok(self, rq_mock):
        rq_mock.add_matcher(_run_job_adapter(
            ws_perms_info={"user_id": self.user_id, "ws_perms": {self.ws_id: "a"}},
            user_roles=[]
        ))
        with self.mongo_util.mongo_engine_connection():
            ori_job_log_count = JobLog.objects.count()
            ori_job_count = Job.objects.count()
            job_id = self.create_job_rec()
            self.assertEqual(ori_job_count, Job.objects.count() - 1)

            runner = self.getRunner()

            # create new log
            lines = [{"line": "Hello world"}]
            runner.add_job_logs(job_id=job_id, log_lines=lines)

            updated_job_log_count = JobLog.objects.count()
            self.assertEqual(ori_job_log_count, updated_job_log_count - 1)

            log = self.mongo_util.get_job_log(job_id=job_id)
            ori_updated_time = log.updated
            self.assertTrue(ori_updated_time)
            self.assertEqual(log.original_line_count, 1)
            self.assertEqual(log.stored_line_count, 1)
            ori_lines = log.lines
            self.assertEqual(len(ori_lines), 1)

            test_line = ori_lines[0]

            self.assertEqual(test_line.line, "Hello world")
            self.assertEqual(test_line.linepos, 1)
            self.assertFalse(test_line.error)

            # add job log
            lines = [
                {"error": True, "line": "Hello Kbase"},
                {"line": "Hello Wrold Kbase"},
            ]

            runner.add_job_logs(job_id=job_id, log_lines=lines)

            log = self.mongo_util.get_job_log(job_id=job_id)
            self.assertTrue(log.updated)
            self.assertTrue(ori_updated_time < log.updated)
            self.assertEqual(log.original_line_count, 3)
            self.assertEqual(log.stored_line_count, 3)
            ori_lines = log.lines
            self.assertEqual(len(ori_lines), 3)

            # original line
            test_line = ori_lines[0]

            self.assertEqual(test_line.line, "Hello world")
            self.assertEqual(test_line.linepos, 1)
            self.assertFalse(test_line.error)

            # new line
            test_line = ori_lines[1]

            self.assertEqual(test_line.line, "Hello Kbase")
            self.assertEqual(test_line.linepos, 2)
            self.assertTrue(test_line.error)

            test_line = ori_lines[2]

            self.assertEqual(test_line.line, "Hello Wrold Kbase")
            self.assertEqual(test_line.linepos, 3)
            self.assertFalse(test_line.error)

            self.mongo_util.get_job(job_id=job_id).delete()
            self.assertEqual(ori_job_count, Job.objects.count())

            self.mongo_util.get_job_log(job_id=job_id).delete()
            self.assertEqual(ori_job_log_count, JobLog.objects.count())

    def test_get_job_params(self):

        with self.mongo_util.mongo_engine_connection():
            ori_job_count = Job.objects.count()
            job_id = self.create_job_rec()
            self.assertEqual(ori_job_count, Job.objects.count() - 1)

            runner = self.getRunner()
            runner._test_job_permissions = MagicMock(return_value=True)
            params = runner.get_job_params(job_id)

            expected_params_keys = [
                "wsid",
                "method",
                "params",
                "service_ver",
                "app_id",
                "source_ws_objects",
                "parent_job_id",
            ]
            self.assertCountEqual(params.keys(), expected_params_keys)
            self.assertEqual(params["wsid"], self.ws_id)
            self.assertEqual(params["method"], "MEGAHIT.run_megahit")
            self.assertEqual(params["app_id"], "MEGAHIT/run_megahit")
            self.assertEqual(params["service_ver"], "2.2.1")
            self.assertCountEqual(params["source_ws_objects"], ["a/b/c", "e/d"])
            self.assertEqual(params["parent_job_id"], "9998")

            self.mongo_util.get_job(job_id=job_id).delete()
            self.assertEqual(ori_job_count, Job.objects.count())

    def test_update_job_status(self):

        with self.mongo_util.mongo_engine_connection():
            ori_job_count = Job.objects.count()
            job_id = self.create_job_rec()
            self.assertEqual(ori_job_count, Job.objects.count() - 1)

            runner = self.getRunner()
            runner._test_job_permissions = MagicMock(return_value=True)

            # test missing status
            with self.assertRaises(ValueError) as context:
                runner.update_job_status(None, "invalid_status")
            self.assertEqual(
                "Please provide both job_id and status", str(context.exception)
            )

            # test invalid status
            with self.assertRaises(ValidationError) as context:
                runner.update_job_status(job_id, "invalid_status")
            self.assertIn("is not a valid status", str(context.exception))

            ori_job = Job.objects(id=job_id)[0]
            ori_updated_time = ori_job.updated

            # test update job status
            job_id = runner.update_job_status(job_id, "estimating")
            updated_job = Job.objects(id=job_id)[0]
            self.assertEqual(updated_job.status, "estimating")
            updated_time = updated_job.updated

            self.assertTrue(ori_updated_time < updated_time)

            self.mongo_util.get_job(job_id=job_id).delete()
            self.assertEqual(ori_job_count, Job.objects.count())

    def test_get_job_status(self):

        with self.mongo_util.mongo_engine_connection():
            ori_job_count = Job.objects.count()
            job_id = self.create_job_rec()
            self.assertEqual(ori_job_count, Job.objects.count() - 1)

            runner = self.getRunner()
            runner._test_job_permissions = MagicMock(return_value=True)

            # test missing job_id input
            with self.assertRaises(ValueError) as context:
                runner.get_job_status(None)
            self.assertEqual("Please provide valid job_id", str(context.exception))

            returnVal = runner.get_job_status(job_id)

            self.assertTrue("status" in returnVal)
            self.assertEqual(returnVal["status"], "created")

            self.mongo_util.get_job(job_id=job_id).delete()
            self.assertEqual(ori_job_count, Job.objects.count())

    def test_finish_job(self):

        with self.mongo_util.mongo_engine_connection():
            ori_job_count = Job.objects.count()
            job_id = self.create_job_rec()
            self.assertEqual(ori_job_count, Job.objects.count() - 1)

            job = self.mongo_util.get_job(job_id=job_id)
            self.assertEqual(job.status, "created")
            self.assertFalse(job.finished)

            runner = self.getRunner()
            runner._test_job_permissions = MagicMock(return_value=True)
            runner.catalog.log_exec_stats = MagicMock(return_value=True)

            # test missing job_id input
            with self.assertRaises(ValueError) as context:
                logging.info("Finish Job Case 0 Raises Error")
                runner.finish_job(None)
            self.assertEqual("Please provide valid job_id", str(context.exception))

            # test finish job with invalid status
            with self.assertRaises(ValueError) as context:
                logging.info("Finish Job Case 1 Raises Error")
                runner.finish_job(job_id)
            self.assertIn("Unexpected job status", str(context.exception))

            # update job status to running

            runner.start_job(job_id=job_id, skip_estimation=True)

            # self.mongo_util.update_job_status(job_id=job_id, status=Status.running.value)
            # job.running = datetime.datetime.utcnow()
            # job.save()

            # test finish job without error
            job_output = dict()
            job_output["version"] = "1"
            job_output["id"] = "5d54bdcb9b402d15271b3208"  # A valid objectid
            job_output["result"] = {"output": "output"}
            logging.info("Case2 : Finish a running job")

            print(f"About to finish job {job_id}. The job status is currently")
            print(runner.get_job_status(job_id))
            runner.finish_job(job_id, job_output=job_output)
            print("Job is now finished, status is")
            print(runner.get_job_status(job_id))

            job = self.mongo_util.get_job(job_id=job_id)
            self.assertEqual(job.status, "finished")
            self.assertFalse(job.errormsg)
            self.assertTrue(job.finished)
            # if job_output not a dict#
            # job_output2 = job.job_output.to_mongo().to_dict()
            job_output2 = job.job_output
            self.assertEqual(job_output2["version"], "1")
            self.assertEqual(str(job_output2["id"]), job_output["id"])

            # update finished status to running
            with self.assertRaises(InvalidStatusTransitionException):
                self.mongo_util.update_job_status(
                    job_id=job_id, status=Status.running.value
                )

    def test_finish_job_with_error_message(self):
        with self.mongo_util.mongo_engine_connection():
            ori_job_count = Job.objects.count()
            job_id = self.create_job_rec()
            job = self.mongo_util.get_job(job_id=job_id)
            self.assertEqual(ori_job_count, Job.objects.count() - 1)

        runner = self.getRunner()
        runner._send_exec_stats_to_catalog = MagicMock(return_value=True)
        runner.catalog.log_exec_stats = MagicMock(return_value=True)
        runner._test_job_permissions = MagicMock(return_value=True)

        with self.assertRaises(InvalidStatusTransitionException):
            runner.finish_job(job_id, error_message="error message")

        runner.start_job(job_id=job_id, skip_estimation=True)
        runner.finish_job(job_id, error_message="error message")

        job = self.mongo_util.get_job(job_id=job_id)

        self.assertEqual(job.status, "error")
        self.assertEqual(job.errormsg, "error message")
        self.assertEqual(job.error_code, 1)
        self.assertIsNone(job.error)
        self.assertTrue(job.finished)

        with self.mongo_util.mongo_engine_connection():
            job_id = runner.update_job_status(job_id, "running")  # put job back to running status

        error = {"message": "error message",
                 "code'": -32000,
                 "name": "Server error",
                 "error": """Traceback (most recent call last):\n  File "/kb/module/bin/../lib/simpleapp/simpleappServer.py"""}

        runner.finish_job(job_id, error_message="error message", error=error, error_code=0)

        job = self.mongo_util.get_job(job_id=job_id)

        self.assertEqual(job.status, "error")
        self.assertEqual(job.errormsg, "error message")
        self.assertEqual(job.error_code, 0)
        self.assertCountEqual(job.error, error)

        self.mongo_util.get_job(job_id=job_id).delete()
        self.assertEqual(ori_job_count, Job.objects.count())

    def test_start_job(self):

        with self.mongo_util.mongo_engine_connection():
            ori_job_count = Job.objects.count()
            job_id = self.create_job_rec()
            self.assertEqual(ori_job_count, Job.objects.count() - 1)

            job = self.mongo_util.get_job(job_id=job_id)
            self.assertEqual(job.status, "created")
            self.assertFalse(job.finished)
            self.assertFalse(job.running)
            self.assertFalse(job.estimating)

            runner = self.getRunner()
            runner._test_job_permissions = MagicMock(return_value=True)

            # test missing job_id input
            with self.assertRaises(ValueError) as context:
                runner.start_job(None)
                self.assertEqual("Please provide valid job_id", str(context.exception))

            # start a created job, set job to estimation status
            runner.start_job(job_id, skip_estimation=False)

            job = self.mongo_util.get_job(job_id=job_id)
            self.assertEqual(job.status, "estimating")
            self.assertFalse(job.running)
            self.assertTrue(job.estimating)

            # start a estimating job, set job to running status
            runner.start_job(job_id)

            job = self.mongo_util.get_job(job_id=job_id)
            self.assertEqual(job.status, "running")
            self.assertTrue(job.running)
            self.assertTrue(job.estimating)

            # test start a job with invalid status
            with self.assertRaises(ValueError) as context:
                runner.start_job(job_id)
            self.assertIn("Unexpected job status", str(context.exception))

            self.mongo_util.get_job(job_id=job_id).delete()
            self.assertEqual(ori_job_count, Job.objects.count())

    @requests_mock.Mocker()
    def test_check_job_global_perm(self, rq_mock):
        rq_mock.add_matcher(_run_job_adapter(
            ws_perms_info={"user_id": self.user_id, "ws_perms": {self.ws_id: "n"}},
            ws_perms_global=[self.ws_id],
            user_roles=[]
        ))
        with self.mongo_util.mongo_engine_connection():
            ori_job_count = Job.objects.count()
            job_id = self.create_job_rec()
            self.assertEqual(ori_job_count, Job.objects.count() - 1)

            job = self.mongo_util.get_job(job_id=job_id)
            self.assertEqual(job.status, "created")
            self.assertFalse(job.finished)
            self.assertFalse(job.running)
            self.assertFalse(job.estimating)

            # test check_job
            runner = self.getRunner()
            job_state = runner.check_job(job_id)
            json.dumps(job_state)  # make sure it's JSON serializable
            self.assertTrue(validate_job_state(job_state))
            self.assertEqual(job_state["status"], "created")
            self.assertEqual(job_state["wsid"], self.ws_id)

            # test globally
            job_states = runner.check_workspace_jobs(self.ws_id)
            self.assertTrue(job_id in job_states)
            self.assertEqual(job_states[job_id]["status"], "created")

            # now test with a different user
            other_method_runner = SDKMethodRunner(self.cfg, user_id="some_other_user", token="other_token")
            job_states = other_method_runner.check_workspace_jobs(self.ws_id)
            self.assertTrue(job_id in job_states)
            self.assertEqual(job_states[job_id]["status"], "created")

    @requests_mock.Mocker()
    def test_check_job_ok(self, rq_mock):
        rq_mock.add_matcher(_run_job_adapter(
            ws_perms_info={"user_id": self.user_id, "ws_perms": {self.ws_id: "a"}},
            user_roles=[]
        ))
        with self.mongo_util.mongo_engine_connection():
            ori_job_count = Job.objects.count()
            job_id = self.create_job_rec()
            self.assertEqual(ori_job_count, Job.objects.count() - 1)

            job = self.mongo_util.get_job(job_id=job_id)
            self.assertEqual(job.status, "created")
            self.assertFalse(job.finished)
            self.assertFalse(job.running)
            self.assertFalse(job.estimating)

            runner = self.getRunner()
            runner._test_job_permissions = MagicMock(return_value=True)

            # test missing job_id input
            with self.assertRaises(ValueError) as context:
                runner.check_job(None)
                self.assertEqual("Please provide valid job_id", str(context.exception))

            # test check_job
            job_state = runner.check_job(job_id)
            json.dumps(job_state)  # make sure it's JSON serializable
            self.assertTrue(validate_job_state(job_state))
            self.assertEqual(job_state["status"], "created")
            self.assertEqual(job_state["wsid"], self.ws_id)

            # test check_job with projection
            job_state = runner.check_job(job_id, projection=["status"])
            self.assertFalse("status" in job_state.keys())
            self.assertEqual(job_state["wsid"], self.ws_id)

            # test check_jobs
            job_states = runner.check_jobs([job_id])
            json.dumps(job_states)  # make sure it's JSON serializable
            self.assertTrue(validate_job_state(job_states[job_id]))
            self.assertTrue(job_id in job_states)
            self.assertEqual(job_states[job_id]["status"], "created")
            self.assertEqual(job_states[job_id]["wsid"], self.ws_id)

            # test check_jobs with projection
            job_states = runner.check_jobs([job_id], projection=["wsid"])
            self.assertTrue(job_id in job_states)
            self.assertFalse("wsid" in job_states[job_id].keys())
            self.assertEqual(job_states[job_id]["status"], "created")

            # test check_workspace_jobs
            job_states = runner.check_workspace_jobs(self.ws_id)
            for job_id in job_states:
                self.assertTrue(job_states[job_id])
            json.dumps(job_states)  # make sure it's JSON serializable
            self.assertTrue(job_id in job_states)
            self.assertEqual(job_states[job_id]["status"], "created")
            self.assertEqual(job_states[job_id]["wsid"], self.ws_id)

            # test check_workspace_jobs with projection
            job_states = runner.check_workspace_jobs(self.ws_id, projection=["wsid"])
            self.assertTrue(job_id in job_states)
            self.assertFalse("wsid" in job_states[job_id].keys())
            self.assertEqual(job_states[job_id]["status"], "created")

            with self.assertRaises(PermissionError) as e:
                job_states = runner.check_workspace_jobs(1234)
            self.assertIn(f"User {self.user_id} does not have permission to read jobs in workspace {1234}",
                          str(e.exception))

            self.mongo_util.get_job(job_id=job_id).delete()
            self.assertEqual(ori_job_count, Job.objects.count())
