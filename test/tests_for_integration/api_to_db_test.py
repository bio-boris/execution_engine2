"""
Integration tests that cover the entire codebase from API to database.

NOTE 1: These tests are designed to only be runnable after running docker-compose up.

NOTE 2: These tests were set up quickly in order to debug a problem with administration related
calls. As such, the auth server was set up to run in test mode locally. If more integrations
are needed, they will need to be added either locally or as docker containers.
If the latter, the test auth and workspace integrations will likely need to be converted to
docker containers or exposed to other containers.

NOTE 3: Although this is supposed to be an integration test, the catalog service and htcondor
are still mocked out as bringing them up would take a large amount of effort. Someday...

NOTE 4: Kafka notes
    a) Currently nothing listens to the kafka feed.
    b) When running the tests, the kafka producer logs that kafka cannot be reached. However,
        this error is silent otherwise.
    c) I wasn't able to contact the docker kafka service with the kafka-python client either.
    d) As such, Kafka is not tested. Once tests are added, at least one test should check that
        something sensible happens if a kafka message cannot be sent.

NOTE 5: EE2 posting to Slack always fails silently in tests. Currently slack calls are not tested.
"""

# TODO add more integration tests, these are not necessarily exhaustive

import os
import tempfile
import time
import htcondor

from bson import ObjectId
from configparser import ConfigParser
from threading import Thread
from pathlib import Path
import pymongo
from pytest import fixture, raises
from typing import Dict
from unittest.mock import patch, create_autospec, ANY

from tests_for_integration.auth_controller import AuthController
from tests_for_integration.workspace_controller import WorkspaceController
from utils_shared.test_utils import (
    get_full_test_config,
    get_ee2_test_config,
    EE2_CONFIG_SECTION,
    KB_DEPLOY_ENV,
    find_free_port,
    create_auth_login_token,
    create_auth_user,
    create_auth_role,
    set_custom_roles,
    assert_close_to_now,
    assert_exception_correct,
)
from execution_engine2.sdk.EE2Constants import ADMIN_READ_ROLE, ADMIN_WRITE_ROLE
from installed_clients.baseclient import ServerError
from installed_clients.execution_engine2Client import execution_engine2 as ee2client
from installed_clients.WorkspaceClient import Workspace

# in the future remove this
from tests_for_utils.Condor_test import _get_common_sub

KEEP_TEMP_FILES = False
TEMP_DIR = Path("test_temp_can_delete")

# may need to make this configurable
JARS_DIR = Path("/opt/jars/lib/jars")

USER_READ_ADMIN = "readuser"
TOKEN_READ_ADMIN = None
USER_NO_ADMIN = "nouser"
TOKEN_NO_ADMIN = None
USER_WRITE_ADMIN = "writeuser"
TOKEN_WRITE_ADMIN = None

USER_KBASE_CONCIERGE = "kbaseconcierge"
TOKEN_KBASE_CONCIERGE = None

USER_WS_READ_ADMIN = "wsreadadmin"
TOKEN_WS_READ_ADMIN = None
USER_WS_FULL_ADMIN = "wsfulladmin"
TOKEN_WS_FULL_ADMIN = None
WS_READ_ADMIN = "WS_READ_ADMIN"
WS_FULL_ADMIN = "WS_FULL_ADMIN"

CAT_GET_MODULE_VERSION = "installed_clients.CatalogClient.Catalog.get_module_version"
CAT_LIST_CLIENT_GROUPS = (
    "installed_clients.CatalogClient.Catalog.list_client_group_configs"
)

# from test/deploy.cfg
MONGO_EE2_DB = "ee2"
MONGO_EE2_JOBS_COL = "ee2_jobs"


@fixture(scope="module")
def config() -> Dict[str, str]:
    yield get_ee2_test_config()


@fixture(scope="module")
def full_config() -> ConfigParser:
    yield get_full_test_config()


@fixture(scope="module")
def mongo_client(config):
    mc = pymongo.MongoClient(
        config["mongo-host"],
        username=config["mongo-user"],
        password=config["mongo-password"],
    )
    yield mc

    mc.close()


def _clean_db(mongo_client, db, db_user):
    try:
        mongo_client[db].command("dropUser", db_user)
    except pymongo.errors.OperationFailure as e:
        if f"User '{db_user}@{db}' not found" not in e.args[0]:
            raise  # otherwise ignore and continue, user is already toast
    mongo_client.drop_database(db)


def _create_db_user(mongo_client, db, db_user, password):
    mongo_client[db].command("createUser", db_user, pwd=password, roles=["readWrite"])


