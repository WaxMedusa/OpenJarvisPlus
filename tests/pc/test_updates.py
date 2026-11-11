from __future__ import annotations

from openjarvis.pc.updates import parse_upgrade_table

SAMPLE = """\
Name                          Id                Version  Available  Source
--------------------------------------------------------------------------------
Git                           Git.Git           2.40.0   2.41.0     winget
Mozilla Firefox (x64 en-US)   Mozilla.Firefox   120.0    121.0      winget
7-Zip 23.01 (x64)             7zip.7zip         23.01    24.05      winget
"""


def test_parse_upgrade_table() -> None:
    packages = parse_upgrade_table(SAMPLE)
    assert [package.package_id for package in packages] == [
        "Git.Git",
        "Mozilla.Firefox",
        "7zip.7zip",
    ]
    assert packages[0].name == "Git"
    assert packages[1].name.startswith("Mozilla Firefox")
    assert packages[1].available == "121.0"


def test_parse_ignores_noise_and_duplicates() -> None:
    output = (
        "Some banner text\n"
        "Name      Id            Version   Available  Source\n"
        "---------------------------------------------------\n"
        "App       Vendor.App    1.0       2.0        winget\n"
        "App       Vendor.App    1.0       2.0        winget\n"
        "\n"
        "2 upgrades available.\n"
    )
    packages = parse_upgrade_table(output)
    assert len(packages) == 1
    assert packages[0].package_id == "Vendor.App"
