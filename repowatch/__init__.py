#!/usr/bin/env python
# -*- coding: utf-8 -*-
# vi:ts=4:softtabstop=4:shiftwidth=4

import ConfigParser
import Queue

import os
import sys
import subprocess
import shutil

import logging
import logging.handlers

from resource import getrlimit, RLIMIT_NOFILE

from contextlib import contextmanager
import tempfile
import stat

import daemon
import lockfile
try:
    from lockfile import pidlockfile
except ImportError:
    from daemon import pidlockfile

import yaml

ONEYEAR = 365*24*60*60
GIT_SSH_WRAPPER = '''#!/bin/sh

if [ -z "$PKEY" ]; then
    # if PKEY is not specified, run ssh using default keyfile
    ssh "$@"
else
    ssh -i "$PKEY" "$@"
fi
'''


@contextmanager
def FakeContext():
    """ This allows us to call with context: but not provide a DaemonContext """
    yield

class RepoWatch:
    """ Manages the threads that watch for events and acts on events that come in """

    def __init__(self, args):
        self.queue = Queue.Queue()
        self.args = args

        # read project config to determine what threads we need to start
        self.projects = {}
        self.options = dict()
        self.threads = dict()
        self.wrapper = None

        # Logging
        logging.basicConfig(level=logging.INFO)
        self.logger = logging.getLogger('repowatch')
        self.logger.setLevel(logging.INFO)

        if self.args.debug:
            self.logger.setLevel(logging.DEBUG)
        else:
            self.logger.setLevel(logging.INFO)

        if (os.path.lexists('/dev/log') and
                (self.args.pidfile or
                 self.args.syslog)):
            self.syslog = logging.handlers.SysLogHandler("/dev/log")
            self.syslog.setFormatter(logging.Formatter("RepoWatch[%(process)s]: %(name)s: %(message)s"))
            self.logger.addHandler(self.syslog)

    def setup(self):
        # Config
        self.logger.info('Reading config')
        needed = dict(gerrit=False, gitlab=False)
        try:
            project_yaml = open(self.args.projects)
        except IOError:
            self.logger.error('Could not find project yaml file at: %s', self.args.projects)
            raise Exception
        for p in yaml.safe_load(project_yaml):
            self.projects[p['project']] = p
            if p['type'] in needed:
                needed[p['type']] = True
            else:
                self.logger.error('Bad type for project %s, must be one of %s',
                                  p['project'],
                                  needed.keys())
                sys.exit(1)

        config = ConfigParser.ConfigParser()
        try:
            config_ini = open(self.args.config)
        except IOError:
            self.logger.error('Could not find config file at: %s', self.args.config)
            raise Exception
        config.readfp(config_ini)

        get_class = lambda x: globals()[x]

        for repo, need in needed.items():
            if need:
                _options = dict()
                try:
                    _options.update(config.items(repo))
                except ConfigParser.NoSectionError:
                    logging.exception('Unable to read %s configuration? Does it exist?', repo)
                    sys.exit(1)

                self.options[repo] = _options
                modu = get_class('Watch{0}'.format(repo.capitalize()))
                try:
                    self.threads[repo] = modu(_options, self.queue)
                except Exception as e:
                    self.logger.info('Error instantiating watcher: %s', e)
                self.threads[repo].daemon = True

        self.logger.info('Finished config')
        self.create_ssh_wrapper()

    def create_ssh_wrapper(self):
        with tempfile.NamedTemporaryFile(prefix='tmp-GIT_SSH-wrapper-', delete=False) as fh:
            fh.write(GIT_SSH_WRAPPER)
            os.chmod(fh.name, stat.S_IRUSR | stat.S_IXUSR)
            self.logger.debug('Created SSH wrapper: %s', fh.name)
            self.wrapper = fh.name

    def cleanup_ssh_wrapper(self, wrapper):
        self.logger.debug('Cleaning SSH wrapper: %s', wrapper)
        try:
            os.unlink(wrapper)
        except Exception as e:
            logging.exception('Error cleaning SSH wrapper')

    def run_cmd(self, cmd, ssh_key=None, **kwargs):
        """ Run the command and return stdout """
        self.logger.debug('Running %s', cmd)

        # Ensure the GIT_SSH wrapper is present
        if not os.path.isfile(self.wrapper):
            self.create_ssh_wrapper()

        env_dict = os.environ.copy()
        env_dict['GIT_SSH'] = self.wrapper
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
            self.logger.error("Nonzero return code. "\
                              "Code %s, Exec: %s, "\
                              "Output: %s",
                              p.returncode,
                              repr(cmd),
                              repr(out))
            return False
        return out

    def run_user_cmd(self, cmds, project_name, branch_name):
        """
        Allows specifying of commands in config for project
        to run after project is created or updated
        """
        project_dir = self.projects[project_name]['path']
        branch_dir = os.path.join(project_dir, branch_name)
        varmap = {
            '%{branch}': branch_name,
            '%{project}': project_name,
            '%{branchdir}': branch_dir,
            '%{projectdir}': project_dir
        }

        # replace variables with real values
        cmds = [reduce(lambda x, y: x.replace(y, varmap[y]), varmap, s) for s in cmds]

        # run commands
        for command in cmds:
            self.run_cmd(command, cwd=branch_dir)

    def _initial_checkout(self):
        """ Look at all branches and check them out """
        self.logger.info('Doing initial checkout of branches')

        for project, data in self.projects.items():
            self.logger.info('Checking that ssh host key is known')
            known = self.run_cmd('ssh-keygen -F {0}'.format(self.options[data['type']]['hostname']))
            if known is False:
                self.logger.error('SSH host key not known! Exiting!')
                raise Exception # TODO: need more specific Exception here!

            remote = self.run_cmd('git ls-remote --heads ' \
                                  'ssh://{0}@{1}:{2}/{3}.git'.format(self.options[data['type']]['username'],
                                                                     self.options[data['type']]['hostname'],
                                                                     self.options[data['type']]['port'],
                                                                     project),
                                  ssh_key=self.options[data['type']]['key_filename'])
            if remote:
                for remote_head_str in remote.rstrip('\n').split('\n'):
                    try:
                        branch = remote_head_str.split('\t')[1][11:]
                        self.update_branch(project, branch)
                    except IndexError:
                        self.logger.debug('Bad remote head: %s', remote_head_str)
                # check out extra branches like issues or changesets
                for ref, outdir in self.threads[data['type']].get_extra(project):
                    self.update_branch(project, ref, outdir)
            else:
                self.logger.warn('Did not find remote heads for %s', project)

    def update_branch(self, project_name, branch_name, output_dir=None):
        """ Do the actual branch update

            project_name: name of the repository project
            branch_name: name of the branch to checkout
            output_dir: directory to checkout branch into, defaults to branch_name

        """
        if output_dir is None:
            output_dir = branch_name
        fullpath = self.projects[project_name]['path']+'/'+output_dir
        project_type = self.projects[project_name]['type']

        try:
            cmds = self.projects[project_name]['cmds']
        except KeyError:
            cmds = None

        self.logger.info('Update repo branch: %s:%s in %s',
                         project_name,
                         branch_name,
                         fullpath)

        if os.path.isdir(fullpath):
            if not os.path.isdir(os.path.join(fullpath, '.git')):
                self.run_cmd('git init', cwd=fullpath)
        else:
            # create branch dir
            os.makedirs(fullpath)
            self.run_cmd('git init', cwd=fullpath)

        self.run_cmd('git fetch ' \
                     '--depth 1 ' \
                     'ssh://{0}@{1}:{2}/{3} {4}'.format(self.options[project_type]['username'],
                                                        self.options[project_type]['hostname'],
                                                        self.options[project_type]['port'],
                                                        project_name,
                                                        branch_name),
                     ssh_key=self.options[project_type]['key_filename'],
                     cwd=fullpath)

        self.run_cmd('git checkout -f FETCH_HEAD ',
                     cwd=fullpath)

        # run user defined commands
        if cmds:
            self.run_user_cmd(cmds, project_name, output_dir)

    def delete_branch(self, project_name, branch_name):
        fullpath = '{0}/{1}'.format(self.projects[project_name]['path'], branch_name)
        self.logger.info('Delete repo/branch: %s:%s at %s',
                         project_name,
                         branch_name,
                         fullpath)
        shutil.rmtree(fullpath)

    def project_is_valid(self, project_name):
        return True if project_name in self.projects.keys() else False

    def _do_handle_one_event(self):
        """ Handles an event off the queue """
        self.logger.debug('Waiting for event')
        event = self.queue.get(True, ONEYEAR)

        if not self.project_is_valid(event['project_name']):
            return

        if event['type'] == 'update':
            del event['type']
            self.update_branch(**event)
        elif event['type'] == 'delete':
            del event['type']
            self.delete_branch(**event)

    def main_loop(self):
        """Does the looping and handling events."""

        still_running = True
        while still_running:
            try:
                self._do_handle_one_event()
            except KeyboardInterrupt:
                still_running = False

    @staticmethod
    def files_preserve_by_path(*paths):
        wanted = []
        for path in paths:
            fd = os.open(path, os.O_RDONLY)
            try:
                wanted.append(os.fstat(fd)[1:3])
            finally:
                os.close(fd)

        def fd_wanted(fd):
            try:
                return os.fstat(fd)[1:3] in wanted
            except OSError:
                return False

        fd_max = getrlimit(RLIMIT_NOFILE)[1]
        return [fd for fd in xrange(fd_max) if fd_wanted(fd)]

    def run(self):
        """Run."""

        if self.args.pidfile:
            pidfile = pidlockfile.PIDLockFile(self.args.pidfile)
            context = daemon.DaemonContext(pidfile=pidfile)
            # because of https://github.com/paramiko/paramiko/issues/59
            context.files_preserve = self.files_preserve_by_path('/dev/urandom')
        else:
            context = FakeContext()

        try:
            self.setup()

            if self.args.pidfile:
                # try and see if we can since it seems that the context doesn't throw a exception
                pidfile.acquire(timeout=2)
                pidfile.release()
                self.logger.info('Attempting to daemonize')
            else:
                self.logger.info('Running in foreground')

            with context:
                self._initial_checkout()

                for _, thread in self.threads.items():
                    thread.start()
                self.main_loop()
        except lockfile.LockTimeout:
            logging.error('Lockfile timeout while attempting to acquire lock, '
                          'are we already running?')
        finally:
            self.logger.info('Shutting down')
            try:
                self.cleanup_ssh_wrapper(self.wrapper)
            except:
                self.logger.info('No SSH wrapper to clean?')
            for _, thread in self.threads.items():
                if thread.is_alive():
                    thread.join(2)
            sys.exit(0)
