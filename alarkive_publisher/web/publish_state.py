from __future__ import annotations

import copy
import json
import os
import tempfile
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from ..routing import PUBLISH_TARGETS, normalize_target


STATE_SCHEMA_VERSION = "0.1"
STATE_FILENAME = "publish-state.json"
WORKFLOW_STATUSES = {"idle", "running", "waiting", "completed", "failed", "interrupted"}
PLATFORM_STATUSES = {"pending", "running", "waiting", "ready", "failed"}
# Canonical v0.2 platform targets. Keep the exported name for callers that
# imported it from the v0.1 state module.
PLATFORMS = PUBLISH_TARGETS
LEGACY_STATE_PLATFORMS = ("xiaohongshu", "wechat")
WORKFLOW_MODES = {"all", "single"}

_UNSET = object()
_STATE_LOCK = threading.RLock()


class PublishStateError(ValueError):
    """An expected error while reading or updating publisher runtime state."""


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _platform_state(status: str = "pending", message: str | None = None) -> dict[str, Any]:
    return {"status": status, "message": message}


def default_publish_state() -> dict[str, Any]:
    """Return a fresh v0.2 target state for a package without a sidecar."""

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
                target: _platform_state() for target in PUBLISH_TARGETS
            },
            "error": None,
        },
    }


def state_path(post_folder: Path | str) -> Path:
    return Path(post_folder).expanduser() / STATE_FILENAME


def _normalise_legacy_state(state: dict[str, Any]) -> dict[str, Any]:
    """Read v0.1.8 sidecars into canonical v0.2 target slots in memory."""

    workflow = state.get("workflow")
    if not isinstance(workflow, dict):
        return state
    platforms = workflow.get("platforms")
    if not isinstance(platforms, dict):
        return state

    if not any(key in platforms for key in LEGACY_STATE_PLATFORMS):
        return state
    normalised = copy.deepcopy(state)
    normalised_workflow = normalised["workflow"]
    normalised_platforms = normalised_workflow["platforms"]
    for target in PUBLISH_TARGETS:
        normalised_platforms.setdefault(target, _platform_state())
    if "baijiahao" in platforms:
        normalised_platforms["baijiahao"] = copy.deepcopy(platforms["baijiahao"])
    if "wechat" in platforms:
        normalised_platforms["wechat_image"] = copy.deepcopy(platforms["wechat"])
    if normalised_workflow.get("target_platform") == "wechat":
        normalised_workflow["target_platform"] = "wechat_image"
    if normalised_workflow.get("current_platform") == "wechat":
        normalised_workflow["current_platform"] = "wechat_image"
    # Preserve legacy xiaohongshu state for old diagnostics without making it
    # a v0.2 target. The Web UI never renders this compatibility-only entry.
    return normalised


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
    workflow_mode = workflow.get("workflow_mode", "all")
    if workflow_mode not in WORKFLOW_MODES:
        raise PublishStateError("publish-state.json 的 workflow_mode 无效。")
    target_platform = workflow.get("target_platform")
    valid_targets = set(PUBLISH_TARGETS) | set(LEGACY_STATE_PLATFORMS)
    if target_platform is not None and target_platform not in valid_targets:
        raise PublishStateError("publish-state.json 的 target_platform 无效。")
    if workflow_mode == "single" and target_platform is None:
        raise PublishStateError("单平台 workflow 必须指定 target_platform。")
    if workflow_mode == "all" and target_platform is not None:
        raise PublishStateError("完整 workflow 不应指定 target_platform。")
    current_platform = workflow.get("current_platform")
    if current_platform is not None and current_platform not in valid_targets:
        raise PublishStateError("publish-state.json 的 current_platform 无效。")

    platforms = workflow.get("platforms")
    if not isinstance(platforms, dict):
        raise PublishStateError("publish-state.json 的 workflow.platforms 无效。")
    allowed_platforms = set(PUBLISH_TARGETS) | set(LEGACY_STATE_PLATFORMS)
    unknown = set(platforms) - allowed_platforms
    if unknown:
        raise PublishStateError(
            "publish-state.json 包含不支持的平台状态：" + "、".join(sorted(unknown))
        )
    for platform in PUBLISH_TARGETS:
        platform_state = platforms.get(platform)
        if (
            not isinstance(platform_state, dict)
            or platform_state.get("status") not in PLATFORM_STATUSES
        ):
            raise PublishStateError(f"publish-state.json 的 {platform} 平台状态无效。")
    for platform in LEGACY_STATE_PLATFORMS:
        if platform not in platforms:
            continue
        platform_state = platforms[platform]
        if (
            not isinstance(platform_state, dict)
            or platform_state.get("status") not in PLATFORM_STATUSES
        ):
            raise PublishStateError(f"publish-state.json 的 {platform} 平台状态无效。")
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
    return copy.deepcopy(_validate_state(_normalise_legacy_state(state)))


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
    validated = copy.deepcopy(_validate_state(_normalise_legacy_state(state)))
    folder = Path(post_folder).expanduser()
    with _STATE_LOCK:
        _write_atomic_unlocked(folder, validated)
    return copy.deepcopy(validated)


