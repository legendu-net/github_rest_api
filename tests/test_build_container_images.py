from pathlib import Path
from unittest.mock import patch
from github_rest_api.scripts.container.build_container_images import (
    has_relevant_changes,
)

RELEVANT_FILE = Path("base/Dockerfile")
IRRELEVANT_FILE = Path("docs/readme.md")
COMMIT1 = "abc1234"
COMMIT2 = "def5678"
IMAGE_DIRS = ["base"]


def test_has_relevant_changes_empty_commits():
    # Empty commit identifiers should always signal relevant changes.
    assert has_relevant_changes("", "", IMAGE_DIRS)
    assert has_relevant_changes("", COMMIT2, IMAGE_DIRS)
    assert has_relevant_changes(COMMIT1, "", IMAGE_DIRS)


@patch(
    "github_rest_api.scripts.container.build_container_images.changed_files_between",
    return_value=[],
)
def test_has_relevant_changes_no_changes(mock_changed):
    assert not has_relevant_changes(COMMIT1, COMMIT2, IMAGE_DIRS)
    mock_changed.assert_called_once()


@patch(
    "github_rest_api.scripts.container.build_container_images.changed_files_between",
    return_value=[IRRELEVANT_FILE],
)
def test_has_relevant_changes_only_irrelevant_files(mock_changed):
    assert not has_relevant_changes(COMMIT1, COMMIT2, IMAGE_DIRS)
    mock_changed.assert_called_once()


@patch(
    "github_rest_api.scripts.container.build_container_images.changed_files_between",
    return_value=[RELEVANT_FILE],
)
def test_has_relevant_changes_with_relevant_file(mock_changed):
    assert has_relevant_changes(COMMIT1, COMMIT2, IMAGE_DIRS)
    mock_changed.assert_called_once()


@patch(
    "github_rest_api.scripts.container.build_container_images.changed_files_between",
    return_value=[IRRELEVANT_FILE, RELEVANT_FILE],
)
def test_has_relevant_changes_mixed_files(mock_changed):
    assert has_relevant_changes(COMMIT1, COMMIT2, IMAGE_DIRS)
    mock_changed.assert_called_once()


@patch(
    "github_rest_api.scripts.container.build_container_images.changed_files_between",
    return_value=[RELEVANT_FILE],
)
def test_has_relevant_changes_bytes_commits(mock_changed):
    # Bytes commit identifiers should work the same as strings.
    assert has_relevant_changes(b"abc1234", b"def5678", IMAGE_DIRS)
    mock_changed.assert_called_once()
