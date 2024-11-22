FROM kbase/sdkpython:3.8.10
MAINTAINER KBase Developer

RUN apt-get clean all && apt-get update --fix-missing -y && apt-get upgrade -y

# -----------------------------------------
# In this section, you can install any system dependencies required
# to run your App.  For instance, you could place an apt-get update or
# install line here, a git checkout to download code, or run any other
# installation scripts.
RUN apt-get install -y gcc wget vim htop tmpreaper
RUN mkdir -p /etc/apt/sources.list.d

# Install condor
RUN curl -fsSL https://get.htcondor.org | /bin/bash -s -- --no-dry-run

# Install jars for testing purposes
# Uncomment this if you want to run tests inside the ee2 container on MacOSX
# RUN cd /opt && git clone https://github.com/kbase/jars && cd -


# Install DOCKERIZE
RUN curl -o /tmp/dockerize.tgz https://raw.githubusercontent.com/kbase/dockerize/dist/dockerize-linux-amd64-v0.5.0.tar.gz && \
      cd /usr/bin && \
      tar xvzf /tmp/dockerize.tgz && \
      rm /tmp/dockerize.tgz


#Install Python3 and Libraries (source /root/miniconda/bin/activate)
RUN wget https://repo.anaconda.com/miniconda/Miniconda3-py310_24.5.0-0-Linux-x86_64.sh -O ~/miniconda.sh \
&& bash ~/miniconda.sh -b -p /miniconda-latest

# Setup Cron
COPY ./bin/ee2_cronjobs /etc/cron.d/ee2_cronjobs

# Need to change startup scripts to match this in MAKEFILE
ENV PATH=/miniconda-latest/bin:$PATH
RUN pip install --upgrade pip && python -V
COPY ./requirements.txt /kb/module/requirements.txt

RUN pip install -r /kb/module/requirements.txt
RUN adduser --disabled-password --gecos '' -shell /bin/bash kbase
# -----------------------------------------

COPY ./ /kb/module
RUN mkdir -p /kb/module/work && chmod -R a+rw /kb/module && mkdir -p /etc/condor/

WORKDIR /kb/module
# Due to older kb-sdk in this base image, getting some compilation results we don't want
# Will have to manually use the correct version of kbase-sdk to compile impl/Server files
RUN make build

# Remove Jars and old Conda for Trivy Scans and after compilation is done
RUN rm -rf /sdk && rm -rf /opt
RUN rm -rf /miniconda-latest/pkgs/conda-4.12.0-py39h06a4308_0/info/test/tests/data/env_metadata

WORKDIR /kb/module/scripts
RUN chmod +x download_runner.sh && ./download_runner.sh

WORKDIR /kb/module/

# Set deploy.cfg location
ENV KB_DEPLOYMENT_CONFIG=/kb/module/deploy.cfg
ENV PATH=/kb/module:$PATH

ENTRYPOINT [ "./scripts/entrypoint.sh" ]
CMD [ ]
