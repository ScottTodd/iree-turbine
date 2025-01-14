#!/usr/bin/env python
# Copyright 2025 The IREE Authors
#
# Licensed under the Apache License v2.0 with LLVM Exceptions.
# See https://llvm.org/LICENSE.txt for license information.
# SPDX-License-Identifier: Apache-2.0 WITH LLVM-exception

"""Updates the pinned IREE package versions in this repository.

Usage:
  update_iree_version_pins.py
"""

from pathlib import Path
import re
import subprocess
import sys
import textwrap

THIS_DIR = Path(__file__).parent
REPO_ROOT = THIS_DIR.parent
REQUIREMENTS_IREE_PINNED_PATH = REPO_ROOT / "requirements-iree-pinned.txt"
UPDATE_GIT_COMMIT_MESSAGE_PATH = THIS_DIR / "update_iree_version_text.txt"


def get_latest_package_version(package_name, extra_pip_args=[]):
    print("\n-------------------------------------------------------------------------")
    print(f"Finding latest available package version for package '{package_name}'\n")

    # This queries the pip index to get the latest version.
    #
    # Note: the `index` subcommand is experimental. Other possible approaches:
    #   * Install (into a venv) then check what was installed with --report or 'pip freeze'
    #   * Download then check what was downloaded
    #   * Scrape the package index and/or release page (https://iree.dev/pip-release-links.html)
    subprocess_args = [
        sys.executable,
        "-m",
        "pip",
        "index",
        "versions",
        package_name,
        "--disable-pip-version-check",
    ]
    subprocess_args.extend(extra_pip_args)

    print(f"Running command:\n  {subprocess.list2cmdline(subprocess_args)}\n")
    result = subprocess.run(subprocess_args, stdout=subprocess.PIPE)
    output = result.stdout.decode("utf-8")
    print(f"Command output:\n{textwrap.indent(output, '  ')}")

    # Search for text like `iree-base-compiler (3.2.0rc20250109)` within the
    # multiple lines of output from the command.
    # WARNING: The output from `pip index` is UNSTABLE and UNSTRUCTURED, but
    # this seems to work using Python 3.11.2 and pip 22.3.1.
    version_search_regex = re.compile(f"{package_name}\s\((.*)\)")
    matches = version_search_regex.match(output)
    if not matches:
        raise RuntimeError("Failed to find a package version using regex")
    version = matches.groups()[0]
    print(
        f"Found package version for '{package_name}' in output using regex: '{version}'"
    )
    return version


def main():
    print("Updating IREE version pins!")

    iree_nightly_pip_args = [
        "--pre",
        "--find-links",
        "https://iree.dev/pip-release-links.html",
    ]
    iree_base_compiler_version = get_latest_package_version(
        "iree-base-compiler", iree_nightly_pip_args
    )
    iree_base_runtime_version = get_latest_package_version(
        "iree-base-runtime", iree_nightly_pip_args
    )

    print("\n-------------------------------------------------------------------------")
    print(f"Editing version pins in '{REQUIREMENTS_IREE_PINNED_PATH}'")
    with open(REQUIREMENTS_IREE_PINNED_PATH, "r") as f:
        text = f.read()
        print(f"Original text:\n{textwrap.indent(text, '  ')}\n")

        old_version = re.findall("iree-base-compiler==(.*)", text)[0]

        text = re.sub(
            "iree-base-compiler==.*",
            f"iree-base-compiler=={iree_base_compiler_version}",
            text,
        )
        text = re.sub(
            "iree-base-runtime==.*",
            f"iree-base-runtime=={iree_base_runtime_version}",
            text,
        )
        print(f"New text:\n{textwrap.indent(text, '  ')}\n")

        # Write a commit message.
        body_text = f"""Automated update of IREE deps to {iree_base_compiler_version}.

Diff: https://github.com/iree-org/iree/compare/iree-{old_version}...iree-{iree_base_compiler_version}
        """
        with open(UPDATE_GIT_COMMIT_MESSAGE_PATH, "w") as commit_f:
            commit_f.write(body_text)
    with open(REQUIREMENTS_IREE_PINNED_PATH, "w") as f:
        f.write(text)

    print("-------------------------------------------------------------------------")
    print("Edits complete")


if __name__ == "__main__":
    main()
