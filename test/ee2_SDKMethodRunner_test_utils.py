# -*- coding: utf-8 -*-
import copy
import json
import logging
import os
import time
import unittest
import bson

import dateutil
import requests
import requests_mock
from bson import ObjectId
from configparser import ConfigParser
from datetime import datetime, timedelta
from mock import MagicMock
from mongoengine import ValidationError
from typing import Dict, List
from unittest.mock import patch
from execution_engine2.utils.Condor import condor_resources
from execution_engine2.SDKMethodRunner import SDKMethodRunner
from execution_engine2.db.MongoUtil import MongoUtil
from execution_engine2.db.models.models import (
    Job,
    JobInput,
    Meta,
    Status,
    JobLog,
    TerminatedCode,
)
from execution_engine2.exceptions import AuthError
from execution_engine2.exceptions import InvalidStatusTransitionException
from execution_engine2.utils.Condor import submission_info
from test.mongo_test_helper import MongoTestHelper
from test.utils.test_utils import bootstrap, get_example_job, validate_job_state



class ee2_sdkmr_test_helper:
    def __init__(self, cfg):
        self.user_id = "wsadmin"
        self.ws_id = 9999
        self.token = "token"
        self.cfg = cfg
        self.method_runner = SDKMethodRunner(
            self.cfg, user_id=self.user_id, token=self.token
        )

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
        job.scheduler_id = "123"

        with self.method_runner.get_mongo_util().mongo_engine_connection():
            job.save()

        return str(job.id)