def update_publish_state(
    post_folder: Path | str,
    updater: Callable[[dict[str, Any]], None],
) -> dict[str, Any]:
    folder = Path(post_folder).expanduser()
    with _STATE_LOCK:
        state = _read_unlocked(folder)
        updater(state)
        _validate_state(state)
        _write_atomic_unlocked(folder, state)
        return copy.deepcopy(state)


def mark_published(post_folder: Path | str) -> dict[str, Any]:
    def update(state: dict[str, Any]) -> None:
        state["published"] = True
        state["published_at"] = _now()

    return update_publish_state(post_folder, update)


def mark_unpublished(post_folder: Path | str) -> dict[str, Any]:
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
    if workflow_mode not in WORKFLOW_MODES:
        raise PublishStateError(f"不支持的 workflow_mode：{workflow_mode}")
    if target_platform is not None:
        target_platform = normalize_target(target_platform)
        if target_platform not in PUBLISH_TARGETS and target_platform != "xiaohongshu":
            raise PublishStateError(f"不支持的平台：{target_platform}")
    if workflow_mode == "single" and target_platform is None:
        raise PublishStateError("单平台 workflow 必须指定 target_platform。")
    if workflow_mode == "all" and target_platform is not None:
        raise PublishStateError("完整 workflow 不应指定 target_platform。")

    fresh_workflow = default_publish_state()["workflow"]
    # Keep old sidecar readers and v0.1 controller integrations harmlessly
    # readable after a workflow starts. Canonical v0.2 targets remain the
    # authoritative slots; these aliases are never rendered by the Web UI.
    fresh_workflow["platforms"]["wechat"] = _platform_state()
    fresh_workflow["platforms"]["xiaohongshu"] = _platform_state()
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
    if status is not _UNSET and status not in WORKFLOW_STATUSES:
        raise PublishStateError(f"不支持的 workflow 状态：{status}")
    if platform is not None:
        platform = normalize_target(platform)
        if platform not in PUBLISH_TARGETS and platform != "xiaohongshu":
            raise PublishStateError(f"不支持的平台：{platform}")
    if platform_status is not _UNSET and platform_status not in PLATFORM_STATUSES:
        raise PublishStateError(f"不支持的平台状态：{platform_status}")
    if current_platform is not _UNSET and current_platform is not None:
        current_platform = normalize_target(current_platform)

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
            workflow["platforms"].setdefault(platform, _platform_state())
            platform_state = workflow["platforms"][platform]
            if platform_status is not _UNSET:
                platform_state["status"] = platform_status
            if platform_message is not _UNSET:
                platform_state["message"] = platform_message
            if platform == "wechat_image":
                # An old controller may still address the legacy key.
                workflow["platforms"].setdefault("wechat", _platform_state())
                if platform_status is not _UNSET:
                    workflow["platforms"]["wechat"]["status"] = platform_status
                if platform_message is not _UNSET:
                    workflow["platforms"]["wechat"]["message"] = platform_message
        workflow["updated_at"] = _now()

    return update_publish_state(post_folder, update)


def mark_interrupted(post_folder: Path | str) -> dict[str, Any]:
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


__all__ = [
    "PLATFORMS",
    "PLATFORM_STATUSES",
    "PublishStateError",
    "STATE_FILENAME",
    "default_publish_state",
    "initialize_workflow",
    "load_publish_state",
    "mark_interrupted",
    "mark_published",
    "mark_unpublished",
    "save_publish_state",
    "state_path",
    "update_publish_state",
    "update_workflow",
]
