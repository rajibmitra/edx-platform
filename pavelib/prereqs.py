"""
Install Python and Node prerequisites.
"""
from __future__ import print_function

import hashlib
import os
import re
import sys
import subprocess
import io
from distutils import sysconfig

from paver.easy import BuildFailure, sh, task

from .utils.envs import Env
from .utils.timer import timed

PREREQS_STATE_DIR = os.getenv('PREREQ_CACHE_DIR', Env.REPO_ROOT / '.prereqs_cache')
NO_PREREQ_MESSAGE = "NO_PREREQ_INSTALL is set, not installing prereqs"
NO_PYTHON_UNINSTALL_MESSAGE = 'NO_PYTHON_UNINSTALL is set. No attempts will be made to uninstall old Python libs.'
COVERAGE_REQ_FILE = 'requirements/edx/coverage.txt'

# If you make any changes to this list you also need to make
# a corresponding change to circle.yml, which is how the python
# prerequisites are installed for builds on circleci.com
if 'TOXENV' in os.environ:
    PYTHON_REQ_FILES = ['requirements/edx/testing.txt']
else:
    PYTHON_REQ_FILES = ['requirements/edx/development.txt']

# Developers can have private requirements, for local copies of github repos,
# or favorite debugging tools, etc.
PRIVATE_REQS = 'requirements/private.txt'
if os.path.exists(PRIVATE_REQS):
    PYTHON_REQ_FILES.append(PRIVATE_REQS)


def str2bool(s):
    s = str(s)
    return s.lower() in ('yes', 'true', 't', '1')


def no_prereq_install():
    """
    Determine if NO_PREREQ_INSTALL should be truthy or falsy.
    """
    return str2bool(os.environ.get('NO_PREREQ_INSTALL', 'False'))


def no_python_uninstall():
    """ Determine if we should run the uninstall_python_packages task. """
    return str2bool(os.environ.get('NO_PYTHON_UNINSTALL', 'False'))


def create_prereqs_cache_dir():
    """Create the directory for storing the hashes, if it doesn't exist already."""
    try:
        os.makedirs(PREREQS_STATE_DIR)
    except OSError:
        if not os.path.isdir(PREREQS_STATE_DIR):
            raise


def compute_fingerprint(path_list):
    """
    Hash the contents of all the files and directories in `path_list`.
    Returns the hex digest.
    """

    hasher = hashlib.sha1()

    for path_item in path_list:

        # For directories, create a hash based on the modification times
        # of first-level subdirectories
        if os.path.isdir(path_item):
            for dirname in sorted(os.listdir(path_item)):
                path_name = os.path.join(path_item, dirname)
                if os.path.isdir(path_name):
                    hasher.update(str(os.stat(path_name).st_mtime))

        # For files, hash the contents of the file
        if os.path.isfile(path_item):
            with io.open(path_item, "rb") as file_handle:
                hasher.update(file_handle.read())

    return hasher.hexdigest()


def prereq_cache(cache_name, paths, install_func):
    """
    Conditionally execute `install_func()` only if the files/directories
    specified by `paths` have changed.

    If the code executes successfully (no exceptions are thrown), the cache
    is updated with the new hash.
    """
    # Retrieve the old hash
    cache_filename = cache_name.replace(" ", "_")
    cache_file_path = os.path.join(PREREQS_STATE_DIR, "{}.sha1".format(cache_filename))
    old_hash = None
    if os.path.isfile(cache_file_path):
        with io.open(cache_file_path, "rb") as cache_file:
            old_hash = cache_file.read()

    # Compare the old hash to the new hash
    # If they do not match (either the cache hasn't been created, or the files have changed),
    # then execute the code within the block.
    new_hash = compute_fingerprint(paths)
    if new_hash != old_hash:
        install_func()

        # Update the cache with the new hash
        # If the code executed within the context fails (throws an exception),
        # then this step won't get executed.
        create_prereqs_cache_dir()
        with io.open(cache_file_path, "wb") as cache_file:
            # Since the pip requirement files are modified during the install
            # process, we need to store the hash generated AFTER the installation
            post_install_hash = compute_fingerprint(paths)
            cache_file.write(post_install_hash)
    else:
        print(u'{cache} unchanged, skipping...'.format(cache=cache_name))


