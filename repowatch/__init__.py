#!/usr/bin/env python
# -*- coding: utf-8 -*-
# vi:ts=4:softtabstop=4:shiftwidth=4

import os
import sys
import traceback
import logging
import logging.handlers
import Queue
from threading import Lock
import ConfigParser
from multiprocessing import cpu_count
from time import sleep
from resource import getrlimit, RLIMIT_NOFILE
from contextlib import contextmanager

import daemon
import lockfile
try:
    from lockfile import pidlockfile
except ImportError:
    from daemon import pidlockfile

import yaml

from .gitlab import WatchGitlab
from .gerrit import WatchGerrit
from .worker import Worker
from .util import create_ssh_wrapper, cleanup_ssh_wrapper, run_cmd, get_remote_branches

# be careful not to exceed the ssh MaxStartups threshold (default 10)
DEFAULT_THREADS = cpu_count() * 2 if (cpu_count() * 2) < 10 else 10


@contextmanager
def FakeContext():
    '''
     This allows us to call with context: but not provide a DaemonContext
    '''
    yield


def get_class(classname):
    '''
    Gets a class that is in the global scope by looking up the name
    '''
    return globals()[classname]


class NoLock(object):
    '''
    This sets up a object that looks like a lock but doesn't act like one
    '''
    def __enter__(self):
        pass

    def __exit__(self, type, value, traceback):
        pass


class RepoWatch(object):
    '''
    Manages the threads that watch for events and acts on events that come in
    '''

    def __init__(self, config_file, project_file, pid_file, syslog, debug, only_once=False):
        self.queue = Queue.Queue()

        # read project config to determine what threads we need to start
        self.projects = {}
        self.options = dict()
        self.threads = dict()
        self.wrapper = None
        self.only_once = only_once

        self.worker_threads = 0

        self.project_file = project_file
        self.config_file = config_file
        self.pid_file = pid_file

        # Logging
        logging.basicConfig(
            level=logging.INFO, format='%(asctime)s %(threadName)s:%(name)s:%(levelname)s:%(message)s')
        self.logger = logging.getLogger('repowatch')
        self.logger.setLevel(logging.INFO)

        if debug:
            self.logger.setLevel(logging.DEBUG)
        else:
            self.logger.setLevel(logging.INFO)

        if (os.path.lexists('/dev/log') and
                (pid_file or
                 syslog)):
            self.syslog = logging.handlers.SysLogHandler("/dev/log")
            self.syslog.setFormatter(logging.Formatter(
                "RepoWatch[%(process)s]: %(threadName)s:%(name)s: %(message)s"))
            self.logger.addHandler(self.syslog)

    def setup(self):
        # Config
        self.logger.info('Reading config')
        needed = dict(gerrit=False, gitlab=False)
        try:
            project_yaml = open(self.project_file)
        except IOError:
            self.logger.error(
                'Could not find project yaml file at: %s', self.project_file)
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
            config_ini = open(self.config_file)
        except IOError:
            self.logger.error(
                'Could not find config file at: %s', self.config_file)
            raise Exception
        config.readfp(config_ini)

        self.wrapper = create_ssh_wrapper()

        for repo, need in needed.items():
            if need:
                _options = dict()
                try:
                    _options.update(config.items(repo))
                except ConfigParser.NoSectionError:
                    logging.exception(
                        'Unable to read %s configuration? Does it exist?', repo)
                    sys.exit(1)

                self.options[repo] = _options
                modul = get_class('Watch{0}'.format(repo.capitalize()))
                try:
                    self.threads[repo] = modul(_options, self.queue)
                except Exception as e:
                    self.logger.info('Error instantiating watcher: %s', e)
                self.threads[repo].daemon = True

                if _options.get('sequential_project_commands', False):
                    lock = Lock()
                else:
                    lock = NoLock()

                num_threads = int(_options.get('threads', DEFAULT_THREADS))
                self.worker_threads += num_threads
                self.logger.info('Starting {0} worker threads'.format(num_threads))
                for i in range(0, num_threads):
                    thread_name = '{0}-worker-{1}'.format(repo, i)
                    self.threads[thread_name] = Worker(
                        _options, self.queue, self.wrapper, self.projects, lock)
                    self.threads[thread_name].daemon = True

        self.logger.info('Finished config')

    def _initial_checkout(self):
        ''' Look at all branches and check them out '''
        self.logger.info('Doing initial checkout of branches')

        for project, data in self.projects.items():
            self.logger.info('Checking that ssh host key is known')
            hostname = self.options[data['type']]['hostname']

            known_host_files = [
                os.path.join(os.path.expanduser('~'), '.ssh/known_hosts'),
                '/etc/ssh/ssh_known_hosts']

            for known_host_file in known_host_files:
                known = run_cmd('ssh-keygen -F {0} -f {1}'.format(hostname, known_host_file),
                                wrapper=self.wrapper)
                if known:
                    break

            if known is False:
                self.logger.error('SSH host key not known! Exiting!')
                raise Exception  # TODO: need more specific Exception here!

            remote = run_cmd('git ls-remote --heads '
                             'ssh://{0}@{1}:{2}/{3}.git'.format(self.options[data['type']]['username'],
                                                                self.options[data['type']]['hostname'],
                                                                self.options[data['type']]['port'],
                                                                project),
                             wrapper=self.wrapper,
                             ssh_key=self.options[data['type']].get('key_filename', None))
            if remote:
                for branch in get_remote_branches(remote):
                    self.logger.debug(
                        'Adding project branch to queue: {0}:{1}'.format(project, branch))
                    self.queue.put({'type': 'update',
                                    'project_name': project,
                                    'branch_name': branch})

                # also delete those pesky old branches
                self.queue.put({'type': 'delete',
                                'project_name': project})
                # check out extra branches like issues or changesets TODO
                # for ref, outdir in self.threads[data['type']].get_extra(project):
                #     self.update_branch(project, ref, outdir)
            else:
                self.logger.warn('Did not find remote heads for %s', project)

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
        '''Run.'''

        if self.pid_file:
            pidfile = pidlockfile.PIDLockFile(self.pid_file)
            context = daemon.DaemonContext(pidfile=pidfile)
            # because of https://github.com/paramiko/paramiko/issues/59
            context.files_preserve = self.files_preserve_by_path('/dev/urandom')
        else:
            context = FakeContext()

        try:
            self.setup()

            if self.pid_file:
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

                while True:
                    try:
                        if self.only_once:
                            raise KeyboardInterrupt
                        sleep(60)
                    except KeyboardInterrupt:
                        for i in range(0, self.worker_threads):
                            self.queue.put({'type': 'shutdown'})
                        break

        except lockfile.LockTimeout:
            logging.error('Lockfile timeout while attempting to acquire lock, '
                          'are we already running?')
        except Exception as e:
            self.logger.error(traceback.format_exception(*sys.exc_info()))
        finally:
            self.logger.info('Shutting down')
            try:
                cleanup_ssh_wrapper(self.wrapper)
            except Exception:
                self.logger.info('No SSH wrapper to clean?')
            for _, thread in self.threads.items():
                if thread.is_alive():
                    thread.running = False
                    self.logger.debug('waiting for {0}'.format(thread))
                    thread.join(5)
            sys.exit(0)
