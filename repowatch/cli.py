import argparse

from . import RepoWatch

def main():
    parser = argparse.ArgumentParser(description='Watch Gerrit/GitLab and checkout branches')
    parser.add_argument('-C', dest='config', action='store',
                        help='Path to repowatch.conf file', required=True)
    parser.add_argument('-P', dest='projects', action='store',
                        help='Path to projects.yaml file', required=True)
    parser.add_argument('-D', dest='pidfile', action='store', default=False,
                        help='Path to pidfile')
    parser.add_argument('--syslog', dest='syslog', action='store_true', default=False,
                        help='log to syslog')
    parser.add_argument('--debug', dest='debug', action='store_true', default=False,
                        help='Debug mode')

    watcher = RepoWatch(parser.parse_args())
    watcher.run()

if __name__ == '__main__':
    main()
