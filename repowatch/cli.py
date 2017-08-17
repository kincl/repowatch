import argparse

from . import RepoWatch

def cli():
    parser = argparse.ArgumentParser(description='Watch Gerrit/GitLab and checkout branches')
    parser.add_argument('-C', dest='config_file', action='store',
                        help='Path to repowatch.conf file', required=True)
    parser.add_argument('-P', dest='project_file', action='store',
                        help='Path to projects.yaml file', required=True)
    parser.add_argument('-D', dest='pid_file', action='store', default=False,
                        help='Path to pidfile')
    parser.add_argument('--syslog', dest='syslog', action='store_true', default=False,
                        help='log to syslog')
    parser.add_argument('--debug', dest='debug', action='store_true', default=False,
                        help='Debug mode')

    args = parser.parse_args()
    watcher = RepoWatch(args.config, args.project, args.pid_file, args.syslog, args.debug)
    watcher.run()

if __name__ == '__main__':
    cli()
