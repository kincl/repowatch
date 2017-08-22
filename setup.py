from setuptools import setup

deps = ['PyYAML',
        'daemon',
        'lockfile',
        'paramiko',]

setup(name='repowatch',
      version='1.0',
      description='Watches Gerrit and GitLab and checks out git repo updates',
      url='https://github.com/kincl/repowatch',
      author='Jason Kincl',
      author_email='jkincl@gmail.gov',
      license='MIT',
      packages=['repowatch'],
      install_requires=deps,
      tests_require=deps,
      entry_points={'console_scripts': ['repowatch=repowatch.cli:main']})
