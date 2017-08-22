import json
import logging
import threading
from os.path import basename

try:
    from BaseHTTPServer import BaseHTTPRequestHandler, HTTPServer
except ImportError:
    from http.server import BaseHTTPRequestHandler, HTTPServer


class GitlabHTTPHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/html')
        self.end_headers()
        # Send the html message
        self.wfile.write("OK")
        data_string = self.rfile.read(int(self.headers['Content-Length']))
        self.handle_event(json.loads(data_string))
        return

    def handle_event(self, event):
        logger = logging.getLogger()
        logger.debug('Gitlab event: %s', event)

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
        self.send_header('Content-type', 'text/html')
        self.end_headers()
        # Send the html message
        self.wfile.write("OK")
        return

    def log_message(self, fmt, *args):
        logger = logging.getLogger('repowatch.gitlab')
        logger.info("%s - - [%s] %s",
                    self.address_string(),
                    self.log_date_time_string(),
                    fmt % args)


class GitlabHTTPServer(HTTPServer):
    def __init__(self, server_address, RequestHandlerClass, queue):
        self.queue = queue
        HTTPServer.__init__(self, server_address, RequestHandlerClass)


class WatchGitlab(threading.Thread):
    """ Starts HTTP server and listens for requests """

    def __init__(self, options, queue):
        self.options = options
        self.queue = queue
        self.logger = logging.getLogger('repowatch.gitlab')
        threading.Thread.__init__(self)

    def get_extra(self, _):
        """ Get open issues? """
        return []

    def run(self):
        port = 8000
        httpd = GitlabHTTPServer(('', port), GitlabHTTPHandler, self.queue)
        self.logger.info('Starting HTTP server on %s', port)
        try:
            httpd.serve_forever()
        except Exception as e:
            logging.exception('WatchGitlab: HTTP server exception: %s', str(e))
        finally:
            httpd.socket.close()