def _set_up_auth_user(auth_url, user, display, roles=None):
    create_auth_user(auth_url, user, display)
    if roles:
        set_custom_roles(auth_url, user, roles)
    return create_auth_login_token(auth_url, user)


def _set_up_auth_users(auth_url):
    create_auth_role(auth_url, ADMIN_READ_ROLE, "ee2 admin read doohickey")
    create_auth_role(auth_url, ADMIN_WRITE_ROLE, "ee2 admin write thinger")
    create_auth_role(auth_url, WS_READ_ADMIN, "wsr")
    create_auth_role(auth_url, WS_FULL_ADMIN, "wsf")

    global TOKEN_READ_ADMIN
    TOKEN_READ_ADMIN = _set_up_auth_user(
        auth_url, USER_READ_ADMIN, "display1", [ADMIN_READ_ROLE]
    )

    global TOKEN_NO_ADMIN
    TOKEN_NO_ADMIN = _set_up_auth_user(auth_url, USER_NO_ADMIN, "display2")

    global TOKEN_WRITE_ADMIN
    TOKEN_WRITE_ADMIN = _set_up_auth_user(
        auth_url, USER_WRITE_ADMIN, "display3", [ADMIN_WRITE_ROLE]
    )

    global TOKEN_KBASE_CONCIERGE
    TOKEN_KBASE_CONCIERGE = _set_up_auth_user(
        auth_url, USER_KBASE_CONCIERGE, "concierge"
    )

    global TOKEN_WS_READ_ADMIN
    TOKEN_WS_READ_ADMIN = _set_up_auth_user(
        auth_url, USER_WS_READ_ADMIN, "wsra", [WS_READ_ADMIN]
    )

    global TOKEN_WS_FULL_ADMIN
    TOKEN_WS_FULL_ADMIN = _set_up_auth_user(
        auth_url, USER_WS_FULL_ADMIN, "wsrf", [WS_FULL_ADMIN]
    )


@fixture(scope="module")
def auth_url(config, mongo_client):
    auth_db = "api_to_db_auth_test"
    auth_mongo_user = "auth"
    # clean up from any previously failed test runs that left the db in place
    _clean_db(mongo_client, auth_db, auth_mongo_user)

    # make a user for the auth db
    _create_db_user(mongo_client, auth_db, auth_mongo_user, "authpwd")

    auth = AuthController(
        JARS_DIR,
        config["mongo-host"],
        auth_db,
        TEMP_DIR,
        mongo_user=auth_mongo_user,
        mongo_pwd="authpwd",
    )
    print(
        f"Started KBase Auth2 {auth.version} on port {auth.port} "
        + f"in dir {auth.temp_dir} in {auth.startup_count}s"
    )
    url = f"http://localhost:{auth.port}"

    _set_up_auth_users(url)

    yield url

    print(f"shutting down auth, KEEP_TEMP_FILES={KEEP_TEMP_FILES}")
    auth.destroy(not KEEP_TEMP_FILES)

    # Because the tests are run with mongo in a persistent docker container via docker-compose,
    # we need to clean up after ourselves.
    _clean_db(mongo_client, auth_db, auth_mongo_user)


def _add_ws_types(ws_controller):
    wsc = Workspace(f"http://localhost:{ws_controller.port}", token=TOKEN_WS_FULL_ADMIN)
    wsc.request_module_ownership("Trivial")
    wsc.administer({"command": "approveModRequest", "module": "Trivial"})
    wsc.register_typespec(
        {
            "spec": """
                module Trivial {
                    /* @optional dontusethisfieldorifyoudomakesureitsastring */
                    typedef structure {
                        string dontusethisfieldorifyoudomakesureitsastring;
                    } Object;
                };
                """,
            "dryrun": 0,
            "new_types": ["Object"],
        }
    )
    wsc.release_module("Trivial")


