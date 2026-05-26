from __future__ import annotations

import base64
import json
import threading
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from services.config import DATA_DIR, config
from services.content_filter import request_text
from services.log_service import LOG_TYPE_CALL, log_service
from services.protocol import openai_v1_image_edit, openai_v1_image_generations

TASK_STATUS_QUEUED = "queued"
TASK_STATUS_RUNNING = "running"
TASK_STATUS_SUCCESS = "success"
TASK_STATUS_ERROR = "error"
TASK_STATUS_CANCELLED = "cancelled"
TERMINAL_STATUSES = {TASK_STATUS_SUCCESS, TASK_STATUS_ERROR, TASK_STATUS_CANCELLED}
UNFINISHED_STATUSES = {TASK_STATUS_QUEUED, TASK_STATUS_RUNNING}


def _now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _timestamp(value: object) -> float:
    if not isinstance(value, str) or not value.strip():
        return 0.0
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(value[:26], fmt).timestamp()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


def _clean(value: object, default: str = "") -> str:
    return str(value or default).strip()


def _owner_id(identity: dict[str, object]) -> str:
    return _clean(identity.get("id")) or "anonymous"


def _owner_name(identity: dict[str, object]) -> str:
    return _clean(identity.get("name")) or _owner_id(identity)


def _owner_role(identity: dict[str, object]) -> str:
    return _clean(identity.get("role"), "user") or "user"


def _task_key(owner_id: str, task_id: str) -> str:
    return f"{owner_id}:{task_id}"


def _collect_image_urls(data: list[Any]) -> list[str]:
    urls: list[str] = []
    for item in data:
        if isinstance(item, dict):
            url = item.get("url")
            if isinstance(url, str) and url:
                urls.append(url)
    return urls


def _serialize_images(images: object) -> list[dict[str, str]]:
    if not isinstance(images, list):
        return []
    items: list[dict[str, str]] = []
    for image in images:
        if not isinstance(image, tuple) or len(image) != 3:
            continue
        content, filename, content_type = image
        if not isinstance(content, bytes):
            continue
        items.append(
            {
                "bytes_b64": base64.b64encode(content).decode("ascii"),
                "filename": _clean(filename, "image.png"),
                "content_type": _clean(content_type, "application/octet-stream"),
            }
        )
    return items


def _normalize_serialized_images(images: object) -> list[dict[str, str]]:
    if not isinstance(images, list):
        return []
    normalized: list[dict[str, str]] = []
    for item in images:
        if not isinstance(item, dict):
            continue
        bytes_b64 = _clean(item.get("bytes_b64"))
        filename = _clean(item.get("filename"), "image.png")
        content_type = _clean(item.get("content_type"), "application/octet-stream")
        if not bytes_b64:
            continue
        normalized.append({"bytes_b64": bytes_b64, "filename": filename, "content_type": content_type})
    return normalized


def _deserialize_images(images: object) -> list[tuple[bytes, str, str]]:
    result: list[tuple[bytes, str, str]] = []
    for item in _normalize_serialized_images(images):
        try:
            content = base64.b64decode(item["bytes_b64"], validate=True)
        except Exception:
            continue
        result.append((content, item["filename"], item["content_type"]))
    return result


def _serialize_request(payload: dict[str, Any]) -> dict[str, Any]:
    request = {
        "prompt": _clean(payload.get("prompt")),
        "model": _clean(payload.get("model"), "gpt-image-2"),
        "n": 1,
        "size": _clean(payload.get("size")),
        "response_format": "url",
        "base_url": _clean(payload.get("base_url")),
    }
    serialized_images = _serialize_images(payload.get("images"))
    if serialized_images:
        request["images"] = serialized_images
    return request


