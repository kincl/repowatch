import os
import tempfile
import subprocess
import stat
import logging

LOG = logging.getLogger('repowatch.util')

GIT_SSH_WRAPPER = '''#!/bin/sh

if [ -z "$PKEY" ]; then
    # if PKEY is not specified, run ssh using default keyfile
    ssh "$@"
else
    ssh -oStrictHostKeyChecking=no -i "$PKEY" "$@"
fi
'''


def run_cmd(cmd, wrapper, ssh_key=None, **kwargs):
    ''' Run the command and return stdout '''
    LOG.debug('Running {0}'.format(cmd))

    env_dict = os.environ.copy()

    # Ensure the GIT_SSH wrapper is present
    if wrapper is not None and os.path.isfile(wrapper):
        env_dict['GIT_SSH'] = wrapper

    if ssh_key:
        env_dict['PKEY'] = ssh_key

    p = subprocess.Popen(cmd.split(),
                         stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT,
                         env=env_dict,
                         **kwargs)
    out, _ = p.communicate()
    out = out.strip()
    if p.returncode != 0:
        LOG.error("Nonzero return code. "
                  "Code %s, Exec: %s, "
                  "Output: %s",
                  p.returncode,
                  repr(cmd),
                  repr(out))
        return False
    return out


def run_user_cmd(cmds, project_name, branch_name, project_dir, branch_dir):
    '''
    Allows specifying of commands in config for project
    to run after project is created or updated
    '''

    varmap = {
        '%{branch}': branch_name,
        '%{project}': project_name,
        '%{branchdir}': branch_dir,
        '%{projectdir}': project_dir
    }

    # replace variables with real values
    cmds = [reduce(lambda x, y: x.replace(y, varmap[y]), varmap, s)
            for s in cmds]

    # run commands
    for command in cmds:
        run_cmd(command, wrapper=None, cwd=branch_dir)


def create_ssh_wrapper():
    '''
    Returns file name of location of SSH wrapper
    '''
    with tempfile.NamedTemporaryFile(prefix='tmp-GIT_SSH-wrapper-', delete=False) as fh:
        fh.write(GIT_SSH_WRAPPER)
        os.chmod(fh.name, stat.S_IRUSR | stat.S_IXUSR)
        # self.logger.debug('Created SSH wrapper: %s', fh.name)
        return fh.name


def cleanup_ssh_wrapper(wrapper):
    # self.logger.debug('Cleaning SSH wrapper: %s', wrapper)
    try:
        os.unlink(wrapper)
    except Exception as e:
        logging.exception('Error cleaning SSH wrapper')