@fixture(scope="module")
def ws_controller(config, mongo_client, auth_url):
    ws_db = "api_to_db_ws_test"
    ws_types_db = "api_to_db_ws_types_test"
    ws_mongo_user = "workspace"
    # clean up from any previously failed test runs that left the db in place
    _clean_db(mongo_client, ws_db, ws_mongo_user)
    _clean_db(mongo_client, ws_types_db, ws_mongo_user)

    # make a user for the ws dbs
    _create_db_user(mongo_client, ws_db, ws_mongo_user, "wspwd")
    _create_db_user(mongo_client, ws_types_db, ws_mongo_user, "wspwd")

    ws = WorkspaceController(
        JARS_DIR,
        config["mongo-host"],
        ws_db,
        ws_types_db,
        auth_url + "/testmode/",
        TEMP_DIR,
        mongo_user=ws_mongo_user,
        mongo_pwd="wspwd",
    )
    print(
        f"Started KBase Workspace {ws.version} on port {ws.port} "
        + f"in dir {ws.temp_dir} in {ws.startup_count}s"
    )
    _add_ws_types(ws)

    yield ws

    print(f"shutting down workspace, KEEP_TEMP_FILES={KEEP_TEMP_FILES}")
    ws.destroy(not KEEP_TEMP_FILES)

    # Because the tests are run with mongo in a persistent docker container via docker-compose,
    # we need to clean up after ourselves.
    _clean_db(mongo_client, ws_db, ws_mongo_user)
    _clean_db(mongo_client, ws_types_db, ws_mongo_user)


def _update_config_and_create_config_file(full_config, auth_url, ws_controller):
    """
    Updates the config in place with the correct auth url for the tests and
    writes the updated config to a temporary file.

    Returns the file path.
    """
    # Don't call get_ee2_test_config here, we *want* to update the config object in place
    # so any other tests that use the config fixture run against the test auth server if they
    # access those keys
    ee2c = full_config[EE2_CONFIG_SECTION]
    ee2c["auth-service-url"] = auth_url + "/testmode/api/legacy/KBase/Sessions/Login"
    ee2c["auth-service-url-v2"] = auth_url + "/testmode/api/v2/token"
    ee2c["auth-url"] = auth_url + "/testmode"
    ee2c["auth-service-url-allow-insecure"] = "true"
    ee2c["workspace-url"] = f"http://localhost:{ws_controller.port}"

    deploy = tempfile.mkstemp(".cfg", "deploy-", dir=TEMP_DIR, text=True)
    os.close(deploy[0])

    with open(deploy[1], "w") as handle:
        full_config.write(handle)

    return deploy[1]


def _clear_dbs(
    mc: pymongo.MongoClient, config: Dict[str, str], ws_controller: WorkspaceController
):
    ee2 = mc[config["mongo-database"]]
    for name in ee2.list_collection_names():
        if not name.startswith("system."):
            # don't drop collection since that drops indexes
            ee2.get_collection(name).delete_many({})
    ws_controller.clear_db()


@fixture(scope="module")
def service(full_config, auth_url, mongo_client, config, ws_controller):
    # also updates the config in place so it contains the correct auth urls for any other
    # methods that use the config fixture
    cfgpath = _update_config_and_create_config_file(
        full_config, auth_url, ws_controller
    )
    print(f"created test deploy at {cfgpath}")
    _clear_dbs(mongo_client, config, ws_controller)

    prior_deploy = os.environ[KB_DEPLOY_ENV]
    # from this point on, calling the get_*_test_config methods will get the temp config file
    os.environ[KB_DEPLOY_ENV] = cfgpath
    # The server creates the configuration, impl, and application *AT IMPORT TIME* so we have to
    # import *after* setting the config path.
    # This is terrible design. Awful. It definitely wasn't me that wrote it over Xmas in 2012
    from execution_engine2 import execution_engine2Server

    portint = find_free_port()
    Thread(
        target=execution_engine2Server.start_server,
        kwargs={"port": portint},
        daemon=True,
    ).start()
    time.sleep(0.05)
    port = str(portint)
    print("running ee2 service at localhost:" + port)
    yield port

    # shutdown the server
    # SampleServiceServer.stop_server()  <-- this causes an error.
    # See the server file for the full scoop, but in short, the stop method expects a _proc
    # package variable to be set, but start doesn't always set it, and that causes an error.

    # Tests are run in the same process so we need to be put the environment back the way it was
    os.environ[KB_DEPLOY_ENV] = prior_deploy

    if not KEEP_TEMP_FILES:
        os.remove(cfgpath)


@fixture
def ee2_port(service, mongo_client, config, ws_controller):
    _clear_dbs(mongo_client, config, ws_controller)

    yield service


def test_is_admin_success(ee2_port):
    ee2cli_read = ee2client("http://localhost:" + ee2_port, token=TOKEN_READ_ADMIN)
    ee2cli_no = ee2client("http://localhost:" + ee2_port, token=TOKEN_NO_ADMIN)
    ee2cli_write = ee2client("http://localhost:" + ee2_port, token=TOKEN_WRITE_ADMIN)

    # note that if we ever need to have Java talk to ee2 these responses will break the SDK client
    assert ee2cli_read.is_admin() is True
    assert ee2cli_no.is_admin() is False
    assert ee2cli_write.is_admin() is True


