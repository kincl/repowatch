import repowatch


CONFIG_CONF = '''
[gerrit]
username = exampleuser
hostname = gerrit.example.com
port = 29418
key_filename = /home/exampleuser/.ssh/id_rsa

[gitlab]
username = git
hostname = gitlab.example.com
port = 22
key_filename = /home/exampleuser/.ssh/id_rsa
'''

PROJECT_YAML = '''
- project: test-project
  type: gerrit
  path: /tmp/test-project-2
- project: testuser/test-project
  type: gitlab
  path: /tmp/test-project
'''


def test_simple_import(tmpdir):
    cfg = tmpdir.join('config_test.conf')
    cfg.write(CONFIG_CONF)

    proj = tmpdir.join('project_test.yaml')
    proj.write(PROJECT_YAML)

    rw = repowatch.RepoWatch(str(cfg),
                             str(proj),
                             False,
                             False,
                             True)
    rw.setup()
