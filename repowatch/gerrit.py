import time
import json
import logging
import threading
from os.path import basename, dirname

import paramiko


class WatchGerrit(threading.Thread):
    """ Threaded job; listens for Gerrit events and puts them in a queue """

    def __init__(self, options, queue):
        options['port'] = int(options['port'])  # convert to int?
        if 'timeout' not in options:
            options['timeout'] = 60
        self.options = options
        self.queue = queue
        self.logger = logging.getLogger('repowatch.gerrit')

        self.running = True

        threading.Thread.__init__(self)

    def get_extra(self, project):
        """ Fetch list of extra refs to check out.

            returns tuple (ref, change_$number)
        """
        extra_refs = []
        try:
            client = paramiko.SSHClient()
            client.load_system_host_keys()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            client.connect(**self.options)
            _, stdout, _ = client.exec_command('gerrit query "status:open project:{0}" '
                                               '--patch-sets --format json'.format(project))
            for line in stdout:
                # get
                data = json.loads(line)
                if data.get('status', None):
                    extra_refs.append([data['patchSets'][-1:][0]['ref'],
                                       'change_{0}'.format(data['number'])])

        except Exception as e:
            self.logger.exception('get_extra error: %s', str(e))
        finally:
            client.close()

        self.logger.debug('Adding extra Gerrit refs: %s', extra_refs)
        return extra_refs

    def run(self):
        while self.running:

            try:
                client = paramiko.SSHClient()
                client.load_system_host_keys()
                client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                client.connect(**self.options)
                client.get_transport().set_keepalive(60)
                _, stdout, _ = client.exec_command('gerrit stream-events')
                for line in stdout:
                    # self.queue.put(json.loads(line))
                    self.handle_event(json.loads(line))
            except Exception as e:
                logging.exception('WatchGerrit: error: %s', str(e))
            finally:
                client.close()
            time.sleep(5)

    def handle_event(self, event):
        try:
            event_project = event['change']['project']
        except KeyError:
            event_project = event['refUpdate']['project']

        self.logger.debug('Gerrit event: %s', event)

        # for all patchsets and drafts we handle those as special
        if event['type'] in ['patchset-created',
                             'draft-published',
                             'change-restored']:
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
