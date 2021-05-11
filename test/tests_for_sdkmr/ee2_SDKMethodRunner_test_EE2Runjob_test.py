# -*- coding: utf-8 -*-
import copy
import logging
import os
import unittest
from configparser import ConfigParser
from unittest.mock import patch

import requests_mock
from mock import MagicMock

from lib.execution_engine2.db.MongoUtil import MongoUtil
from lib.execution_engine2.db.models.models import Job
from lib.execution_engine2.sdk.SDKMethodRunner import SDKMethodRunner
from lib.execution_engine2.utils.CondorTuples import SubmissionInfo
from execution_engine2.utils.clients import (
    get_client_set,
    get_user_client_set,
)
from execution_engine2.sdk.job_submission_parameters import JobRequirements
from installed_clients.CatalogClient import Catalog

from test.utils_shared.test_utils import (
    bootstrap,
    get_example_job,
    run_job_adapter,
    get_example_job_as_dict,
)
from tests_for_db.mongo_test_helper import MongoTestHelper

logging.basicConfig(level=logging.INFO)
bootstrap()

from test.tests_for_sdkmr.ee2_SDKMethodRunner_test_utils import ee2_sdkmr_test_helper
from pytest import raises


class ee2_SDKMethodRunner_test(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        config_file = os.environ.get("KB_DEPLOYMENT_CONFIG", "test/deploy.cfg")
        logging.info(f"Loading config from {config_file}")

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

        with open(config_file) as cf:
            cls.method_runner = SDKMethodRunner(
                get_user_client_set(cls.cfg, cls.user_id, cls.token),
                get_client_set(cls.cfg, cf),
            )

        cls.mongo_util = MongoUtil(cls.cfg)
        cls.mongo_helper = MongoTestHelper(cls.cfg)

        cls.test_collection = cls.mongo_helper.create_test_db(
            db=cls.cfg["mongo-database"], col=cls.cfg["mongo-jobs-collection"]
        )

        cls.sdkmr_test_helper = ee2_sdkmr_test_helper(cls.user_id)

    def getRunner(self) -> SDKMethodRunner:
        # Initialize these clients from None
        runner = copy.copy(self.__class__.method_runner)  # type : SDKMethodRunner
        runner.get_jobs_status()
        runner.get_runjob()
        runner.get_job_logs()
        return runner

    def create_job_rec(self):
        return self.sdkmr_test_helper.create_job_rec()

    def test_init_ok(self):
        class_attri = ["workspace", "mongo_util", "condor"]
        runner = self.getRunner()
        self.assertTrue(set(class_attri) <= set(runner.__dict__.keys()))

    @patch.object(Catalog, "get_module_version")
    def test_init_job_rec(self, get_mod_ver):
        ori_job_count = Job.objects.count()
        runner = self.getRunner()

        job_params = {
            "wsid": self.ws_id,
            "method": "MEGAHIT.run_megahit",
            "app_id": "MEGAHIT/run_megahit",
            "service_ver": "2.2.1",
            "params": [
                {
                    "workspace_name": "wjriehl:1475006266615",
                    "read_library_refs": ["18836/5/1"],
                    "output_contigset_name": "rhodo_contigs",
                    "recipe": "auto",
                    "assembler": None,
                    "pipeline": None,
                    "min_contig_len": None,
                }
            ],
            "job_reqs": JobRequirements(1, 1, 1, "njs"),
            "source_ws_objects": ["a/b/c", "e/d"],
            "parent_job_id": "9998",
            "meta": {"tag": "dev", "token_id": "12345"},
        }

        get_mod_ver.return_value = {
            "git_commit_hash": "048baf3c2b76cb923b3b4c52008ed77dbe20292d"
        }

        job_id = runner.get_runjob()._init_job_rec(self.user_id, job_params)

        get_mod_ver.assert_called_once_with(
            {"module_name": "MEGAHIT", "version": "2.2.1"}
        )

        self.assertEqual(ori_job_count, Job.objects.count() - 1)

        job = Job.objects.get(id=job_id)

        self.assertEqual(job.user, self.user_id)
        self.assertEqual(job.authstrat, "kbaseworkspace")
        self.assertEqual(job.wsid, self.ws_id)

        job_input = job.job_input

        self.assertEqual(job_input.wsid, self.ws_id)
        self.assertEqual(job_input.method, "MEGAHIT.run_megahit")
        self.assertEqual(job_input.app_id, "MEGAHIT/run_megahit")
        # TODO this is an integration test
        # self.assertEqual(job_input.service_ver, "2.2.1")
        self.assertEqual(
            job_input.service_ver, "048baf3c2b76cb923b3b4c52008ed77dbe20292d"
        )

        self.assertCountEqual(job_input.source_ws_objects, ["a/b/c", "e/d"])
        self.assertEqual(job_input.parent_job_id, "9998")

        narrative_cell_info = job_input.narrative_cell_info
        self.assertEqual(narrative_cell_info.tag, "dev")
        self.assertEqual(narrative_cell_info.token_id, "12345")

        self.assertFalse(job.job_output)

        self.mongo_util.get_job(job_id=job_id).delete()
        self.assertEqual(ori_job_count, Job.objects.count())

    def test_get_job_params(self):

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

    def test_start_job(self):

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
        runner.start_job(job_id, skip_estimation=False)

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
    @patch("lib.execution_engine2.utils.Condor.Condor", autospec=True)
    def test_run_job(self, rq_mock, condor_mock):
        rq_mock.add_matcher(
            run_job_adapter(
                ws_perms_info={"user_id": self.user_id, "ws_perms": {self.ws_id: "a"}}
            )
        )
        runner = self.getRunner()
        runner.get_condor = MagicMock(return_value=condor_mock)
        job = get_example_job_as_dict(user=self.user_id, wsid=self.ws_id)

        si = SubmissionInfo(clusterid="test", submit=job, error=None)
        condor_mock.run_job = MagicMock(return_value=si)

        job_id = runner.run_job(params=job)
        print(f"Job id is {job_id} ")

    @requests_mock.Mocker()
    @patch("lib.execution_engine2.utils.Condor.Condor", autospec=True)
    def test_retry_job(self, rq_mock, condor_mock):
        # 1. Run the job
        rq_mock.add_matcher(
            run_job_adapter(
                ws_perms_info={"user_id": self.user_id, "ws_perms": {self.ws_id: "a"}}
            )
        )
        runner = self.getRunner()
        runner.get_condor = MagicMock(return_value=condor_mock)
        runner.workspace.get_object_info3 = MagicMock(return_value={"paths": []})

        job = get_example_job_as_dict(
            user=self.user_id, wsid=self.ws_id, source_ws_objects=[]
        )
        si = SubmissionInfo(clusterid="test", submit=job, error=None)
        condor_mock.run_job = MagicMock(return_value=si)

        parent_job_id = runner.run_job(params=job)

        # 2. Retry the job
        retry_job_id = runner.retry(job_id=parent_job_id)

        # 3. Attempt to retry a retry
        # TODO: Why cant I use a CannotRetryARetryException here?
        with self.assertRaises(Exception):
            runner.retry(job_id=retry_job_id)

        # 4. Attempt a fresh retry

        runner.retry(job_id=parent_job_id)

        # 5. Get both jobs and compare them!
        original_job, retried_job = runner.check_jobs(
            job_ids=[parent_job_id, retry_job_id]
        )["job_states"]

        same_keys = ["user", "authstrat", "wsid", "scheduler_type", "job_input"]
        different_keys = ["updated", "queued", "finished"]
        assert "retry_parent" not in original_job
        assert original_job["retried"]
        assert original_job["retry_count"] == 2
        assert retried_job["retry_parent"] == parent_job_id

        for key in same_keys:
            assert original_job[key] == retried_job[key]

        for key in different_keys:
            assert original_job[key] != retried_job[key]

        assert original_job["job_input"]["params"] == retried_job["job_input"]["params"]

        # TODO Retry a job that uses run_job_batch or kbparallels (Like metabat)
        # TODO Retry a job without an app_id

    @requests_mock.Mocker()
    @patch("lib.execution_engine2.utils.Condor.Condor", autospec=True)
    def test_retry_job_with_params_and_nci_and_src_ws_objs(self, rq_mock, condor_mock):
        # 1. Run the job
        rq_mock.add_matcher(
            run_job_adapter(
                ws_perms_info={"user_id": self.user_id, "ws_perms": {self.ws_id: "a"}}
            )
        )
        runner = self.getRunner()
        runner.workspace.get_object_info3 = MagicMock(return_value={"paths": []})
        runner.workspace_auth.can_write = MagicMock(return_value=True)
        runner.get_condor = MagicMock(return_value=condor_mock)

        quast_params = {
            "workspace_name": "XX:narrative_1620418248793",
            "assemblies": ["62160/9/18"],
            "force_glimmer": 0,
        }
        source_ws_objects = quast_params["assemblies"]
        nci = {
            "run_id": "3a211c4e-5ba8-4b94-aeae-378079ccc63d",
            "token_id": "f38f09f7-5ab1-4bfc-9f3f-2b82c7a8dbdc",
            "tag": "release",
            "cell_id": "3ee13d64-623b-407f-98a1-72e577662132",
        }

        job = get_example_job_as_dict(
            user=self.user_id,
            wsid=self.ws_id,
            narrative_cell_info=nci,
            params=quast_params,
            source_ws_objects=source_ws_objects,
            method_name="kb_quast.run_QUAST_app",
            app_id="kb_quast/run_QUAST_app",
        )
        si = SubmissionInfo(clusterid="test", submit=job, error=None)
        condor_mock.run_job = MagicMock(return_value=si)
        parent_job_id = runner.run_job(params=job)

        # 2. Retry the job
        retry_job_id = runner.retry(job_id=parent_job_id)

        # 3. Attempt to retry a retry
        # TODO: Why cant I use a CannotRetryARetryException here?
        with self.assertRaises(Exception):
            runner.retry(job_id=retry_job_id)

        # 4. Attempt a fresh retry

        runner.retry(job_id=parent_job_id)

        # 5. Get both jobs and compare them!
        original_job, retried_job = runner.check_jobs(
            job_ids=[parent_job_id, retry_job_id]
        )["job_states"]

        # TODO Check narrative_cell_info
        same_keys = ["user", "authstrat", "wsid", "scheduler_type", "job_input"]
        different_keys = [
            "updated",
            "queued",
            "finished",
        ]
        assert "retry_parent" not in original_job
        assert original_job["retried"]
        assert original_job["retry_count"] == 2
        assert retried_job["retry_parent"] == parent_job_id

        for key in same_keys:
            assert original_job[key] == retried_job[key]

        for key in different_keys:
            assert original_job[key] != retried_job[key]

        # TODO Retry a job that uses run_job_batch or kbparallels (Like metabat)
        # TODO Retry a job without an app_id

    @requests_mock.Mocker()
    @patch("lib.execution_engine2.utils.Condor.Condor", autospec=True)
    def test_run_job_batch(self, rq_mock, condor_mock):
        """
        Test running batch jobs
        """
        rq_mock.add_matcher(
            run_job_adapter(
                ws_perms_info={"user_id": self.user_id, "ws_perms": {self.ws_id: "a"}}
            )
        )
        runner = self.getRunner()
        runner.get_condor = MagicMock(return_value=condor_mock)
        job = get_example_job_as_dict(user=self.user_id, wsid=self.ws_id)

        si = SubmissionInfo(clusterid="test", submit=job, error=None)
        condor_mock.run_job = MagicMock(return_value=si)

        jobs = [job, job, job]
        job_ids = runner.run_job_batch(params=jobs, batch_params={"wsid": self.ws_id})

        assert "parent_job_id" in job_ids and isinstance(job_ids["parent_job_id"], str)
        assert "child_job_ids" in job_ids and isinstance(job_ids["child_job_ids"], list)
        assert len(job_ids["child_job_ids"]) == len(jobs)

        # Test that you can't run a job in someone elses workspace
        with self.assertRaises(PermissionError):
            job_bad = get_example_job(user=self.user_id, wsid=1234).to_mongo().to_dict()
            job_bad["method"] = job["job_input"]["app_id"]
            job_bad["app_id"] = job["job_input"]["app_id"]
            job_bad["service_ver"] = job["job_input"]["service_ver"]
            jobs = [job, job_bad]
            runner.run_job_batch(params=jobs, batch_params={"wsid": self.ws_id})

    @requests_mock.Mocker()
    @patch("lib.execution_engine2.utils.Condor.Condor", autospec=True)
    def test_run_job_fail(self, rq_mock, condor_mock):
        rq_mock.add_matcher(
            run_job_adapter(
                ws_perms_info={"user_id": self.user_id, "ws_perms": {self.ws_id: "a"}}
            )
        )
        runner = self.getRunner()

        job = get_example_job_as_dict(user=self.user_id, wsid=self.ws_id)

        si = SubmissionInfo(clusterid="test", submit=job, error=None)
        condor_mock.run_job = MagicMock(return_value=si)

        with self.assertRaises(expected_exception=RuntimeError):
            runner.run_job(params=job)
