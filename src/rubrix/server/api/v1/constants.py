from rubrix._constants import DATASET_NAME_REGEX_PATTERN
from rubrix._constants import (
    RUBRIX_WORKSPACE_HEADER_NAME as _RUBRIX_WORKSPACE_HEADER_NAME,
)
from rubrix.server.security.model import (
    WORKSPACE_NAME_PATTERN as _WORKSPACE_NAME_PATTERN,
)

API_VERSION = "v1"

TASK_DATASET_ENDPOINT = "/{task}/{name}"

TASK_PATTERN = r"[a-zA-Z-_]+"
TASKS_PATTERN = rf"({TASK_PATTERN})(,{TASK_PATTERN})*"

DATASET_NAME_PATTERN = DATASET_NAME_REGEX_PATTERN
RUBRIX_WORKSPACE_HEADER_NAME = _RUBRIX_WORKSPACE_HEADER_NAME

WORKSPACE_NAME_PATTERN = _WORKSPACE_NAME_PATTERN