def _normalize_request(value: object, mode: str, *, prompt: str, model: str, size: str) -> dict[str, Any]:
    request = value if isinstance(value, dict) else {}
    normalized = {
        "prompt": _clean(request.get("prompt"), prompt),
        "model": _clean(request.get("model"), model),
        "n": 1,
        "size": _clean(request.get("size"), size),
        "response_format": "url",
        "base_url": _clean(request.get("base_url")),
    }
    if mode == "edit":
        images = _normalize_serialized_images(request.get("images"))
        if images:
            normalized["images"] = images
    return normalized


def _task_request_payload(task: dict[str, Any], *, base_url: str = "") -> dict[str, Any]:
    request = task.get("request") if isinstance(task.get("request"), dict) else {}
    payload = {
        "prompt": _clean(request.get("prompt"), _clean(task.get("prompt"))),
        "model": _clean(request.get("model"), _clean(task.get("model"), "gpt-image-2")),
        "n": 1,
        "size": _clean(request.get("size"), _clean(task.get("size"))),
        "response_format": "url",
        "base_url": _clean(base_url or request.get("base_url")),
    }
    if task.get("mode") == "edit":
        payload["images"] = _deserialize_images(request.get("images"))
    return payload


def _task_identity(task: dict[str, Any]) -> dict[str, object]:
    return {
        "id": _clean(task.get("owner_id")) or "anonymous",
        "name": _clean(task.get("owner_name")) or _clean(task.get("owner_id")) or "anonymous",
        "role": _clean(task.get("owner_role"), "user") or "user",
    }


def _can_access(identity: dict[str, object], task: dict[str, Any]) -> bool:
    return _owner_role(identity) == "admin" or _owner_id(identity) == _clean(task.get("owner_id"))


def _public_task(task: dict[str, Any]) -> dict[str, Any]:
    item = {
        "key": task.get("key") or _task_key(_clean(task.get("owner_id")), _clean(task.get("id"))),
        "id": task.get("id"),
        "owner_id": task.get("owner_id"),
        "owner_name": task.get("owner_name"),
        "owner_role": task.get("owner_role"),
        "status": task.get("status"),
        "mode": task.get("mode"),
        "model": task.get("model"),
        "size": task.get("size"),
        "prompt": task.get("prompt"),
        "input_count": int(task.get("input_count") or 0),
        "created_at": task.get("created_at"),
        "updated_at": task.get("updated_at"),
    }
    if task.get("data") is not None:
        item["data"] = task.get("data")
    if task.get("error"):
        item["error"] = task.get("error")
    return item