def test_get_admin_permission_success(ee2_port):
    ee2cli_read = ee2client("http://localhost:" + ee2_port, token=TOKEN_READ_ADMIN)
    ee2cli_no = ee2client("http://localhost:" + ee2_port, token=TOKEN_NO_ADMIN)
    ee2cli_write = ee2client("http://localhost:" + ee2_port, token=TOKEN_WRITE_ADMIN)

    assert ee2cli_read.get_admin_permission() == {"permission": "r"}
    assert ee2cli_no.get_admin_permission() == {"permission": "n"}
    assert ee2cli_write.get_admin_permission() == {"permission": "w"}


######## run_job tests ########


def _get_htc_mocks():
    sub = create_autospec(htcondor.Submit, spec_set=True, instance=True)
    schedd = create_autospec(htcondor.Schedd, spec_set=True, instance=True)
    txn = create_autospec(htcondor.Transaction, spec_set=True, instance=True)
    return sub, schedd, txn


def _finish_htc_mocks(sub_init, schedd_init, sub, schedd, txn):
    sub_init.return_value = sub
    schedd_init.return_value = schedd
    # mock context manager ops
    schedd.transaction.return_value = txn
    txn.__enter__.return_value = txn
    return sub, schedd, txn


def _check_htc_calls(sub_init, sub, schedd_init, schedd, txn, expected_sub):
    sub_init.assert_called_once_with(expected_sub)
    schedd_init.assert_called_once_with()
    schedd.transaction.assert_called_once_with()
    sub.queue.assert_called_once_with(txn, 1)


def _set_up_workspace_objects(ws_controller, token):
    wsc = Workspace(ws_controller.get_url(), token=token)
    wsc.create_workspace({"workspace": "foo"})
    wsc.save_objects(
        {
            "id": 1,
            "objects": [
                {"name": "one", "type": "Trivial.Object-1.0", "data": {}},
                {"name": "two", "type": "Trivial.Object-1.0", "data": {}},
            ],
        }
    )


def _get_run_job_param_set():
    return {
        "method": "mod.meth",
        "app_id": "mod/app",
        "wsid": 1,
        "source_ws_objects": ["1/1/1", "1/2/1"],
        "params": [{"foo": "bar"}, 42],
        "service_ver": "beta",
        "parent_job_id": "totallywrongid",
        "meta": {
            "run_id": "rid",
            "token_id": "tid",
            "tag": "yourit",
            "cell_id": "cid",
            "thiskey": "getssilentlydropped",
        },
    }


def _get_condor_sub_for_rj_param_set(job_id, user, token, clientgroup, cpu, mem, disk):
    expected_sub = _get_common_sub(job_id)
    expected_sub.update(
        {
            "JobBatchName": job_id,
            "arguments": f"{job_id} https://ci.kbase.us/services/ee2",
            "+KB_PARENT_JOB_ID": '"totallywrongid"',
            "+KB_MODULE_NAME": '"mod"',
            "+KB_FUNCTION_NAME": '"meth"',
            "+KB_APP_ID": '"mod/app"',
            "+KB_APP_MODULE_NAME": '"mod"',
            "+KB_WSID": '"1"',
            "+KB_SOURCE_WS_OBJECTS": '"1/1/1,1/2/1"',
            "request_cpus": f"{cpu}",
            "request_memory": f"{mem}MB",
            "request_disk": f"{disk}GB",
            "requirements": f'regexp("{clientgroup}",CLIENTGROUP)',
            "+KB_CLIENTGROUP": f'"{clientgroup}"',
            "Concurrency_Limits": f"{user}",
            "+AccountingGroup": f'"{user}"',
            "environment": (
                '"DOCKER_JOB_TIMEOUT=604805 KB_ADMIN_AUTH_TOKEN=test_auth_token '
                + f"KB_AUTH_TOKEN={token} CLIENTGROUP={clientgroup} JOB_ID={job_id} "
                + "CONDOR_ID=$(Cluster).$(Process) PYTHON_EXECUTABLE=/miniconda/bin/python "
                + 'DEBUG_MODE=False PARENT_JOB_ID=totallywrongid "'
            ),
            "leavejobinqueue": "true",
            "initial_dir": "../scripts/",
            "+Owner": '"condor_pool"',
            "executable": "../scripts//../scripts/execute_runner.sh",
            "transfer_input_files": "../scripts/JobRunner.tgz",
        }
    )
    return expected_sub


