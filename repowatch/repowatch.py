#!/usr/bin/env python
# -*- coding: utf-8 -*-
# vi:ts=4:softtabstop=4:shiftwidth=4

import ConfigParser
import argparse
import Queue
import json
import threading
import time

import os
import sys
import subprocess
import shutil
import yaml

import logging
import logging.handlers

from os.path import basename, dirname
from resource import getrlimit, RLIMIT_NOFILE

from BaseHTTPServer import BaseHTTPRequestHandler, HTTPServer
import cgi

from contextlib import contextmanager

import daemon
import daemon.pidlockfile
import lockfile

import tempfile
import stat

GIT_SSH_WRAPPER = '''#!/bin/sh

if [ -z "$PKEY" ]; then
    # if PKEY is not specified, run ssh using default keyfile
    ssh "$@"
else
    ssh -i "$PKEY" "$@"
fi
'''

class WatchGerrit(threading.Thread):
    """ Threaded job; listens for Gerrit events and puts them in a queue """

    def __init__(self, options, queue):
        import paramiko

        options['port'] = int(options['port']) # convert to int?
        if 'timeout' not in options:
            options['timeout'] = 60
        self.options = options
        self.queue = queue
        self.logger = logging.getLogger('RepoWatch.WatchGerrit')

        self.client = paramiko.SSHClient()
        self.client.load_system_host_keys()
        self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        threading.Thread.__init__(self)

    def get_extra(self, project):
        """ Fetch list of extra refs to check out.

            returns tuple (ref, change_$number)

        """
        extra_refs = []
        try:
            self.client.connect(**self.options)
            _, stdout, _ = self.client.exec_command('gerrit query "status:open project:{0}" --patch-sets --format json'.format(project))
            for line in stdout:
                # get
                data = json.loads(line)
                if data.get('status', None):
                    extra_refs.append([data['patchSets'][-1:][0]['ref'], 'change_{0}'.format(data['number'])])

        except Exception, e:
            logging.exception('get_extra error: {0}'.format(str(e)))
        finally:
            self.client.close()

        self.logger.debug('Adding extra Gerrit refs: {0}'.format(extra_refs))
        return extra_refs

    def run(self):
        while 1:

            try:
                self.client.connect(**self.options)
                self.client.get_transport().set_keepalive(60)
                _, stdout, _ = self.client.exec_command('gerrit stream-events')
                for line in stdout:
                    #self.queue.put(json.loads(line))
                    self.handle_event(json.loads(line))
            except Exception, e:
                logging.exception('WatchGerrit: error: {0}'.format(str(e)))
            finally:
                self.client.close()
            time.sleep(5)

    def handle_event(self, event):
        try:
            event_project = event['change']['project']
        except KeyError:
            event_project = event['refUpdate']['project']

        self.logger.debug('Gerrit event: {0}'.format(event))

        # for all patchsets and drafts we handle those as special
        if event['type'] in ['patchset-created',
                             'draft-published',
                             'change-restored',
                             'comment-added']:
            self.queue.put({'type': 'update',
                            'project_name': event['change']['project'],
                            'branch_name': event['patchSet']['ref'],
                            'output_dir': 'change_{0}'.format(basename(dirname(event['patchSet']['ref'])))})

        # need to remove the branch_name directory that was created, a change-merged
        # also triggers a ref-updated event
        if event['type'] in ['change-abandoned',
                             'change-merged']:
            self.queue.put({'type': 'delete',
                            'project_name': event['change']['project'],
                            'branch_name': 'change_{0}'.format(basename(dirname(event['patchSet']['ref'])))})

        # for ref updates, this needs to handle creating and deleting branch_namees and updating
        if event['type'] in ['ref-updated']:
            if event['refUpdate']['newRev'] == u'0000000000000000000000000000000000000000':
                self.queue.put({'type': 'delete',
                                'project_name': event['refUpdate']['project'],
                                'branch_name': event['refUpdate']['refName']})
            else:
                self.queue.put({'type': 'update',
                                'project_name': event['refUpdate']['project'],
                                'branch_name': event['refUpdate']['refName']})


class GitlabHTTPHandler(BaseHTTPRequestHandler):

    def do_POST(self):
        self.send_response(200)
        self.send_header('Content-type','text/html')
        self.end_headers()
        # Send the html message
        self.wfile.write("OK")
        data_string = self.rfile.read(int(self.headers['Content-Length']))
        self.handle_event(json.loads(data_string))
        return

    def handle_event(self, event):
        logger = logging.getLogger()
        logger.debug('Gitlab event: {0}'.format(event))

        if event['after'] == u'0000000000000000000000000000000000000000':
            self.server.queue.put({'type': 'delete',
                                   'project_name': event['repository']['url'].split(':')[1][:-4],
                                   'branch_name': basename(event['ref'])})
        else:
            self.server.queue.put({'type': 'update',
                                   'project_name': event['repository']['url'].split(':')[1][:-4],
                                   'branch_name': basename(event['ref'])})

    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type','text/html')
        self.end_headers()
        # Send the html message
        self.wfile.write("OK")
        return

    def log_message(self, format, *args):
        logger = logging.getLogger('RepoWatch.WatchGitlab.GitlabHTTPHandler')
        logger.info("%s - - [%s] %s" %
                                 (self.address_string(),
                                  self.log_date_time_string(),
                                  format%args))