class ImageTaskService:
    def __init__(
        self,
        path: Path,
        *,
        generation_handler: Callable[[dict[str, Any]], dict[str, Any]] = openai_v1_image_generations.handle,
        edit_handler: Callable[[dict[str, Any]], dict[str, Any]] = openai_v1_image_edit.handle,
        retention_days_getter: Callable[[], int] | None = None,
    ):
        self.path = path
        self.generation_handler = generation_handler
        self.edit_handler = edit_handler
        self.retention_days_getter = retention_days_getter or (lambda: config.image_retention_days)
        self._lock = threading.RLock()
        self._tasks: dict[str, dict[str, Any]] = {}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            self._tasks = self._load_locked()
            changed = self._recover_unfinished_locked()
            changed = self._cleanup_locked() or changed
            if changed:
                self._save_locked()

    def submit_generation(
        self,
        identity: dict[str, object],
        *,
        client_task_id: str,
        prompt: str,
        model: str,
        size: str | None,
        base_url: str,
    ) -> dict[str, Any]:
        payload = {
            "prompt": prompt,
            "model": model,
            "n": 1,
            "size": size,
            "response_format": "url",
            "base_url": base_url,
        }
        return self._submit(identity, client_task_id=client_task_id, mode="generate", payload=payload)

    def submit_edit(
        self,
        identity: dict[str, object],
        *,
        client_task_id: str,
        prompt: str,
        model: str,
        size: str | None,
        base_url: str,
        images: list[tuple[bytes, str, str]],
    ) -> dict[str, Any]:
        payload = {
            "prompt": prompt,
            "images": images,
            "model": model,
            "n": 1,
            "size": size,
            "response_format": "url",
            "base_url": base_url,
        }
        return self._submit(identity, client_task_id=client_task_id, mode="edit", payload=payload)

    def list_tasks(self, identity: dict[str, object], task_ids: list[str]) -> dict[str, Any]:
        owner = _owner_id(identity)
        is_admin = _owner_role(identity) == "admin"
        requested_ids = [_clean(task_id) for task_id in task_ids if _clean(task_id)]
        with self._lock:
            if self._cleanup_locked():
                self._save_locked()
            items = []
            missing_ids = []
            if requested_ids:
                for task_id in requested_ids:
                    task = self._tasks.get(task_id)
                    if task is None and not is_admin:
                        task = self._tasks.get(_task_key(owner, task_id))
                    if task is None:
                        if is_admin:
                            matches = [item for item in self._tasks.values() if _clean(item.get("id")) == task_id]
                            items.extend(_public_task(item) for item in matches if _can_access(identity, item))
                            if matches:
                                continue
                        missing_ids.append(task_id)
                        continue
                    if not _can_access(identity, task):
                        missing_ids.append(task_id)
                        continue
                    items.append(_public_task(task))
                return {"items": items, "missing_ids": missing_ids}

            items = [
                _public_task(task)
                for task in self._tasks.values()
                if is_admin or _clean(task.get("owner_id")) == owner
            ]
            items.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
            return {"items": items, "missing_ids": []}

    def cancel_task(self, identity: dict[str, object], task_key: str) -> dict[str, Any]:
        key = _clean(task_key)
        with self._lock:
            task = self._tasks.get(key)
            if task is None or not _can_access(identity, task):
                raise KeyError("task not found")
            status = _clean(task.get("status"))
            if status == TASK_STATUS_CANCELLED:
                return _public_task(task)
            if status not in UNFINISHED_STATUSES:
                raise ValueError("only queued or running task can be cancelled")
            task["status"] = TASK_STATUS_CANCELLED
            task["error"] = "任务已取消"
            task.pop("data", None)
            task["updated_at"] = _now_iso()
            self._save_locked()
            return _public_task(task)

    def retry_task(self, identity: dict[str, object], task_key: str, *, base_url: str) -> dict[str, Any]:
        key = _clean(task_key)
        with self._lock:
            task = self._tasks.get(key)
            if task is None or not _can_access(identity, task):
                raise KeyError("task not found")
            if _clean(task.get("status")) != TASK_STATUS_ERROR:
                raise ValueError("only error task can be retried")
            payload = _task_request_payload(task, base_url=base_url)
            if not _clean(payload.get("prompt")):
                raise ValueError("missing task prompt")
            if task.get("mode") == "edit" and not payload.get("images"):
                raise ValueError("missing original task images")
            request = task.get("request") if isinstance(task.get("request"), dict) else {}
            request["base_url"] = _clean(base_url or request.get("base_url"))
            task["request"] = request
            task["status"] = TASK_STATUS_QUEUED
            task.pop("error", None)
            task.pop("data", None)
            task["updated_at"] = _now_iso()
            self._save_locked()
            public_task = _public_task(task)
            mode = _clean(task.get("mode"), "generate")
            model = _clean(task.get("model"), "gpt-image-2")
            task_identity = _task_identity(task)

        self._start_task_thread(key, mode, payload, task_identity, model)
        return public_task

    def _submit(
        self,
        identity: dict[str, object],
        *,
        client_task_id: str,
        mode: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        task_id = _clean(client_task_id)
        if not task_id:
            raise ValueError("client_task_id is required")
        owner = _owner_id(identity)
        key = _task_key(owner, task_id)
        now = _now_iso()
        with self._lock:
            cleaned = self._cleanup_locked()
            task = self._tasks.get(key)
            if task is not None:
                if cleaned:
                    self._save_locked()
                return _public_task(task)
            task = {
                "key": key,
                "id": task_id,
                "owner_id": owner,
                "owner_name": _owner_name(identity),
                "owner_role": _owner_role(identity),
                "status": TASK_STATUS_QUEUED,
                "mode": mode,
                "model": _clean(payload.get("model"), "gpt-image-2"),
                "size": _clean(payload.get("size")),
                "prompt": _clean(payload.get("prompt")),
                "input_count": len(payload.get("images") or []),
                "request": _serialize_request(payload),
                "created_at": now,
                "updated_at": now,
            }
            self._tasks[key] = task
            self._save_locked()

        self._start_task_thread(key, mode, payload, dict(identity), _clean(payload.get("model"), "gpt-image-2"))
        return _public_task(task)

    def _start_task_thread(
        self,
        key: str,
        mode: str,
        payload: dict[str, Any],
        identity: dict[str, object],
        model: str,
    ) -> None:
        thread = threading.Thread(
            target=self._run_task,
            args=(key, mode, payload, identity, model),
            name=f"image-task-{_clean(key).split(':', 1)[-1][:16]}",
            daemon=True,
        )
        thread.start()

    def _run_task(
        self,
        key: str,
        mode: str,
        payload: dict[str, Any],
        identity: dict[str, object],
        model: str,
    ) -> None:
        started = time.time()
        if not self._mark_task_running(key):
            return
        try:
            handler = self.edit_handler if mode == "edit" else self.generation_handler
            result = handler(payload)
            if not isinstance(result, dict):
                raise RuntimeError("image task returned streaming result unexpectedly")
            data = result.get("data")
            if not isinstance(data, list) or not data:
                upstream = _clean(result.get("message"))
                if upstream:
                    message = upstream
                else:
                    message = "号池中没有可用账号或所有账号均被限流，请检查号池状态（账号额度、是否被封禁、是否到达生图上限）"
                raise RuntimeError(message)
            if not self._finish_task(key, status=TASK_STATUS_SUCCESS, data=data, error=""):
                return
            self._log_call(
                identity,
                mode,
                model,
                started,
                "调用完成",
                request_preview=request_text(payload.get("prompt")),
                urls=_collect_image_urls(data),
            )
        except Exception as exc:
            error_message = str(exc) or "image task failed"
            if not self._finish_task(key, status=TASK_STATUS_ERROR, error=error_message, data=[]):
                return
            self._log_call(
                identity,
                mode,
                model,
                started,
                "调用失败",
                request_preview=request_text(payload.get("prompt")),
                status="failed",
                error=error_message,
            )

    def _log_call(
        self,
        identity: dict[str, object],
        mode: str,
        model: str,
        started: float,
        suffix: str,
        *,
        request_preview: str = "",
        status: str = "success",
        error: str = "",
        urls: list[str] | None = None,
    ) -> None:
        endpoint = "/v1/images/edits" if mode == "edit" else "/v1/images/generations"
        summary_prefix = "图生图" if mode == "edit" else "文生图"
        detail = {
            "key_id": identity.get("id"),
            "key_name": identity.get("name"),
            "role": identity.get("role"),
            "endpoint": endpoint,
            "model": model,
            "started_at": datetime.fromtimestamp(started).strftime("%Y-%m-%d %H:%M:%S"),
            "ended_at": _now_iso(),
            "duration_ms": int((time.time() - started) * 1000),
            "status": status,
        }
        if request_preview:
            detail["request_text"] = request_preview
        if error:
            detail["error"] = error
        if urls:
            detail["urls"] = list(dict.fromkeys(urls))
        try:
            log_service.add(LOG_TYPE_CALL, f"{summary_prefix}{suffix}", detail)
        except Exception:
            pass

    def _mark_task_running(self, key: str) -> bool:
        with self._lock:
            task = self._tasks.get(key)
            if task is None or _clean(task.get("status")) != TASK_STATUS_QUEUED:
                return False
            task["status"] = TASK_STATUS_RUNNING
            task["error"] = ""
            task["updated_at"] = _now_iso()
            self._save_locked()
            return True

    def _finish_task(self, key: str, **updates: Any) -> bool:
        with self._lock:
            task = self._tasks.get(key)
            if task is None or _clean(task.get("status")) != TASK_STATUS_RUNNING:
                return False
            task.update(updates)
            task["updated_at"] = _now_iso()
            self._save_locked()
            return True

    def _load_locked(self) -> dict[str, dict[str, Any]]:
        if not self.path.exists():
            return {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        raw_items = raw.get("tasks") if isinstance(raw, dict) else raw
        if not isinstance(raw_items, list):
            return {}
        tasks: dict[str, dict[str, Any]] = {}
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            task_id = _clean(item.get("id"))
            owner = _clean(item.get("owner_id"))
            if not task_id or not owner:
                continue
            status = _clean(item.get("status"))
            if status not in {TASK_STATUS_QUEUED, TASK_STATUS_RUNNING, TASK_STATUS_SUCCESS, TASK_STATUS_ERROR, TASK_STATUS_CANCELLED}:
                status = TASK_STATUS_ERROR
            mode = "edit" if item.get("mode") == "edit" else "generate"
            model = _clean(item.get("model"), "gpt-image-2")
            size = _clean(item.get("size"))
            prompt = _clean(item.get("prompt"))
            task = {
                "key": _clean(item.get("key")) or _task_key(owner, task_id),
                "id": task_id,
                "owner_id": owner,
                "owner_name": _clean(item.get("owner_name"), owner),
                "owner_role": _clean(item.get("owner_role"), "user") or "user",
                "status": status,
                "mode": mode,
                "model": model,
                "size": size,
                "prompt": prompt,
                "request": _normalize_request(item.get("request"), mode, prompt=prompt, model=model, size=size),
                "input_count": int(item.get("input_count") or 0),
                "created_at": _clean(item.get("created_at"), _now_iso()),
                "updated_at": _clean(item.get("updated_at"), _clean(item.get("created_at"), _now_iso())),
            }
            if mode == "edit":
                task["input_count"] = len(task["request"].get("images") or [])
            data = item.get("data")
            if isinstance(data, list):
                task["data"] = data
            error = _clean(item.get("error"))
            if error:
                task["error"] = error
            tasks[_clean(task["key"])] = task
        return tasks

    def _save_locked(self) -> None:
        items = sorted(self._tasks.values(), key=lambda item: str(item.get("updated_at") or ""), reverse=True)
        tmp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp_path.write_text(json.dumps({"tasks": items}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp_path.replace(self.path)

    def _recover_unfinished_locked(self) -> bool:
        changed = False
        for task in self._tasks.values():
            status = _clean(task.get("status"))
            if status == TASK_STATUS_RUNNING:
                task["status"] = TASK_STATUS_ERROR
                task["error"] = "服务已重启，运行中的图片任务已中断"
                task["updated_at"] = _now_iso()
                changed = True
            elif status == TASK_STATUS_QUEUED:
                task["status"] = TASK_STATUS_ERROR
                task["error"] = "服务已重启，排队中的图片任务未继续执行"
                task["updated_at"] = _now_iso()
                changed = True
        return changed

    def _cleanup_locked(self) -> bool:
        try:
            retention_days = max(1, int(self.retention_days_getter()))
        except Exception:
            retention_days = 30
        cutoff = time.time() - retention_days * 86400
        removed_keys = [
            key
            for key, task in self._tasks.items()
            if task.get("status") in TERMINAL_STATUSES and _timestamp(task.get("updated_at")) < cutoff
        ]
        for key in removed_keys:
            self._tasks.pop(key, None)
        return bool(removed_keys)


image_task_service = ImageTaskService(DATA_DIR / "image_tasks.json")
