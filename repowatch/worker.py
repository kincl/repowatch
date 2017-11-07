import os
import shutil
import logging
import threading

from .util import run_cmd, run_user_cmd

ONEYEAR = 365*24*60*60


class StopException(Exception):
    pass


class Worker(threading.Thread):
    """ Waits for queue events and does the checkout and management """

    def __init__(self, options, queue, ssh_wrapper, projects, ):
        self.options = options
        self.queue = queue
        self.ssh_wrapper = ssh_wrapper
        self.projects = projects
        self.logger = logging.getLogger('repowatch.worker')

        threading.Thread.__init__(self)

    def run(self):
        still_running = True
        self.logger.debug('Waiting for event')

        while still_running:
            try:
                self._do_handle_one_event()
            except StopException:
                self.logger.debug("Stopping")
                still_running = False

    def update_branch(self, project_name, branch_name, output_dir=None):
        ''' Do the actual branch update

            project_name: name of the repository project
            branch_name: name of the branch to checkout
            output_dir: directory to checkout branch into, defaults to branch_name

        '''
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
                run_cmd('git init', cwd=fullpath)
        else:
            # create branch dir
            os.makedirs(fullpath)
            run_cmd('git init', cwd=fullpath)

        run_cmd('git fetch '
                '--depth 1 '
                'ssh://{0}@{1}:{2}/{3} {4}'.format(self.options['username'],
                                                   self.options['hostname'],
                                                   self.options['port'],
                                                   project_name,
                                                   branch_name),
                ssh_key=self.options.get('key_filename', None),
                cwd=fullpath)

        run_cmd('git checkout -f FETCH_HEAD ', cwd=fullpath)

        # run user defined commands
        if cmds:
            project_dir = self.projects[project_name]['path']
            branch_dir = os.path.join(project_dir, branch_name)
            run_user_cmd(cmds, project_name, branch_name, project_dir, branch_dir)

    def delete_branch(self, project_name, branch_name):
        fullpath = '{0}/{1}'.format(self.projects[project_name]['path'], branch_name)
        if os.path.isdir(fullpath):
            self.logger.info('Delete repo/branch: %s:%s at %s',
                             project_name,
                             branch_name,
                             fullpath)
            shutil.rmtree(fullpath)

    def project_is_valid(self, project_name):
        return True if project_name in self.projects.keys() else False

    def _do_handle_one_event(self):
        ''' Handles an event off the queue '''
        event = self.queue.get(True, ONEYEAR)

        if event['type'] == 'shutdown':
            raise StopException

        if not self.project_is_valid(event['project_name']):
            self.logger.error('Not a valid project name: '.format(event['project_name']))
            return

        if event['type'] == 'update':
            del event['type']
            self.update_branch(**event)
        elif event['type'] == 'delete':
            # cleanup old branches on every event process
            self.cleanup_old_branches(event['project_name'])
            del event['type']
