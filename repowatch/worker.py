import os
import shutil
import logging
import threading
from Queue import Empty

from .util import run_cmd, run_user_cmd, get_remote_branches

ONEYEAR = 365*24*60*60


class StopException(Exception):
    pass


class Worker(threading.Thread):
    """ Waits for queue events and does the checkout and management """

    def __init__(self, options, queue, ssh_wrapper, projects, ):
        self.options = options
        self.queue = queue
        self.wrapper = ssh_wrapper
        self.projects = projects
        self.logger = logging.getLogger('repowatch.worker')

        self.running = True

        threading.Thread.__init__(self)

    def run(self):
        self.logger.debug('Waiting for event')

        while self.running:
            try:
                self._do_handle_one_event()
            except Empty:
                pass
            except StopException:
                self.logger.debug("Stopping")
                self.running = False

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
                run_cmd('git init', wrapper=self.wrapper, cwd=fullpath)
        else:
            # create branch dir
            os.makedirs(fullpath)
            run_cmd('git init', wrapper=self.wrapper, cwd=fullpath)

        run_cmd('git fetch '
                '--depth 1 '
                'ssh://{0}@{1}:{2}/{3} {4}'.format(self.options['username'],
                                                   self.options['hostname'],
                                                   self.options['port'],
                                                   project_name,
                                                   branch_name),
                wrapper=self.wrapper,
                ssh_key=self.options.get('key_filename', None),
                cwd=fullpath)

        run_cmd('git checkout -f FETCH_HEAD ', wrapper=self.wrapper, cwd=fullpath)

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

    def cleanup_old_branches(self, project_name):
        """ delete local branches which don't exist upstream """
        self.logger.info(
            'Cleaning up local branches on project {0}'.format(project_name))

        data = self.projects[project_name]
        remote = run_cmd('git ls-remote --heads '
                         'ssh://{0}@{1}:{2}/{3}.git'.format(self.options['username'],
                                                            self.options['hostname'],
                                                            self.options['port'],
                                                            project_name),
                         wrapper=self.wrapper,
                         ssh_key=self.options.get('key_filename', None))
        if remote:
            project_path = data['path']
            remote_branches = get_remote_branches(remote)
            local_branches = [name for name in os.listdir(project_path)
                              if os.path.isdir(os.path.join(project_path, name))]
            for branch in local_branches:
                if branch not in (remote_branches):
                    self.delete_branch(project_name, branch)
        else:
            self.logger.warn(
                'Did not find remote heads for {0}'.format(project_name))

    def project_is_valid(self, project_name):
        return True if project_name in self.projects.keys() else False

    def _do_handle_one_event(self):
        ''' Handles an event off the queue '''
        event = self.queue.get(True, 2)

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
