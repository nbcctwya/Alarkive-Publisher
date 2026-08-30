from __future__ import annotations

import copy
import json
import os
import tempfile
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Callable


STATE_SCHEMA_VERSION = "0.1"
STATE_FILENAME = "publish-state.json"
WORKFLOW_STATUSES = {"idle", "running", "waiting", "completed", "failed", "interrupted"}
PLATFORM_STATUSES = {"pending", "running", "waiting", "ready", "failed"}
PLATFORMS = ("xiaohongshu", "baijiahao", "wechat")
WORKFLOW_MODES = {"all", "single"}

_UNSET = object()
_STATE_LOCK = threading.RLock()


class PublishStateError(ValueError):
    """An expected error while reading or updating publisher runtime state."""


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def default_publish_state() -> dict[str, Any]:
    """Return a fresh default state for old packages without a sidecar."""

    return {
        "schema_version": STATE_SCHEMA_VERSION,
        "published": False,
        "published_at": None,
        "workflow": {
            "status": "idle",
            "workflow_mode": "all",
            "target_platform": None,
            "current_platform": None,
            "current_step": None,
            "message": None,
            "updated_at": None,
            "platforms": {
                platform: {"status": "pending", "message": None}
                for platform in PLATFORMS
            },
            "error": None,
        },
    }


def state_path(post_folder: Path | str) -> Path:
    return Path(post_folder).expanduser() / STATE_FILENAME


def _validate_state(state: Any) -> dict[str, Any]:
    if not isinstance(state, dict):
        raise PublishStateError("publish-state.json 的根对象无效。")
    if state.get("schema_version") != STATE_SCHEMA_VERSION:
        raise PublishStateError(
            f"publish-state.json schema_version 不受支持：{state.get('schema_version')}"
        )
    if not isinstance(state.get("published"), bool):
        raise PublishStateError("publish-state.json 的 published 必须是布尔值。")
    if state.get("published_at") is not None and not isinstance(state.get("published_at"), str):
        raise PublishStateError("publish-state.json 的 published_at 无效。")

    workflow = state.get("workflow")
    if not isinstance(workflow, dict) or workflow.get("status") not in WORKFLOW_STATUSES:
        raise PublishStateError("publish-state.json 的 workflow 无效。")
    # v0.1.7 adds optional workflow metadata.  Old sidecars without these
    # fields continue to mean the original all-platform workflow.  Do not
    # inject the defaults here: local-marker updates must preserve an old
    # workflow object exactly.
    workflow_mode = workflow.get("workflow_mode", "all")
    if workflow_mode not in WORKFLOW_MODES:
        raise PublishStateError("publish-state.json 的 workflow_mode 无效。")
    target_platform = workflow.get("target_platform")
    if target_platform is not None and target_platform not in PLATFORMS:
        raise PublishStateError("publish-state.json 的 target_platform 无效。")
    if workflow_mode == "single" and target_platform is None:
        raise PublishStateError("单平台 workflow 必须指定 target_platform。")
    if workflow_mode == "all" and target_platform is not None:
        raise PublishStateError("完整 workflow 不应指定 target_platform。")
    platforms = workflow.get("platforms")
    if not isinstance(platforms, dict):
        raise PublishStateError("publish-state.json 的 workflow.platforms 无效。")
    for platform in PLATFORMS:
        platform_state = platforms.get(platform)
        if (
            not isinstance(platform_state, dict)
            or platform_state.get("status") not in PLATFORM_STATUSES
        ):
            raise PublishStateError(
                f"publish-state.json 的 {platform} 平台状态无效。"
            )
    return state


def _read_unlocked(post_folder: Path) -> dict[str, Any]:
    path = state_path(post_folder)
    if not path.is_file():
        return default_publish_state()
    try:
        with path.open("r", encoding="utf-8") as file:
            state = json.load(file)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PublishStateError(f"publish-state.json 无法读取或解析：{exc}") from exc
    return copy.deepcopy(_validate_state(state))


def load_publish_state(post_folder: Path | str) -> dict[str, Any]:
    """Load runtime state without requiring the sidecar to exist."""

    folder = Path(post_folder).expanduser()
    with _STATE_LOCK:
        return _read_unlocked(folder)


def _write_atomic_unlocked(post_folder: Path, state: dict[str, Any]) -> None:
    folder = post_folder.resolve()
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / STATE_FILENAME
    temporary_path: Path | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{STATE_FILENAME}.", suffix=".tmp", dir=str(folder)
        )
        os.close(descriptor)
        temporary_path = Path(temporary_name)
        with temporary_path.open("w", encoding="utf-8", newline="") as file:
            json.dump(state, file, ensure_ascii=False, indent=2)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary_path, target)
    except OSError as exc:
        raise PublishStateError(f"publish-state.json 无法保存：{exc}") from exc
    finally:
        if temporary_path is not None and temporary_path.exists():
            try:
                temporary_path.unlink()
            except OSError:
                pass


