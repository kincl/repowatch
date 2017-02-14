RepoWatch
=========

Takes Gerrit events or GitLab Web Hooks and updates the correct branch of a puppet checkout

Requirements
------------

`yum install git python-argparse PyYAML python-daemon python-paramiko`

Installation/Configuration
--------------------------
Configuration is done with two files:

repowatch.conf:
```dosini
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
```

projects.yaml:
```yaml
---
- project: test-project
  type: gerrit
  path: /tmp/test-project-2
  cmds:
    - echo "%{branch} %{branchdir}"
    - echo "%{project} %{projectdir}"
- project: testuser/test-project
  type: gitlab
  path: /tmp/test-project
```

User specified commands run after checkout.

TODO
----
 - test git binary installed

Credits
-------
Gerrit watcher code is based on https://github.com/atdt/gerrit-stream

Apache license