def _check_mongo_job(mongo_client, job_id, user, clientgroup, cpu, mem, disk, githash):
    job = mongo_client[MONGO_EE2_DB][MONGO_EE2_JOBS_COL].find_one(
        {"_id": ObjectId(job_id)}
    )
    assert_close_to_now(job.pop("updated"))
    assert_close_to_now(job.pop("queued"))
    expected_job = {
        "_id": ObjectId(job_id),
        "user": user,
        "authstrat": "kbaseworkspace",
        "wsid": 1,
        "status": "queued",
        "job_input": {
            "wsid": 1,
            "method": "mod.meth",
            "params": [{"foo": "bar"}, 42],
            "service_ver": githash,
            "app_id": "mod/app",
            "source_ws_objects": ["1/1/1", "1/2/1"],
            "parent_job_id": "totallywrongid",
            "requirements": {
                "clientgroup": clientgroup,
                "cpu": cpu,
                "memory": mem,
                "disk": disk,
            },
            "narrative_cell_info": {
                "run_id": "rid",
                "token_id": "tid",
                "tag": "yourit",
                "cell_id": "cid",
            },
        },
        "child_jobs": [],
        "batch_job": False,
        "scheduler_id": "123",
        "scheduler_type": "condor",
    }
    assert job == expected_job


def test_run_job(ee2_port, ws_controller, mongo_client):
    """
    A test of the run_job method.
    """
    _set_up_workspace_objects(ws_controller, TOKEN_NO_ADMIN)
    # need to get the mock objects first so spec_set can do its magic before we mock out
    # the classes in the context manager
    sub, schedd, txn = _get_htc_mocks()
    # seriously black you're killing me here. This is readable?
    with patch("htcondor.Submit", spec_set=True, autospec=True) as sub_init, patch(
        "htcondor.Schedd", spec_set=True, autospec=True
    ) as schedd_init, patch(
        CAT_LIST_CLIENT_GROUPS, spec_set=True, autospec=True
    ) as list_cgroups, patch(
        CAT_GET_MODULE_VERSION, spec_set=True, autospec=True
    ) as get_mod_ver:
        # set up the rest of the mocks
        _finish_htc_mocks(sub_init, schedd_init, sub, schedd, txn)
        sub.queue.return_value = 123
        list_cgroups.return_value = [
            {"client_groups": ['{"request_cpus":8,"request_memory":5}']}
        ]
        get_mod_ver.return_value = {"git_commit_hash": "somehash"}

        # run the method
        ee2 = ee2client(f"http://localhost:{ee2_port}", token=TOKEN_NO_ADMIN)
        job_id = ee2.run_job(_get_run_job_param_set())

        # check that mocks were called correctly
        # Since these are class methods, the first argument is self, which we ignore
        get_mod_ver.assert_called_once_with(
            ANY, {"module_name": "mod", "version": "beta"}
        )
        list_cgroups.assert_called_once_with(
            ANY, {"module_name": "mod", "function_name": "meth"}
        )

        expected_sub = _get_condor_sub_for_rj_param_set(
            job_id, USER_NO_ADMIN, TOKEN_NO_ADMIN, "njs", 8, 5, 30
        )
        _check_htc_calls(sub_init, sub, schedd_init, schedd, txn, expected_sub)

        _check_mongo_job(
            mongo_client, job_id, USER_NO_ADMIN, "njs", 8, 5, 30, "somehash"
        )


def test_run_job_fail_no_workspace_access(ee2_port):
    params = {"method": "mod.meth", "app_id": "mod/app", "wsid": 1}
    # this error could probably use some cleanup
    err = (
        "('An error occurred while fetching user permissions from the Workspace', "
        + "ServerError('No workspace with id 1 exists'))"
    )
    _run_job_fail(ee2_port, TOKEN_NO_ADMIN, params, err)


def test_run_job_fail_bad_method(ee2_port):
    params = {"method": "mod.meth.moke", "app_id": "mod/app"}
    err = "Unrecognized method: 'mod.meth.moke'. Please input module_name.function_name"
    _run_job_fail(ee2_port, TOKEN_NO_ADMIN, params, err)


def test_run_job_fail_bad_app(ee2_port):
    params = {"method": "mod.meth", "app_id": "mod.app"}
    err = "Application ID 'mod.app' contains a '.'"
    _run_job_fail(ee2_port, TOKEN_NO_ADMIN, params, err)