def save_publish_state(post_folder: Path | str, state: dict[str, Any]) -> dict[str, Any]:
    """Atomically save a validated state and return a defensive copy."""

    validated = copy.deepcopy(_validate_state(state))
    folder = Path(post_folder).expanduser()
    with _STATE_LOCK:
        _write_atomic_unlocked(folder, validated)
    return copy.deepcopy(validated)


def update_publish_state(
    post_folder: Path | str,
    updater: Callable[[dict[str, Any]], None],
) -> dict[str, Any]:
    """Update one state under the process lock and atomically replace the file."""

    folder = Path(post_folder).expanduser()
    with _STATE_LOCK:
        state = _read_unlocked(folder)
        updater(state)
        _validate_state(state)
        _write_atomic_unlocked(folder, state)
        return copy.deepcopy(state)


def mark_published(post_folder: Path | str) -> dict[str, Any]:
    """Set only the local content-management publication marker."""

    def update(state: dict[str, Any]) -> None:
        state["published"] = True
        state["published_at"] = _now()

    return update_publish_state(post_folder, update)


def mark_unpublished(post_folder: Path | str) -> dict[str, Any]:
    """Set only ``published`` and ``published_at``; preserve workflow exactly."""

    def update(state: dict[str, Any]) -> None:
        state["published"] = False
        state["published_at"] = None

    return update_publish_state(post_folder, update)


def initialize_workflow(
    post_folder: Path | str,
    *,
    workflow_mode: str = "all",
    target_platform: str | None = None,
) -> dict[str, Any]:
    """Reset only workflow progress for a newly started publish preparation."""

    if workflow_mode not in WORKFLOW_MODES:
        raise PublishStateError(f"不支持的 workflow_mode：{workflow_mode}")
    if target_platform is not None and target_platform not in PLATFORMS:
        raise PublishStateError(f"不支持的平台：{target_platform}")
    if workflow_mode == "single" and target_platform is None:
        raise PublishStateError("单平台 workflow 必须指定 target_platform。")
    if workflow_mode == "all" and target_platform is not None:
        raise PublishStateError("完整 workflow 不应指定 target_platform。")

    fresh_workflow = default_publish_state()["workflow"]

    def update(state: dict[str, Any]) -> None:
        state["workflow"] = copy.deepcopy(fresh_workflow)
        state["workflow"]["status"] = "running"
        state["workflow"]["workflow_mode"] = workflow_mode
        state["workflow"]["target_platform"] = target_platform
        state["workflow"]["message"] = "正在启动发布准备流程"
        state["workflow"]["updated_at"] = _now()

    return update_publish_state(post_folder, update)


def update_workflow(
    post_folder: Path | str,
    *,
    status: str | object = _UNSET,
    current_platform: str | None | object = _UNSET,
    current_step: str | None | object = _UNSET,
    message: str | None | object = _UNSET,
    platform: str | None = None,
    platform_status: str | object = _UNSET,
    platform_message: str | None | object = _UNSET,
    error: dict[str, Any] | None | object = _UNSET,
) -> dict[str, Any]:
    """Update selected workflow fields while preserving all other progress."""

    if status is not _UNSET and status not in WORKFLOW_STATUSES:
        raise PublishStateError(f"不支持的 workflow 状态：{status}")
    if platform is not None and platform not in PLATFORMS:
        raise PublishStateError(f"不支持的平台：{platform}")
    if platform_status is not _UNSET and platform_status not in PLATFORM_STATUSES:
        raise PublishStateError(f"不支持的平台状态：{platform_status}")

    def update(state: dict[str, Any]) -> None:
        workflow = state["workflow"]
        if status is not _UNSET:
            workflow["status"] = status
        if current_platform is not _UNSET:
            workflow["current_platform"] = current_platform
        if current_step is not _UNSET:
            workflow["current_step"] = current_step
        if message is not _UNSET:
            workflow["message"] = message
        if error is not _UNSET:
            workflow["error"] = copy.deepcopy(error)
        if platform is not None:
            platform_state = workflow["platforms"][platform]
            if platform_status is not _UNSET:
                platform_state["status"] = platform_status
            if platform_message is not _UNSET:
                platform_state["message"] = platform_message
        workflow["updated_at"] = _now()

    return update_publish_state(post_folder, update)


def mark_interrupted(post_folder: Path | str) -> dict[str, Any]:
    """Persist that a running/waiting workflow disappeared with the server."""

    return update_workflow(
        post_folder,
        status="interrupted",
        message="上一次发布流程未正常结束。",
        error={
            "platform": None,
            "step": None,
            "type": "Interrupted",
            "message": "上一次发布流程未正常结束。",
        },
    )
