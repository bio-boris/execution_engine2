# -*- coding: utf-8 -*-
import logging
import unittest

logging.basicConfig(level=logging.INFO)

from pymongo import MongoClient
from test.test_utils import read_config_into_dict


class ExecutionEngine2SchedulerTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):

        config = read_config_into_dict('deploy.cfg','execution_engine2')

        #For running python interpreter in a docker container
        mongo_in_docker = config.get('mongo-in-docker-compose', None)
        if mongo_in_docker is not None:
            config['mongo-host'] = config['mongo-in-docker-compose']

        #For using mongo running in docker
        config['start-local-mongo'] = 0



        cls.config = config








        cls.ctx = {"job_id": "test", "user_id": "test", "token": "test"}

        cls.mongo_client = MongoClient(
            host=cls.config["mongo-host"],
            port=int(cls.config["mongo-port"]),
            username=cls.config["mongo-user"],
            password=cls.config["mongo-password"],
            authSource="admin",
            authMechanism=cls.config["mongo-authmechanism"],
        )

        logging.info("Creating privileged user")
        cls.db = cls.mongo_client.get_database(cls.config["mongo-database"])
        cls.db.command("dropUser", cls.config["mongo-user"])
        cls.db.command(
            "createUser",
            cls.config["mongo-user"],
            pwd=cls.config["mongo-password"],
            roles=["dbOwner"],
        )

    def test_database_configured(self):
        logging.info("Checking privileged user")
        users_info = self.db.command("usersInfo")
        success = 0
        for user in users_info["users"]:
            if user["user"] == "travis":
                self.assertEqual(
                    [{"role": "dbOwner", "db": self.config["mongo-database"]}],
                    user["roles"],
                )
                success = 1
        assert success