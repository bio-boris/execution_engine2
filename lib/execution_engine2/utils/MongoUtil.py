import logging
import subprocess
import traceback
from contextlib import contextmanager

from bson.objectid import ObjectId
from mongoengine import connect, connection
from pymongo import MongoClient
from pymongo.errors import ServerSelectionTimeoutError

from execution_engine2.exceptions import (
    RecordNotFoundException,
    InvalidStatusTransitionException,
)
from lib.execution_engine2.models.models import JobLog, Job, Status


class MongoUtil:
    def _start_local_service(self):

        try:
            start_local = int(self.config.get("start-local-mongo", 0))
        except Exception:
            raise ValueError(
                "unexpected start-local-mongo: {}".format(
                    self.config.get("start-local-mongo")
                )
            )
        if start_local:
            print("Start local is")
            print(start_local)
            logging.info("starting local mongod service")

            logging.info("running sudo service mongodb start")
            pipe = subprocess.Popen(
                "sudo service mongodb start",
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            stdout = pipe.communicate()
            logging.info(stdout)

            logging.info("running mongod --version")
            pipe = subprocess.Popen(
                "mongod --version",
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            stdout = pipe.communicate()
            logging.info(stdout)

    @classmethod
    def _get_collection(
        self,
        mongo_host,
        mongo_port,
        mongo_database,
        mongo_user=None,
        mongo_password=None,
        mongo_authmechanism="DEFAULT",
    ):
        """
        connect Mongo server and return a collection
        """

        if mongo_user:
            logging.info(
                "mongo-user found in config file, configuring client for authentication using mech "
                + str(mongo_authmechanism)
            )
            pymongo_client = MongoClient(
                mongo_host,
                mongo_port,
                username=mongo_user,
                password=mongo_password,
                authSource=mongo_database,
                authMechanism=mongo_authmechanism,
            )

            mongoengine_client = connect(
                db=mongo_database,
                host=mongo_host,
                port=mongo_port,
                username=mongo_user,
                password=mongo_password,
                authentication_source=mongo_database,
                authentication_mechanism=mongo_authmechanism,
            )
        else:
            logging.info("no mongo-user found in config file, connecting without auth")
            pymongo_client = MongoClient(mongo_host, mongo_port)

            mongoengine_client = connect(
                mongo_database, host=mongo_host, port=mongo_port
            )
        try:
            pymongo_client.server_info()  # force a call to server
        except ServerSelectionTimeoutError as e:
            error_msg = "Connot connect to Mongo server\n"
            error_msg += "ERROR -- {}:\n{}".format(
                e, "".join(traceback.format_exception(None, e, e.__traceback__))
            )
            raise ValueError(error_msg)

        return pymongo_client, mongoengine_client

    def __init__(self, config):
        self.config = config
        self.mongo_host = config["mongo-host"]
        self.mongo_port = int(config["mongo-port"])
        self.mongo_database = config["mongo-database"]
        self.mongo_user = config["mongo-user"]
        self.mongo_pass = config["mongo-password"]
        self.mongo_authmechanism = config["mongo-authmechanism"]

        self.mongo_collection = None

        self._start_local_service()
        logging.basicConfig(
            format="%(created)s %(levelname)s: %(message)s", level=logging.INFO
        )

    @contextmanager
    def pymongo_client(self, mongo_collection):
        """
        Instantiates a mongo client to be used as a context manager
        Closes the connection at the end
        :return:
        """
        self.mongo_collection = mongo_collection

        mc = MongoClient(
            self.mongo_host,
            self.mongo_port,
            username=self.mongo_user,
            password=self.mongo_pass,
            authSource=self.mongo_database,
            authMechanism=self.mongo_authmechanism,
        )

        try:
            yield mc
        finally:
            mc.close()

    def get_job_log(self, job_id=None) -> JobLog:
        if job_id is None:
            raise ValueError("Please provide a job id")
        with self.mongo_engine_connection():
            try:
                job_log = JobLog.objects.with_id(job_id)
            except Exception:
                raise ValueError(
                    "Unable to find job:\nError:\n{}".format(traceback.format_exc())
                )

            if not job_log:
                raise RecordNotFoundException(
                    "Cannot find job log with id: {}".format(job_id)
                )

        return job_log

    def get_job(self, job_id=None, projection=None) -> Job:
        if job_id is None:
            raise ValueError("Please provide a job id")

        job = self.get_jobs(job_ids=[job_id], projection=projection)[0]

        return job

    def get_jobs(self, job_ids=None, projection=None):
        if not (job_ids and isinstance(job_ids, list)):
            raise ValueError("Please provide a non empty list of job ids")
        with self.mongo_engine_connection():
            try:
                if projection:
                    if not isinstance(projection, list):
                        raise ValueError("Please input a list type projection")
                    jobs = Job.objects(id__in=job_ids).exclude(*projection)
                else:
                    jobs = Job.objects(id__in=job_ids)
            except Exception:
                raise ValueError(
                    "Unable to find job:\nError:\n{}".format(traceback.format_exc())
                )

            if not jobs:
                raise RecordNotFoundException(
                    "Cannot find job with ids: {}".format(job_ids)
                )

        return jobs

    def update_job_status(self, job_id, status):
        """
        A job in status created can be estimating/running/error/terminated
        A job in status created cannot be created

        A job in status estimating can be running/finished/error/terminated
        A job in status estimating cannot be created or estimating

        A job in status running can be terminated/error/finished
        A job in status running cannot be created/estimating

        A job in status finished/terminated/error cannot be changed


        Update one job record with an approriate Status
        :param job_id: The job id to update
        :param status: The status to update with
        :return: Void
        """
        with self.mongo_engine_connection():
            j = Job.objects.with_id(job_id)  # type: Job
            #  A job in status finished/terminated/error cannot be changed
            logging.info("job status is {j.status}")

            if j.status in [
                Status.error.value,
                Status.finished.value,
                Status.terminated.value,
            ]:
                raise InvalidStatusTransitionException(
                    f"A job with status {j.status} cannot be changed"
                )

            #  A job in status running can only be terminated/error/finished
            if j.status == Status.running.value:
                if status not in [
                    Status.finished.value,
                    Status.terminated.value,
                    Status.error.value,
                ]:
                    raise InvalidStatusTransitionException(
                        f"Cannot change from {j.status} to {status}"
                    )

            # A job in status estimating cannot be created
            if j.status == Status.estimating.value:
                if status == Status.created.value:
                    raise InvalidStatusTransitionException(
                        f"Cannot change from {j.status} to {status}"
                    )

            # A job in status X cannot become status X
            if j.status == status:
                raise InvalidStatusTransitionException(
                    f"Cannot change from {j.status} to itself {status}"
                )

            j.status = status
            j.save()

    def get_empty_job_log(self):
        jl = JobLog()
        jl.stored_line_count = 0
        jl.original_line_count = 0
        return jl

    @contextmanager
    def mongo_engine_connection(self):
        mongoengine_client = connect(
            db=self.mongo_database,
            host=self.mongo_host,
            port=self.mongo_port,
            username=self.mongo_user,
            password=self.mongo_pass,
            authentication_source=self.mongo_database,
            authentication_mechanism=self.mongo_authmechanism,
        )  # type: connection
        try:
            yield mongoengine_client
        finally:
            mongoengine_client.close()

    @contextmanager
    def me_collection(self, mongo_collection):
        self.mongo_collection = mongo_collection
        try:
            pymongo_client, mongoengine_client = self._get_collection(
                self.mongo_host,
                self.mongo_port,
                self.mongo_database,
                self.mongo_user,
                self.mongo_pass,
                self.mongo_authmechanism,
            )
            yield pymongo_client, mongoengine_client
        finally:
            pymongo_client.close()
            mongoengine_client.close()

    def insert_one(self, doc):
        """
        insert a doc into collection
        """
        logging.info("start inserting document")

        with self.me_collection(self.mongo_collection) as (
            pymongo_client,
            mongoengine_client,
        ):
            try:
                rec = pymongo_client[self.mongo_database][
                    self.mongo_collection
                ].insert_one(doc)
            except Exception as e:
                error_msg = "Connot insert doc\n"
                error_msg += "ERROR -- {}:\n{}".format(
                    e, "".join(traceback.format_exception(None, e, e.__traceback__))
                )
                raise ValueError(error_msg)

        return rec.inserted_id

    def update_one(self, doc, job_id):
        """
        update existing records
        https://docs.mongodb.com/manual/reference/operator/update/set/
        """
        logging.info("start updating document")

        with self.me_collection(self.mongo_collection) as (
            pymongo_client,
            mongoengine_client,
        ):
            job_col = pymongo_client[self.mongo_database][self.mongo_collection]
            try:
                update_filter = {"_id": ObjectId(job_id)}
                update = {"$set": doc}
                job_col.update_one(update_filter, update)
            except Exception as e:
                error_msg = "Connot update doc\n"
                error_msg += "ERROR -- {}:\n{}".format(
                    e, "".join(traceback.format_exception(None, e, e.__traceback__))
                )
                raise ValueError(error_msg)

        return True

    def delete_one(self, job_id):
        """
        delete a doc by _id
        """
        logging.info("start deleting document")
        with self.me_collection(self.mongo_collection) as (
            pymongo_client,
            mongoengine_client,
        ):
            job_col = pymongo_client[self.mongo_database][self.mongo_collection]
            try:
                delete_filter = {"_id": ObjectId(job_id)}
                job_col.delete_one(delete_filter)
            except Exception as e:
                error_msg = "Connot delete doc\n"
                error_msg += "ERROR -- {}:\n{}".format(
                    e, "".join(traceback.format_exception(None, e, e.__traceback__))
                )
                raise ValueError(error_msg)

        return True

    def find_in(self, elements, field_name, projection=None, batch_size=1000):
        """
        return cursor that contains docs which field column is in elements
        """
        logging.info("start querying MongoDB")

        with self.me_collection(self.mongo_collection) as (
            pymongo_client,
            mongoengine_client,
        ):
            job_col = pymongo_client[self.mongo_database][self.mongo_collection]
            try:
                result = job_col.find(
                    {field_name: {"$in": elements}},
                    projection=projection,
                    batch_size=batch_size,
                )
            except Exception as e:
                error_msg = "Connot query doc\n"
                error_msg += "ERROR -- {}:\n{}".format(
                    e, "".join(traceback.format_exception(None, e, e.__traceback__))
                )
                raise ValueError(error_msg)

            logging.info("returned {} results".format(result.count()))

        return result
