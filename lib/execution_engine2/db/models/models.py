import time
from datetime import datetime
from enum import Enum

from mongoengine import (
    StringField,
    IntField,
    FloatField,
    EmbeddedDocument,
    Document,
    BooleanField,
    ListField,
    EmbeddedDocumentField,
    EmbeddedDocumentListField,
    DynamicField,
    ObjectIdField,
    ReferenceField,
)
from mongoengine import ValidationError


def valid_status(status):
    try:
        Status(status)
    except Exception:
        raise ValidationError(
            f"{status} is not a valid status {vars(Status)['_member_names_']}"
        )


def valid_authstrat(strat):
    if strat is None:
        pass
    try:
        AuthStrat(strat)
    except Exception:
        raise ValidationError(
            f"{strat} is not a valid Authentication strategy {vars(AuthStrat)['_member_names_']}"
        )


def valid_termination_code(code):
    if code is None:
        pass
    try:
        TerminatedCode(code)
    except Exception:
        raise ValidationError(
            f"{code} is not a valid TerminatedCode strategy {vars(TerminatedCode)['_member_names_']}"
        )


def valid_errorcode(code):
    if code is None:
        pass
    try:
        ErrorCode(code)
    except Exception:
        raise ValidationError(
            f"{code} is not a valid ErrorCode strategy {vars(ErrorCode)['_member_names_']}"
        )


# TODO Make sure Datetime is correct format
# TODO Use ReferenceField to create a mapping between WSID and JOB IDS?
"""
"created": ISODate("2019-02-19T23:00:57.119Z"),
"updated": ISODate("2019-02-19T23:01:32.132Z"),
"""


class LogLines(EmbeddedDocument):
    """
    Log lines contain the content, whether or not to display the content as an error message,
    the position of the line, and the timestamp
    """

    line = StringField(required=True)
    linepos = IntField(required=True)
    error = BooleanField(default=False)
    ts = FloatField(default=time.time)


class JobLog(Document):
    """
    As a job runs, it saves its STDOUT and STDERR here

    """

    primary_key = ObjectIdField(primary_key=True, required=True)
    updated = FloatField(default=time.time)
    original_line_count = IntField()
    stored_line_count = IntField()
    lines = ListField()

    meta = {"collection": "ee2_logs"}

    def save(self, *args, **kwargs):
        self.updated = time.time()
        return super(JobLog, self).save(*args, **kwargs)


class Meta(EmbeddedDocument):
    """
    Information about from the cell where this job was run
    """

    run_id = StringField()
    token_id = StringField()
    tag = StringField()
    cell_id = StringField()

    def __repr__(self):
        return self.to_json()


class CondorResourceUsage(EmbeddedDocument):
    """
    Storing stats about a job's usage
    """

    cpu = ListField()
    memory = ListField()
    timestamp = ListField()

    # Maybe remove this if we always want to make timestamp required
    def save(self, *args, **kwargs):
        self.timestamp.append(datetime.utcnow().timestamp())
        return super(CondorResourceUsage, self).save(*args, **kwargs)


class Estimate(EmbeddedDocument):
    """
    Estimator function output goes here
    """

    cpu = IntField()
    memory = StringField()


class JobRequirements(EmbeddedDocument):
    """
    To be populated by ee2 during init of a job record
    """

    clientgroup = StringField()
    cpu = IntField()
    memory = IntField()
    disk = IntField()
    estimate = EmbeddedDocumentField(Estimate)

    def __repr__(self):
        return self.to_json()


class JobInput(EmbeddedDocument):
    """
    To be created from the Narrative
    """

    wsid = IntField(required=False, default=None)
    method = StringField(required=True)
    requested_release = StringField()
    params = DynamicField()
    service_ver = StringField(required=True)
    app_id = StringField()
    source_ws_objects = ListField()
    # this ID is for jobs submitted via run_job with a parent_job_id field included by the
    # client. For this case, the parent job is not updated at all.
    parent_job_id = StringField()
    requirements = EmbeddedDocumentField(JobRequirements)
    narrative_cell_info = EmbeddedDocumentField(Meta, required=True)

    def __repr__(self):
        return self.to_json()