def test_run_job_fail_bad_upa(ee2_port):
    params = {
        "method": "mod.meth",
        "app_id": "mod/app",
        "source_ws_objects": ["ws/obj/1"],
    }
    err = (
        "source_ws_objects index 0, 'ws/obj/1', is not a valid Unique Permanent Address"
    )
    _run_job_fail(ee2_port, TOKEN_NO_ADMIN, params, err)


def test_run_job_fail_no_such_object(ee2_port, ws_controller):
    # Set up workspace and objects
    wsc = Workspace(ws_controller.get_url(), token=TOKEN_NO_ADMIN)
    wsc.create_workspace({"workspace": "foo"})
    wsc.save_objects(
        {
            "id": 1,
            "objects": [
                {"name": "one", "type": "Trivial.Object-1.0", "data": {}},
            ],
        }
    )
    params = {"method": "mod.meth", "app_id": "mod/app", "source_ws_objects": ["1/2/1"]}
    err = "Some workspace object is inaccessible"
    _run_job_fail(ee2_port, TOKEN_NO_ADMIN, params, err)


def _run_job_fail(ee2_port, token, params, expected, throw_exception=False):
    client = ee2client(f"http://localhost:{ee2_port}", token=token)
    if throw_exception:
        client.run_job(params)
    else:
        with raises(ServerError) as got:
            client.run_job(params)
        assert_exception_correct(got.value, ServerError("name", 1, expected))


######## run_job_concierge tests ########


def test_run_job_concierge_minimal(ee2_port, ws_controller, mongo_client):
    def modify_sub(sub):
        del sub["Concurrency_Limits"]

    _run_job_concierge(
        ee2_port=ee2_port,
        ws_controller=ws_controller,
        mongo_client=mongo_client,
        # if the concierge dict is empty, regular old run_job gets run
        conc_params={"trigger": "concierge"},  # contents are ignored
        modify_sub=modify_sub,
        clientgroup="concierge",
        cpu=4,
        mem=23000,
        disk=100,
    )


def test_run_job_concierge_mixed(ee2_port, ws_controller, mongo_client):
    """
    Gets cpu from the input, memory from deploy.cfg, and disk from the catalog.
    """

    def modify_sub(sub):
        del sub["Concurrency_Limits"]

    _run_job_concierge(
        ee2_port=ee2_port,
        ws_controller=ws_controller,
        mongo_client=mongo_client,
        conc_params={"client_group": "extreme", "request_cpus": 76},
        modify_sub=modify_sub,
        clientgroup="extreme",
        cpu=76,
        mem=250000,
        disk=7,
        catalog_return=[{"client_groups": ['{"request_cpus":8,"request_disk":7}']}],
    )


def test_run_job_concierge_maximal(ee2_port, ws_controller, mongo_client):
    def modify_sub(sub):
        sub[
            "requirements"
        ] = '(CLIENTGROUP == "bigmem") && (baz == "bat") && (foo == "bar")'
        sub["Concurrency_Limits"] = "some_sucker"
        sub["+AccountingGroup"] = '"some_sucker"'
        sub["environment"] = sub["environment"].replace(
            "DEBUG_MODE=False", "DEBUG_MODE=True"
        )

    _run_job_concierge(
        ee2_port=ee2_port,
        ws_controller=ws_controller,
        mongo_client=mongo_client,
        conc_params={
            "client_group": "bigmem",
            "request_cpus": 42,
            "request_memory": 56,
            "request_disk": 89,
            "client_group_regex": False,
            "account_group": "some_sucker",
            "ignore_concurrency_limits": False,
            "requirements_list": ["foo=bar", "baz=bat"],
            "debug_mode": "true",
        },
        modify_sub=modify_sub,
        clientgroup="bigmem",
        cpu=42,
        mem=56,
        disk=89,
    )


