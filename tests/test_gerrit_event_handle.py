import json
import logging
import argparse
from repowatch import repowatch

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger()
#logger.addHandler(logging.StreamHandler())

def noop(*args):
    pass

def put_and_handle_event(line):
    parser = argparse.ArgumentParser()
    args = parser.parse_args()
    watcher = repowatch.RepoWatch(args)

    watcher.projects = [{'project': 'test-project',
                         'path':'test',
                         'type': 'gerrit'},
                       ]

    watcher.update_branch = noop
    watcher.delete_branch = noop
    watcher.logger = logger
    # in prod this doesn't happen but my test cases have unescaped newlines
    watcher.queue.put(json.loads(line.replace('\n', '\\n')))
    watcher._do_handle_one_event()

# def test_refupdated():
#     line = '{"type":"ref-updated","submitter":{"name":"Test User","email":"testuser@example.com","username":"testuser"},"refUpdate":{"oldRev":"6fc0c28ccca56d7a8a6059cab2744309dbce895c","newRev":"dbcec6cf4f117ce370471eae907b0e4ea882989e","refName":"master","project":"test-project"}}'
#     put_and_handle_event(line)
#
# def test_patchsetcreated():
#     line = '{"type":"patchset-created","change":{"project":"test-project","branch":"master","id":"Ic4a5733975188c001c270357d011d9f84b0e2593","number":"112","subject":"testing commit","owner":{"name":"Test User","email":"testuser@example.com","username":"testuser"},"url":"https://gerrit/112","commitMessage":"testing commit\n\nChange-Id: Ic4a5733975188c001c270357d011d9f84b0e2593\n","status":"NEW"},"patchSet":{"number":"1","revision":"18cfaba2990295443b33e22f65aabf18e2c1818f","parents":["11c0fc5e938ca72b982c3386f92ac130f3dd0551"],"ref":"refs/changes/12/112/1","uploader":{"name":"Test User","email":"testuser@example.com","username":"testuser"},"createdOn":1400707172,"author":{"name":"Test User","email":"testuser@example.com","username":"testuser"},"isDraft":false,"sizeInsertions":1,"sizeDeletions":-1},"uploader":{"name":"Test User","email":"testuser@example.com","username":"testuser"}}'
#     put_and_handle_event(line)
#
# def test_deletebranch():
#     line = '{"type":"ref-updated","submitter":{"name":"Test User","email":"testuser@example.com","username":"testuser"},"refUpdate":{"oldRev":"fd8b574e9626d4ab266429c73235a013a5fa72c4","newRev":"0000000000000000000000000000000000000000","refName":"topic1","project":"test-project"}}'
#     put_and_handle_event(line)
#
# def test_changemerged():
#     line = '{"type":"change-merged","change":{"project":"test-project","branch":"master","id":"Ia2419260a68d77492fd8a778648255aa3e7ae3f7","number":"114","subject":"testing commit","owner":{"name":"Test User","email":"testuser@example.com","username":"testuser"},"url":"https://gerrit/114","commitMessage":"testing commit\n\nChange-Id: Ia2419260a68d77492fd8a778648255aa3e7ae3f7\n","status":"MERGED"},"patchSet":{"number":"1","revision":"6291de8b6c0c06797873752a9202ca8eb6740032","parents":["e048341d110a454ca43b4f3c029f29bba42ac280"],"ref":"refs/changes/14/114/1","uploader":{"name":"Test User","email":"testuser@example.com","username":"testuser"},"createdOn":1400713343,"author":{"name":"Test User","email":"testuser@example.com","username":"testuser"},"isDraft":false,"sizeInsertions":1,"sizeDeletions":-1},"submitter":{"name":"Test User","email":"testuser@example.com","username":"testuser"}}'
#     put_and_handle_event(line)
