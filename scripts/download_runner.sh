#!/bin/bash
# This script downloads the runner and copies it into the /runner/JobRunner.tgz
# The entrypoint.sh copies this into the /condor/shared directory
set -x
runner_dir=/runner
mkdir -p ${runner_dir} && cd ${runner_dir} && rm -rf JobRunner
git clone --single-branch --branch main https://github.com/kbase/JobRunner.git
rm -rf JobRunner/test
rm -rf JobRunner/.git
tar -czvf ${runner_dir}/JobRunner.tgz JobRunner