def node_prereqs_installation():
    """
    Configures npm and installs Node prerequisites
    """

    # NPM installs hang sporadically. Log the installation process so that we
    # determine if any packages are chronic offenders.
    shard_str = os.getenv('SHARD', None)
    if shard_str:
        npm_log_file_path = '{}/npm-install.{}.log'.format(Env.GEN_LOG_DIR, shard_str)
    else:
        npm_log_file_path = '{}/npm-install.log'.format(Env.GEN_LOG_DIR)
    npm_log_file = io.open(npm_log_file_path, 'wb')
    npm_command = 'npm install --verbose'.split()

    cb_error_text = "Subprocess return code: 1"

    # Error handling around a race condition that produces "cb() never called" error. This
    # evinces itself as `cb_error_text` and it ought to disappear when we upgrade
    # npm to 3 or higher. TODO: clean this up when we do that.
    try:
        # The implementation of Paver's `sh` function returns before the forked
        # actually returns. Using a Popen object so that we can ensure that
        # the forked process has returned
        proc = subprocess.Popen(npm_command, stderr=npm_log_file)
        proc.wait()
    except BuildFailure, error_text:
        if cb_error_text in error_text:
            print("npm install error detected. Retrying...")
            proc = subprocess.Popen(npm_command, stderr=npm_log_file)
            proc.wait()
        else:
            raise BuildFailure(error_text)
    print(u"Successfully installed NPM packages. Log found at {}".format(
        npm_log_file_path
    ))


def python_prereqs_installation():
    """
    Installs Python prerequisites
    """
    for req_file in PYTHON_REQ_FILES:
        pip_install_req_file(req_file)


def pip_install_req_file(req_file):
    """Pip install the requirements file."""
    pip_cmd = 'pip install -q --disable-pip-version-check --exists-action w'
    sh(u"{pip_cmd} -r {req_file}".format(pip_cmd=pip_cmd, req_file=req_file))


@task
@timed
def install_node_prereqs():
    """
    Installs Node prerequisites
    """
    if no_prereq_install():
        print(NO_PREREQ_MESSAGE)
        return

    prereq_cache("Node prereqs", ["package.json"], node_prereqs_installation)


# To add a package to the uninstall list, just add it to this list! No need
# to touch any other part of this file.
PACKAGES_TO_UNINSTALL = [
    "South",                        # Because it interferes with Django 1.8 migrations.
    "edxval",                       # Because it was bork-installed somehow.
    "django-storages",
    "django-oauth2-provider",       # Because now it's called edx-django-oauth2-provider.
    "edx-oauth2-provider",          # Because it moved from github to pypi
    "i18n-tools",                   # Because now it's called edx-i18n-tools
]