def _run_job_concierge(
    ee2_port,
    ws_controller,
    mongo_client,
    conc_params,
    modify_sub,
    clientgroup,
    cpu,
    mem,
    disk,
    catalog_return=None,
):
    _set_up_workspace_objects(ws_controller, TOKEN_KBASE_CONCIERGE)
    # need to get the mock objects first so spec_set can do its magic before we mock out
    # the classes in the context manager
    sub, schedd, txn = _get_htc_mocks()
    # seriously black you're killing me here. This is readable?
    with patch("htcondor.Submit", spec_set=True, autospec=True) as sub_init, patch(
        "htcondor.Schedd", spec_set=True, autospec=True
    ) as schedd_init, patch(
        CAT_LIST_CLIENT_GROUPS, spec_set=True, autospec=True
    ) as list_cgroups, patch(
        CAT_GET_MODULE_VERSION, spec_set=True, autospec=True
    ) as get_mod_ver:
        # set up the rest of the mocks
        _finish_htc_mocks(sub_init, schedd_init, sub, schedd, txn)
        sub.queue.return_value = 123
        list_cgroups.return_value = catalog_return or []
        get_mod_ver.return_value = {"git_commit_hash": "somehash"}

        # run the method
        ee2 = ee2client(f"http://localhost:{ee2_port}", token=TOKEN_KBASE_CONCIERGE)
        # if the concierge dict is empty, regular old run_job gets run
        job_id = ee2.run_job_concierge(_get_run_job_param_set(), conc_params)

        # check that mocks were called correctly
        # Since these are class methods, the first argument is self, which we ignore
        get_mod_ver.assert_called_once_with(
            ANY, {"module_name": "mod", "version": "beta"}
        )
        list_cgroups.assert_called_once_with(
            ANY, {"module_name": "mod", "function_name": "meth"}
        )

        expected_sub = _get_condor_sub_for_rj_param_set(
            job_id,
            USER_KBASE_CONCIERGE,
            TOKEN_KBASE_CONCIERGE,
            clientgroup,
            cpu,
            mem,
            disk,
        )
        modify_sub(expected_sub)

        _check_htc_calls(sub_init, sub, schedd_init, schedd, txn, expected_sub)

        _check_mongo_job(
            mongo_client,
            job_id,
            USER_KBASE_CONCIERGE,
            clientgroup,
            cpu,
            mem,
            disk,
            "somehash",
        )


def test_run_job_concierge_fail_no_workspace_access(ee2_port):
    params = {"method": "mod.meth", "app_id": "mod/app", "wsid": 1}
    # this error could probably use some cleanup
    err = (
        "('An error occurred while fetching user permissions from the Workspace', "
        + "ServerError('No workspace with id 1 exists'))"
    )
    _run_job_concierge_fail(ee2_port, TOKEN_KBASE_CONCIERGE, params, {"a": "b"}, err)


def test_run_job_concierge_fail_not_concierge(ee2_port):
    params = {"method": "mod.meth", "app_id": "mod/app"}
    err = "You are not the concierge user. This method is not for you"
    _run_job_concierge_fail(ee2_port, TOKEN_NO_ADMIN, params, {"a": "b"}, err)


def test_run_job_concierge_fail_bad_method(ee2_port):
    params = {"method": "mod.meth.moke", "app_id": "mod/app"}
    err = "Unrecognized method: 'mod.meth.moke'. Please input module_name.function_name"
    _run_job_concierge_fail(ee2_port, TOKEN_KBASE_CONCIERGE, params, {"a": "b"}, err)


def test_run_job_concierge_fail_reqs_list_not_list(ee2_port):
    params = {"method": "mod.meth", "app_id": "mod/app"}
    conc_params = {"requirements_list": {"a": "b"}}
    err = "requirements_list must be a list"
    _run_job_concierge_fail(ee2_port, TOKEN_KBASE_CONCIERGE, params, conc_params, err)


def test_run_job_concierge_fail_reqs_list_bad_req(ee2_port):
    params = {"method": "mod.meth", "app_id": "mod/app"}
    conc_params = {"requirements_list": ["a=b", "touchmymonkey"]}
    err = "Found illegal requirement in requirements_list: touchmymonkey"
    _run_job_concierge_fail(ee2_port, TOKEN_KBASE_CONCIERGE, params, conc_params, err)


def test_run_job_concierge_fail_bad_cpu(ee2_port):
    params = {"method": "mod.meth", "app_id": "mod/app"}
    conc_params = {"request_cpus": [2]}
    err = "Found illegal cpu request '[2]' in job requirements from concierge parameters"
    _run_job_concierge_fail(ee2_port, TOKEN_KBASE_CONCIERGE, params, conc_params, err)


def test_run_job_concierge_fail_bad_mem(ee2_port):
    params = {"method": "mod.meth", "app_id": "mod/app"}
    conc_params = {"request_memory": "-3"}
    err = "memory in MB must be at least 1"
    _run_job_concierge_fail(ee2_port, TOKEN_KBASE_CONCIERGE, params, conc_params, err)


def test_run_job_concierge_fail_bad_disk(ee2_port):
    params = {"method": "mod.meth", "app_id": "mod/app"}
    conc_params = {"request_disk": 4.5}
    err = "Found illegal disk request '4.5' in job requirements from concierge parameters"
    _run_job_concierge_fail(ee2_port, TOKEN_KBASE_CONCIERGE, params, conc_params, err)