class GitlabHTTPServer(HTTPServer):
    def __init__(self, server_address, RequestHandlerClass, queue):
        self.queue = queue
        HTTPServer.__init__(self, server_address, RequestHandlerClass)

class WatchGitlab(threading.Thread):
    """ Starts HTTP server and listens for requests """

    def __init__(self, options, queue):
        self.options = options
        self.queue = queue
        self.logger = logging.getLogger('RepoWatch.WatchGitlab')
        threading.Thread.__init__(self)

    def get_extra(self, project):
        """ Get open issues? """
        return []

    def run(self):
        port = 8000
        httpd = GitlabHTTPServer(('', port), GitlabHTTPHandler, self.queue)
        self.logger.info('Starting HTTP server on {0}'.format(port))
        try:
            httpd.serve_forever()
        except Exception, e:
            logging.exception('WatchGitlab: HTTP server exception: {0}'.format(str(e)))
        finally:
            httpd.socket.close()

@contextmanager
def FakeContext():
    """ This allows us to call with context: but not provide a DaemonContext """
    yield

class RepoWatch:
    """ Manages the threads that watch for events and acts on events that come in """

    def __init__(self, args):
        self.queue = Queue.Queue()
        self.args = args

    def setup(self):
        # Logging
        logging.basicConfig(level=logging.INFO)
        self.logger = logging.getLogger('RepoWatch')
        self.logger.setLevel(logging.INFO)

        if self.args.debug:
            self.logger.setLevel(logging.DEBUG)
        else:
            self.logger.setLevel(logging.INFO)
            self.syslog = logging.handlers.SysLogHandler("/dev/log")
            self.syslog.setFormatter(logging.Formatter("RepoWatch[%(process)s]: %(name)s: %(message)s"))

        if self.args.pidfile:
            self.logger.addHandler(self.syslog)

        # Config
        self.logger.info('Reading config')

        needed = dict(gerrit=False, gitlab=False)

        # read project config to determine what threads we need to start
        self.projects = {}

        try:
            project_yaml = open(self.args.projects)
        except IOError:
            self.logger.error('Could not find project yaml file at: {0}'.format(self.args.projects))
            raise Exception
        for p in yaml.safe_load(project_yaml):
            self.projects[p['project']] = p
            if p['type'] in needed:
                needed[p['type']] = True
            else:
                self.logger.error('Bad type for project {0}, must be one of {1}'.format(p['project'],
                                                                                        needed.keys()))
                sys.exit(1)

        config = ConfigParser.ConfigParser()
        self.options = dict()
        self.threads = dict()
        try:
            config_ini = open(self.args.config)
        except IOError:
            self.logger.error('Could not find config file at: {0}'.format(self.args.config))
            raise Exception
        config.readfp(config_ini)

        get_class = lambda x: globals()[x]

        for repo, need in needed.items():
            if need:
                _options = dict()
                try:
                    _options.update(config.items(repo))
                except ConfigParser.NoSectionError:
                    logging.exception('Unable to read {0} configuration? Does it exist?'.format(repo))
                    sys.exit(1)

                self.options[repo] = _options
                modu = get_class('Watch{0}'.format(repo.capitalize()))
                try:
                    self.threads[repo] = modu(_options, self.queue)
                except Exception as e:
                    self.logger.info('Error instantiating watcher: {0}'.format(e))
                self.threads[repo].daemon = True

        self.logger.info('Finished config')
        self.create_ssh_wrapper()

    def create_ssh_wrapper(self):
        file = tempfile.NamedTemporaryFile(prefix='tmp-GIT_SSH-wrapper-', delete=False)
        file.write(GIT_SSH_WRAPPER)
        file.close()
        os.chmod(file.name, stat.S_IRUSR | stat.S_IXUSR)
        self.logger.debug('Created SSH wrapper: {0}'.format(file.name))
        self.wrapper = file.name

    def cleanup_ssh_wrapper(self, wrapper):
        self.logger.debug('Cleaning SSH wrapper: {0}'.format(wrapper))
        try:
            os.unlink(wrapper)
        except Exception, e:
            logging.exception('Error cleaning SSH wrapper')

    def run_cmd(self, cmd, ssh_key=None, **kwargs):
        """ Run the command and return stdout """
        self.logger.debug('Running {0}'.format(cmd))

        # Ensure the GIT_SSH wrapper is present
        if not os.path.isfile(self.wrapper):
            self.create_ssh_wrapper()
        env_dict = dict(GIT_SSH=self.wrapper)
        if ssh_key:
            env_dict['PKEY'] = ssh_key

        p = subprocess.Popen(cmd.split(),
                             stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE,
                             env=env_dict,
                             **kwargs)
        out, err = p.communicate()
        if p.returncode != 0:
            self.logger.error("Nonzero return code\n" \
                              "Code: {0} Exec: {1}\n" \
                              "Output: {2} Error: {3}".format(p.returncode,
                                                               cmd,
                                                               repr(out),
                                                               repr(err)))
        else:
            return out

    def _initial_checkout(self):
        """ Look at all branches and check them out """
        self.logger.info('Doing initial checkout of branches')

        for project, data in self.projects.items():
            remote = self.run_cmd('git ls-remote --heads ' \
                                  'ssh://{0}@{1}:{2}/{3}.git'.format(self.options[data['type']]['username'],
                                                                     self.options[data['type']]['hostname'],
                                                                     self.options[data['type']]['port'],
                                                                     project),
                                   ssh_key = self.options[data['type']]['key_filename'])
            if remote:
                for branch in [h.split('\t')[1][11:] for h in remote.rstrip('\n').split('\n')]:
                    self.update_branch(project, branch)
                # check out extra branches like issues or changesets
                for ref, outdir in self.threads[data['type']].get_extra(project):
                    self.update_branch(project, ref, outdir)
            else:
                self.logger.warn('Did not find remote heads for {0}'.format(project))

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

        self.logger.info('Update repo branch: {0}:{1} in {2}'.format(project_name, branch_name, fullpath))

        if not os.path.isdir(fullpath):
            # create branch dir
            os.makedirs(fullpath)
            self.run_cmd('git init', cwd=fullpath)

        self.run_cmd('git fetch ' \
                     'ssh://{0}@{1}:{2}/{3} {4}'.format(self.options[project_type]['username'],
                                                        self.options[project_type]['hostname'],
                                                        self.options[project_type]['port'],
                                                        project_name,
                                                        branch_name),
                     ssh_key = self.options[project_type]['key_filename'],
                     cwd=fullpath)

        self.run_cmd('git checkout -f FETCH_HEAD ',
                     cwd=fullpath)

        # set perms
        self.run_cmd('find {0} ' \
                     '-type f ' \
                     '-not -path *.git* ' \
                     '-exec chmod 644 {{}} ;'.format(dirname(fullpath)))

        self.run_cmd('find {0} ' \
                     '-type d ' \
                     '-not -path *.git* ' \
                     '-exec chmod 755 {{}} ;'.format(dirname(fullpath)))


    def delete_branch(self, project_name, branch_name):
        fullpath = '{0}/{1}'.format(self.projects[project_name]['path'], branch_name)
        self.logger.info('Delete repo/branch: {0}:{1} at {2}'.format(project_name, branch_name, fullpath))
        shutil.rmtree(fullpath)

    def project_is_valid(self, project_name):
        return True if project_name in self.projects.keys() else False

    def _do_handle_one_event(self):
        """ Handles an event off the queue """

        ONEYEAR = 365*24*60*60
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
        wanted=[]
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
        return [ fd for fd in xrange(fd_max) if fd_wanted(fd) ]

    def run(self):
        """Run."""

        if self.args.pidfile:
            pidfile = daemon.pidlockfile.TimeoutPIDLockFile(self.args.pidfile, acquire_timeout=2)
            context = daemon.DaemonContext(pidfile=pidfile)
            # because of https://github.com/paramiko/paramiko/issues/59
            context.files_preserve = self.files_preserve_by_path('/dev/urandom')
        else:
            context = FakeContext()

        try:
            self.setup()

            if self.args.pidfile:
                # try and see if we can since it seems that the context doesn't throw a exception
                pidfile.acquire()
                pidfile.release()
                self.logger.info('Attempting to daemonize')
            else:
                self.logger.info('Running in foreground')

            with context:
                self._initial_checkout()

                for name, thread in self.threads.items():
                    thread.start()
                self.main_loop()
        except lockfile.LockTimeout:
            logging.exception('Lockfile timeout while attempting to acquire lock, '
                              'are we already running?')
        finally:
            self.logger.info('Shutting down')
            try:
                self.cleanup_ssh_wrapper(self.wrapper)
            except:
                self.logger.info('No SSH wrapper to clean?')
            for name, thread in self.threads.items():
                thread.join(2)
            sys.exit(0)

def main():
    parser = argparse.ArgumentParser(description='Watch Gerrit/GitLab and checkout branches')
    parser.add_argument('-C', dest='config', action='store',
                        help='Path to repowatch.conf file', required=True)
    parser.add_argument('-P', dest='projects', action='store',
                        help='Path to projects.yaml file', required=True)
    parser.add_argument('-D', dest='pidfile', action='store', default=False,
                        help='Path to pidfile')
    parser.add_argument('--debug', dest='debug', action='store_true', default=False,
                        help='Debug mode')

    watcher = RepoWatch(parser.parse_args())
    watcher.run()

if __name__ == '__main__':
    main()