@task
@timed
def uninstall_python_packages():
    """
    Uninstall Python packages that need explicit uninstallation.

    Some Python packages that we no longer want need to be explicitly
    uninstalled, notably, South.  Some other packages were once installed in
    ways that were resistant to being upgraded, like edxval.  Also uninstall
    them.
    """

    if no_python_uninstall():
        print(NO_PYTHON_UNINSTALL_MESSAGE)
        return

    # So that we don't constantly uninstall things, use a hash of the packages
    # to be uninstalled.  Check it, and skip this if we're up to date.
    hasher = hashlib.sha1()
    hasher.update(repr(PACKAGES_TO_UNINSTALL))
    expected_version = hasher.hexdigest()
    state_file_path = os.path.join(PREREQS_STATE_DIR, "Python_uninstall.sha1")
    create_prereqs_cache_dir()

    if os.path.isfile(state_file_path):
        with io.open(state_file_path) as state_file:
            version = state_file.read()
        if version == expected_version:
            print('Python uninstalls unchanged, skipping...')
            return

    # Run pip to find the packages we need to get rid of.  Believe it or not,
    # edx-val is installed in a way that it is present twice, so we have a loop
    # to really really get rid of it.
    for _ in range(3):
        uninstalled = False
        frozen = sh("pip freeze", capture=True)

        for package_name in PACKAGES_TO_UNINSTALL:
            if package_in_frozen(package_name, frozen):
                # Uninstall the pacakge
                sh(u"pip uninstall --disable-pip-version-check -y {}".format(package_name))
                uninstalled = True
        if not uninstalled:
            break
    else:
        # We tried three times and didn't manage to get rid of the pests.
        print("Couldn't uninstall unwanted Python packages!")
        return

    # Write our version.
    with io.open(state_file_path, "wb") as state_file:
        state_file.write(expected_version)


def package_in_frozen(package_name, frozen_output):
    """Is this package in the output of 'pip freeze'?"""
    # Look for either:
    #
    #   PACKAGE-NAME==
    #
    # or:
    #
    #   blah_blah#egg=package_name-version
    #
    pattern = r"(?mi)^{pkg}==|#egg={pkg_under}-".format(
        pkg=re.escape(package_name),
        pkg_under=re.escape(package_name.replace("-", "_")),
    )
    return bool(re.search(pattern, frozen_output))


@task
@timed
def install_coverage_prereqs():
    """ Install python prereqs for measuring coverage. """
    if no_prereq_install():
        print(NO_PREREQ_MESSAGE)
        return
    pip_install_req_file(COVERAGE_REQ_FILE)


@task
@timed
def install_python_prereqs():
    """
    Installs Python prerequisites.
    """
    if no_prereq_install():
        print(NO_PREREQ_MESSAGE)
        return

    uninstall_python_packages()

    # Include all of the requirements files in the fingerprint.
    files_to_fingerprint = list(PYTHON_REQ_FILES)

    # Also fingerprint the directories where packages get installed:
    # ("/edx/app/edxapp/venvs/edxapp/lib/python2.7/site-packages")
    files_to_fingerprint.append(sysconfig.get_python_lib())

    # In a virtualenv, "-e installs" get put in a src directory.
    src_dir = os.path.join(sys.prefix, "src")
    if os.path.isdir(src_dir):
        files_to_fingerprint.append(src_dir)

    # Also fingerprint this source file, so that if the logic for installations
    # changes, we will redo the installation.
    this_file = __file__
    if this_file.endswith(".pyc"):
        this_file = this_file[:-1]      # use the .py file instead of the .pyc
    files_to_fingerprint.append(this_file)

    prereq_cache("Python prereqs", files_to_fingerprint, python_prereqs_installation)


@task
@timed
def install_prereqs():
    """
    Installs Node and Python prerequisites
    """
    if no_prereq_install():
        print(NO_PREREQ_MESSAGE)
        return

    if not str2bool(os.environ.get('SKIP_NPM_INSTALL', 'False')):
        install_node_prereqs()
    install_python_prereqs()
    log_installed_python_prereqs()
    print_devstack_warning()


def log_installed_python_prereqs():
    """  Logs output of pip freeze for debugging. """
    sh("pip freeze > {}".format(Env.GEN_LOG_DIR + "/pip_freeze.log"))
    return


def print_devstack_warning():
    if Env.USING_DOCKER:  # pragma: no cover
        print("********************************************************************************")
        print("* WARNING: Mac users should run this from both the lms and studio shells")
        print("* in docker devstack to avoid startup errors that kill your CPU.")
        print("* For more details, see:")
        print("* https://github.com/edx/devstack#docker-is-using-lots-of-cpu-time-when-it-should-be-idle")
        print("********************************************************************************")
