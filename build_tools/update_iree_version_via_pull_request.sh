#!/bin/bash

# Copyright 2025 The IREE Authors
#
# Licensed under the Apache License v2.0 with LLVM Exceptions.
# See https://llvm.org/LICENSE.txt for license information.
# SPDX-License-Identifier: Apache-2.0 WITH LLVM-exception

# This script was inspired by https://github.com/romoh/dependencies-autoupdate,
# with substantial changes to fit our needs.

set -exo pipefail

GITHUB_TOKEN=$1
UPDATE_COMMAND=$2

SCRIPT_DIR=$(dirname "$0")
PR_BASE_BRANCH_NAME="main"
BRANCH_NAME="integrates/iree"
GIT_EMAIL="noreply@github.com"

print_help_string() {
    echo "USAGE: update_iree_version_via_pull_request.sh GITHUB_TOKEN UPDATE_COMMAND"
}

if [ -z "${GITHUB_TOKEN}" ]; then
    echo "Missing GITHUB_TOKEN"
    print_help_string
    exit 1
fi

if [ -z "${UPDATE_COMMAND}" ]; then
    echo "Missing UPDATE_COMMAND"
    print_help_string
    exit 1
fi

git fetch
BRANCH_EXISTS=$(git branch --list ${BRANCH_NAME})

# Checkout or create branch.
if [ -z "${BRANCH_EXISTS}" ]; then
    git checkout -b ${BRANCH_NAME}
else
    echo "Branch name ${BRANCH_NAME} already exists, checking out"
    git checkout ${BRANCH_NAME}
    git pull
    git reset --hard origin/${PR_BASE_BRANCH_NAME}
fi

echo "Running update command ${UPDATE_COMMAND}"
eval ${UPDATE_COMMAND}

set +e
git diff --exit-code >/dev/null 2>&1
if [ $? = 1 ]
then
    set -e
    echo "Updates detected, committing changes and pushing to PR"

    git config --global user.email ${GIT_EMAIL}
    git config --global user.name ${GITHUB_ACTOR}

    # format: https://[username]:[token]@github.com/[organization]/[repo].git
    git remote add authenticated "https://${GITHUB_ACTOR}:${GITHUB_TOKEN}@github.com/${GITHUB_REPOSITORY}.git"

    git add -A
    git commit -a -m "Update dependencies" --signoff
    git push authenticated -f

    echo "https://api.github.com/repos/${GITHUB_REPOSITORY}/pulls"

    PR_BODY=`cat ${SCRIPT_DIR}/update_iree_version_text.txt`

    # Update the existing PR or create a new one if none already exists.
    # TODO: add generated PR description
    RESPONSE=$(curl -X POST -H "Content-Type: application/json" -H "Accept: application/vnd.github.v3+json" -H "Authorization: token ${GITHUB_TOKEN}" \
         --data '{"title":"Automatically update IREE version pins.","head": "'"${BRANCH_NAME}"'","base":"'"${PR_BASE_BRANCH_NAME}"'", "body":"'"${PR_BODY}"'"}' \
         "https://api.github.com/repos/${GITHUB_REPOSITORY}/pulls")
    echo ${RESPONSE}

    if [[ "${RESPONSE}" == *"already exist"* ]]; then
        echo "Pull request already opened. Updates were pushed to the existing PR instead"
        exit 0
    fi
else
    set -e
    echo "No updates were detected, no PR will be created"
    exit 0
fi