def test_run_job_concierge_fail_bad_clientgroup(ee2_port):
    params = {"method": "mod.meth", "app_id": "mod/app"}
    conc_params = {"client_group": "fakefakefake"}
    err = "No such clientgroup: fakefakefake"
    _run_job_concierge_fail(ee2_port, TOKEN_KBASE_CONCIERGE, params, conc_params, err)


def test_run_job_concierge_fail_bad_clientgroup_regex(ee2_port):
    params = {"method": "mod.meth", "app_id": "mod/app"}
    conc_params = {"client_group_regex": "now I have 2 problems"}
    err = ("Found illegal client group regex 'now I have 2 problems' in job requirements "
           + "from concierge parameters")
    _run_job_concierge_fail(ee2_port, TOKEN_KBASE_CONCIERGE, params, conc_params, err)


def test_run_job_concierge_fail_bad_catalog_data(ee2_port):
    with patch(CAT_LIST_CLIENT_GROUPS, spec_set=True, autospec=True) as list_cgroups:
        list_cgroups.return_value = [{"client_groups": ['{"request_cpus":-8}']}]

        params = {"method": "mod.meth", "app_id": "mod/app"}
        conc_params = {"request_memory": 9}
        # TODO this is not a useful error for the user. Need to change the job reqs resolver
        # However, getting this wrong in the catalog is not super likely so not urgent
        err = "CPU count must be at least 1"
        _run_job_concierge_fail(ee2_port, TOKEN_KBASE_CONCIERGE, params, conc_params, err)


def test_run_job_concierge_fail_bad_reqs_item(ee2_port):
    params = {"method": "mod.meth", "app_id": "mod/app"}
    conc_params = {"requirements_list": ["a=b", "=c"]}
    # this error isn't the greatest but as I understand it the concierge endpoint is going
    # to become redundant so don't worry about it for now
    err = "Missing input parameter: key in scheduler requirements structure"
    _run_job_concierge_fail(ee2_port, TOKEN_KBASE_CONCIERGE, params, conc_params, err)


def test_run_job_concierge_fail_bad_debug_mode(ee2_port):
    params = {"method": "mod.meth", "app_id": "mod/app"}
    conc_params = {"debug_mode": "debug debug debug"}
    err = ("Found illegal debug mode 'debug debug debug' in job requirements from "
           + "concierge parameters")
    _run_job_concierge_fail(ee2_port, TOKEN_KBASE_CONCIERGE, params, conc_params, err)


def test_run_job_concierge_fail_bad_app(ee2_port):
    params = {"method": "mod.meth", "app_id": "mod.app"}
    err = "Application ID 'mod.app' contains a '.'"
    _run_job_concierge_fail(ee2_port, TOKEN_KBASE_CONCIERGE, params, {"a": "b"}, err)


def test_run_job_concierge_fail_bad_upa(ee2_port):
    params = {
        "method": "mod.meth",
        "app_id": "mod/app",
        "source_ws_objects": ["ws/obj/1"],
    }
    err = "source_ws_objects index 0, 'ws/obj/1', is not a valid Unique Permanent Address"
    _run_job_concierge_fail(ee2_port, TOKEN_KBASE_CONCIERGE, params, {"a": "b"}, err)


def test_run_job_concierge_fail_no_such_object(ee2_port, ws_controller):
    # Set up workspace and objects
    wsc = Workspace(ws_controller.get_url(), token=TOKEN_NO_ADMIN)
    wsc.create_workspace({"workspace": "foo"})
    wsc.save_objects(
        {
            "id": 1,
            "objects": [
                {"name": "one", "type": "Trivial.Object-1.0", "data": {}},
            ],
        }
    )
    params = {"method": "mod.meth", "app_id": "mod/app", "source_ws_objects": ["1/2/1"]}
    err = "Some workspace object is inaccessible"
    _run_job_concierge_fail(ee2_port, TOKEN_KBASE_CONCIERGE, params, {"a": "b"}, err)


def _run_job_concierge_fail(
    ee2_port, token, params, conc_params, expected, throw_exception=False
):
    client = ee2client(f"http://localhost:{ee2_port}", token=token)
    if throw_exception:
        client.run_job_concierge(params, conc_params)
    else:
        with raises(ServerError) as got:
            client.run_job_concierge(params, conc_params)
        assert_exception_correct(got.value, ServerError("name", 1, expected))