class JobOutput(EmbeddedDocument):
    """
    To be created from the successful and possibly also failure run of a job
    """

    version = StringField(required=True)
    id = ObjectIdField(required=True)
    result = DynamicField(required=True)


class ErrorCode(Enum):
    """
    Reasons why the job was marked as error
    """

    unknown_error = 0
    job_crashed = 1
    job_terminated_by_automation = 2
    job_over_timelimit = 3
    job_missing_output = 4
    token_expired = 5
    condor_submit_issue = 6


class Error(EmbeddedDocument):
    """
    Storing detailed error information. Possibly coming from the JobRunner
    """

    message = StringField()
    code = IntField()
    name = StringField()
    error = StringField()


class TerminatedCode(Enum):
    """
    Reasons for why the job was cancelled
    """

    terminated_by_user = 0
    terminated_by_admin = 1
    terminated_by_automation = 2
    terminated_by_batch_abort = 3
    terminated_by_server_failure = 4


class Status(Enum):
    """
    A job begins at created, then can either be estimating
    """

    created = "created"
    estimating = "estimating"
    queued = "queued"
    running = "running"
    # finished = "finished"  # Successful termination legacy code
    completed = "completed"  # Successful termination
    error = "error"  # Something went wrong and job failed # Possible Reasons are (ErrorCodes)
    terminated = "terminated"  # Canceled by user, admin, or script # Possible Reasons are (TerminatedCodes)


class AuthStrat(Enum):
    """
    The strings to be passed to the auth service when checking to see if a given token
    has access to the workspace
    """

    DEFAULT = "DEFAULT"
    kbaseworkspace = "kbaseworkspace"
    execution_engine = "execution_engine"


class JobInputFile(EmbeddedDocument):
    # TODO USE THIS
    # TODO VERIFY UPA WITH REGEX or special function [0-9]+/[0-9]+/*[0-9]*
    upa = StringField()
    filename = StringField()
    filesize_mb = IntField()
    # Provenance?


class JobInputAttributes(Document):
    # TODO USE THIS
    job_input_filesizes = EmbeddedDocumentListField(JobInputFile)


class JobUsageAttributes(Document):
    # TODO USE THIS
    job_peak_cpu_usage = FloatField(default=0)
    job_peak_memory_usage_mb = IntField(default=0)
    job_peak_disk_usage_mb = IntField(default=0)


class JobResourceAttributes(Document):
    """
    Should be partially populated by the JobRunner at run time
    Can be periodically populated by the JobRunner during the lifecycle of the job
    Can be populated at the end of
    """

    client_group = StringField(required=True)
    request_cpus = IntField(required=True)
    request_memory = IntField(required=True)
    request_disk = IntField(required=True)


class Job(Document):
    """
    A job is created the execution engine service and it's updated from
    the job and the portal process for the rest of the time
    """

    # id.generation_time = created
    user = StringField(required=True)
    authstrat = StringField(
        required=True, default="kbaseworkspace", validation=valid_authstrat
    )
    wsid = IntField(required=False, default=None)
    status = StringField(required=True, validation=valid_status)

    updated = FloatField(default=time.time)

    # id.generation_time = created
    queued = FloatField(
        default=None
    )  # Time when job was submitted to the queue to be run
    estimating = FloatField(
        default=None
    )  # Time when job was submitted to begin estimating
    running = FloatField(default=None)  # Time when job started
    # Time when job finished, errored out, or was terminated by the user/admin
    finished = FloatField(default=None)

    errormsg = StringField()
    msg = StringField()
    error = DynamicField()

    terminated_code = IntField(validation=valid_termination_code)
    error_code = IntField(validation=valid_errorcode)
    batch_job = BooleanField(default=False)
    scheduler_type = StringField()
    scheduler_id = StringField()
    scheduler_estimator_id = StringField()
    job_input = EmbeddedDocumentField(JobInput, required=True)
    job_output = DynamicField()
    condor_job_ads = DynamicField()
    # this is the ID of the coordinating job created as part of run_job_batch. Only child jobs
    # in a "true" batch job maintained by EE2 should have this field. Coordinating jobs will
    # be updated with the child ID in child_jobs, unlike "fake" batch jobs that are created
    # outside of the EE2 codebase using the 'parent_job_id' field.
    batch_id = StringField()
    child_jobs = ListField()  # Only coordinating jobs should have child jobs
    # batch_parent_container = BooleanField(default=False) # Only parent container should have this
    retry_ids = ListField()  # The retry_parent has been used to launch these jobs
    # Only present on a retried job, not it's parent. If attempting to retry this job, use its parent instead
    retry_parent = StringField()
    retry_saved_toggle = BooleanField(
        default=False
    )  # Marked true when all retry steps have completed

    # See https://docs.mongoengine.org/guide/defining-documents.html#indexes
    # Hmm, are these indexes need to be + or - ?
    indexes = [("status", "batch_job"), ("status", "queued")]

    meta = {"collection": "ee2_jobs", "indexes": indexes}

    def save(self, *args, **kwargs):
        self.updated = time.time()
        return super(Job, self).save(*args, **kwargs)

    def __repr__(self):
        return self.to_json()


