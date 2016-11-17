#!/bin/bash

echo Building repowatch RPM...

GITROOT=$(git rev-parse --show-toplevel)
cd ${GITROOT}

COMMIT=$(git rev-parse --short HEAD)
COMMIT_DATE=$(git log HEAD -n1 --pretty=format:%ai | awk '{gsub(/\-/,"",$1);printf $1}')
SHORT_COMMIT=${COMMIT:0:7}

RPMTOPDIR=$GITROOT/rpmbuild
mkdir -p ${RPMTOPDIR}/{SOURCES,SPECS}

git archive --format=tar --prefix=repowatch-${SHORT_COMMIT}/ HEAD | gzip -c > ${RPMTOPDIR}/SOURCES/repowatch-${SHORT_COMMIT}.tar.gz

# copy the repowatch.spec
cp ./contrib/repowatch.spec $RPMTOPDIR/SPECS/repowatch.spec

# Update COMMIT and DATE
sed -i "s/%global commit0\ .*/%global commit0 ${COMMIT}/" ${RPMTOPDIR}/SPECS/repowatch.spec
sed -i "s/%global commit0_date\ .*/%global commit0_date ${COMMIT_DATE}/" ${RPMTOPDIR}/SPECS/repowatch.spec

# Build SRC and binary RPMs
rpmbuild    --define "_topdir ${RPMTOPDIR}" \
            --define "_rpmdir $PWD"       \
            --define "_srcrpmdir $PWD"    \
            --define '_rpmfilename %%{NAME}-%%{VERSION}-%%{RELEASE}.%%{ARCH}.rpm' \
            -ba ${RPMTOPDIR}/SPECS/repowatch.spec
