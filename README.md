# execution_engine2
  
[![Codacy Badge](https://api.codacy.com/project/badge/Grade/c1a997d83d834ba99e7cb4a88b945e05)](https://www.codacy.com/gh/kbase/execution_engine2?utm_source=github.com&amp;utm_medium=referral&amp;utm_content=kbase/execution_engine2&amp;utm_campaign=Badge_Grade)
[![codecov](https://codecov.io/gh/kbase/execution_engine2/branch/develop/graph/badge.svg)](https://codecov.io/gh/kbase/execution_engine2)
[![Quality Gate Status](https://sonarcloud.io/api/project_badges/measure?project=kbase_execution_engine2&metric=alert_status)](https://sonarcloud.io/dashboard?id=kbase_execution_engine2)
  
  
This is a [KBase](https://kbase.us) module generated by the [KBase Software Development Kit (SDK)](https://github.com/kbase/kb_sdk).  
  
You will need to have the SDK installed to use this module. [Learn more about the SDK and how to use it](https://kbase.github.io/kb_sdk_docs/).  
  
You can also learn more about the apps implemented in this module from its [catalog page](https://narrative.kbase.us/#catalog/modules/execution_engine2) or its [spec file]($module_name.spec).  

# Contributing

* Contributing requirements, such as pre-commit as per [CONTRIBUTING.rst](CONTRIBUTING.rst)


# Setup and test locally
  
See the .travis file for information on how to test locally

# Setup and test with docker-compose on MacOS/Linux

## Build and exec into the dev container 

Make sure you have the latest versions of 

* docker
* docker-compose

```
git clone https://github.com/kbase/execution_engine2.git
cd execution_engine2
docker build . -t execution_engine2:test
docker-compose up -d
docker-compose exec ee2_with_ssh bash
# (This directory is linked to your pwd via the docker-compose file)
cd /ee2
make test-coverage
```

Once the docker image is built, it does not need to be rebuilt after code changes to rerun tests.
Just ensure the services are up, exec into the container, and run the tests.

## To run a specific test directory or specific file
```
PYTHONPATH=.:lib:test pytest --cov-report=xml --cov lib/execution_engine2/ --verbose test/tests_for_db/
PYTHONPATH=.:lib:test pytest --cov-report=xml --cov lib/execution_engine2/ --verbose test/tests_for_db/ee2_model_test.py
```

## To run a specific test file via PyCharm
See [Testing with Pycharm](docs/testing_with_pycharm.md)

## To run pre-commit hooks

`exec` into the docker container as before and switch to the `/ee2` directory.

```
pip install pre-commit
pre-commit install
pre-commit run --all-files
```

To remove the pre commit hooks:
```
pre-commit uninstall
```

## Installing HTCondor Bindings from the mac
* You may not be able to load without disabling the mac Security Gatekeeper with `sudo spctl --master-disable`
* The HTCondor bindings only work on the Python.org install of python or your system install of python2.7. They will not work with anaconda. So download python from python.org
* Download the mac bindings at https://research.cs.wisc.edu/htcondor/tarball/current/8.9.10/release/
* Current version is [8.9.10](https://research.cs.wisc.edu/htcondor/tarball/current/8.9.10/release/condor-8.9.10-x86_64_MacOSX-unstripped.tar.gz)
* Add <condor>/lib/python3 to PYTHONPATH.
* `import htcondor`
  
## Test Running Options  
### PyCharm
* Use a remote ssh debugger with the correct path mappings
* Right click on the file you'd like to run and select run test

## Develop

* To add a bugfix or new feature:
    * Create a new feature branch, branching from `develop`. Ask a repo owner for help if
      necessary.
    * If you're a repo owner you can push directly to this branch. If not, make pull requests to
      the branch as necessary.
    * Add:
        * Feature / bugfix code
        * Tests
        * Documentation, if applicable
        * Release notes, if applicable
        * See the PR template in `worksflows/pull_request_template.md` for details
    * Once the feature is complete, create a PR from the feature branch to `develop` and request a
      review from person with EE2 knowledge via the Github interface and via Slack.
    * When the PR is approved, squash and merge into `develop` and delete the feature branch.
* To create a new release:
    * Increment the version as per [semantic versioning](https://semver.org/) in `kbase.yml`.
        * Update the release notes to the correct version, if necessary.
    * Run `make compile`.
    * Go through the process above to get the changes into `develop`.
    * Make a PR from `develop` to `main`.
    * Once the PR is apporoved, merge (no squash) to `main`.
    * Tag the merge commit in GitHub with the semantic version from `kbase.yml`.
 
## KBase Catalog interactions

### Client Groups

EE2 understands client group specifications in JSON and CSV formats. Both formats have special
fields in common:
* `request_cpus` - the number of CPUs to request
* `request_memory` - the amount of memory, in MB, to request
* `request_disk` - the amount of memory, in GB, to request
* `client_group_regex` - boolean - treat the client group (see below) as a regular expression
* `debug_mode` - boolean - run the job in debug mode

The client group is handled differently for JSON and CSV:
* The JSON format has the `clientgroup` field, which is optional.
* The CSV format must have the client group in the first 'column' of the CSV and is required. The
  remainder of the 'columns' must be in `key=value` format.

Any fields other than the above are sent on to the scheduler as key value pairs.

For example, to set the client group to `bigmem`, request 32 CPUs, 64GB of memory, and 1TB of disk,
the following would be entered in the catalog UI:
* CSV: `bigmem, request_cpus=32, request_memory=64000, request_disk=1000`
* JSON: `{"client_group": "bigmem", "request_cpus" : "32", "request_memory" : "64000", "request_disk" : "1000"}`

Note that the representation of this data in the catalog API is idiosyncratic - both the JSON and
CSV data are split by commas into parts. EE2 will detect JSON entries and reconsitute them before
deserialization.


# CronJobs/Reaper Scripts

### PurgeBadJobs
* Cronjobs are copied in and launched via the Dockerfile
* There are cronjobs configured in /etc/cron.d/ee2_cronjobs 
* You can monitor them by reading the logs in /root/cron-purge.log 

### PurgeHeldJobs
* This is a daemon launched by entrypoint.sh 
* It is not a cronjob because there is no way to easy way to seek through the HTCondor EXECUTE log, which takes a while to seek through

# Help  
  
Contact @Tianhao-Gu, @bio_boris, @briehl