# class BatchJobCollection(Document):
#     """
#     A container for storing related batch job containers
#     Does this need to exist before creating a collection?
#     """
#
#     # User and wsid are used for permission handling
#     user = StringField(required=True)
#     wsid = IntField(required=False, default=None)
#     batch_jobs = ListField(required=True)
#     updated = FloatField(default=time.time)
#     title = StringField(required=False)
#     description = StringField(required=False)
#
#     def save(self, *args, **kwargs):
#         self.updated = time.time()
#         return super(BatchJobCollection, self).save(*args, **kwargs)
#
#     def __repr__(self):
#         return self.to_json()
#
#
# class BatchJobContainer(Document):
#     """
#     A container for storing jobs information
#     Can be created via run_job_batch endpoint, or through the UI/ee2 api,
#     or a running job with the ee2_client
#     """
#
#     meta = {"collection": "ee2_jobs"}
#     user = StringField(required=True)
#     wsid = IntField(required=False, default=None)
#     updated = FloatField(default=time.time)
#     scheduler_type = StringField(default="htcondor", required=False)
#     child_jobs = ListField(required=True)
#     title = StringField(required=False)
#     description = StringField(required=False)
#     meta = {"collection": "ee2_jobs"}
#
#     def save(self, *args, **kwargs):
#         self.updated = time.time()
#         return super(BatchJobContainer, self).save(*args, **kwargs)
#
#     def __repr__(self):
#         return self.to_json()


# Unused for now
class HeldJob(Document):
    job_id = ReferenceField(Job)
    used_cpu = IntField(required=True)
    used_memory = FloatField(required=True)
    used_memory_raw = FloatField(required=True)
    used_disk_raw = FloatField(required=True)
    over_memory = BooleanField(required=True)
    over_cpu = BooleanField(required=True)
    check_time = FloatField(required=True)
    node_jobs = IntField(required=True)
    node_cpu = FloatField(required=True)
    node_memory = FloatField(required=True)
    node_disk = FloatField(required=True)
    node_name = StringField(required=True)

    def save(self, *args, **kwargs):
        self.updated = time.time()
        return super(HeldJob, self).save(*args, **kwargs)


#
# Unused fields that we might want
#

result_example = {
    "shocknodes": [],
    "shockurl": "https://ci.kbase.us/services/shock-api/",
    "workspaceids": [],
    "workspaceurl": "https://ci.kbase.us/services/ws/",
    "results": [
        {
            "servtype": "Workspace",
            "url": "https://ci.kbase.us/services/ws/",
            "id": "psnovichkov:1450397093052/QQ",
            "desc": "description",
        }
    ],
    "prog": 0,
    "maxprog": None,
    "other": {
        "estcompl": None,
        "service": "bsadkhin",
        "desc": "Execution engine job for simpleapp.simple_add",
        "progtype": None,
    },
}


#
# Unused Stuff to look at
#


class Results(EmbeddedDocument):
    run_id = StringField()
    shockurl = StringField()
    workspaceids = ListField()
    workspaceurl = StringField()
    shocknodes = ListField()
