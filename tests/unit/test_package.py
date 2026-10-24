import re

import coach


def test_package_exposes_semantic_versions() -> None:
    semver = re.compile(r"^\d+\.\d+\.\d+$")
    assert semver.match(coach.__version__)
    assert semver.match(coach.SPEC_VERSION)
