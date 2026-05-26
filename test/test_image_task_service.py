from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path

from services.image_task_service import ImageTaskService


OWNER = {"id": "owner-1", "name": "Owner", "role": "admin"}
OTHER_OWNER = {"id": "owner-2", "name": "Other", "role": "user"}


def wait_for_task(service: ImageTaskService, identity: dict[str, object], task_id: str, status: str, timeout: float = 2.0):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        result = service.list_tasks(identity, [task_id])
        last = (result.get("items") or [None])[0]
        if last and last.get("status") == status:
            return last
        time.sleep(0.02)
    raise AssertionError(f"task {task_id} did not reach {status}, last={last}")


class ImageTaskServiceTests(unittest.TestCase):
    def make_service(self, path: Path, handler=None) -> ImageTaskService:
        return ImageTaskService(
            path,
            generation_handler=handler or (lambda _payload: {"data": [{"url": "http://example.test/image.png"}]}),
            edit_handler=handler or (lambda _payload: {"data": [{"url": "http://example.test/edit.png"}]}),
            retention_days_getter=lambda: 30,
        )

    def test_duplicate_submit_uses_existing_task(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            calls = 0

            def handler(_payload):
                nonlocal calls
                calls += 1
                time.sleep(0.05)
                return {"data": [{"url": "http://example.test/image.png"}]}

            service = self.make_service(Path(tmp_dir) / "image_tasks.json", handler)
            first = service.submit_generation(
                OWNER,
                client_task_id="task-1",
                prompt="cat",
                model="gpt-image-2",
                size=None,
                base_url="http://local.test",
            )
            second = service.submit_generation(
                OWNER,
                client_task_id="task-1",
                prompt="cat",
                model="gpt-image-2",
                size=None,
                base_url="http://local.test",
            )

            self.assertEqual(first["id"], "task-1")
            self.assertEqual(second["id"], "task-1")
            task = wait_for_task(service, OWNER, "task-1", "success")
            self.assertEqual(task["data"][0]["url"], "http://example.test/image.png")
            self.assertEqual(calls, 1)

    def test_different_owner_cannot_query_task(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = self.make_service(Path(tmp_dir) / "image_tasks.json")
            service.submit_generation(
                OWNER,
                client_task_id="private-task",
                prompt="cat",
                model="gpt-image-2",
                size=None,
                base_url="http://local.test",
            )

            wait_for_task(service, OWNER, "private-task", "success")
            result = service.list_tasks(OTHER_OWNER, ["private-task"])

            self.assertEqual(result["items"], [])
            self.assertEqual(result["missing_ids"], ["private-task"])

    def test_success_task_persists_to_new_service_instance(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "image_tasks.json"
            service = self.make_service(path)
            service.submit_generation(
                OWNER,
                client_task_id="persisted-task",
                prompt="cat",
                model="gpt-image-2",
                size=None,
                base_url="http://local.test",
            )
            wait_for_task(service, OWNER, "persisted-task", "success")

            reloaded = self.make_service(path)
            result = reloaded.list_tasks(OWNER, ["persisted-task"])

            self.assertEqual(result["missing_ids"], [])
            self.assertEqual(result["items"][0]["status"], "success")
            self.assertEqual(result["items"][0]["data"][0]["url"], "http://example.test/image.png")

    def test_startup_marks_unfinished_tasks_as_error(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "image_tasks.json"
            path.write_text(
                json.dumps(
                    {
                        "tasks": [
                            {
                                "id": "queued-task",
                                "owner_id": "owner-1",
                                "status": "queued",
                                "mode": "generate",
                                "model": "gpt-image-2",
                                "created_at": "2099-01-01 00:00:00",
                                "updated_at": "2099-01-01 00:00:00",
                            },
                            {
                                "id": "running-task",
                                "owner_id": "owner-1",
                                "status": "running",
                                "mode": "generate",
                                "model": "gpt-image-2",
                                "created_at": "2099-01-01 00:00:00",
                                "updated_at": "2099-01-01 00:00:00",
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )

            service = self.make_service(path)
            result = service.list_tasks(OWNER, ["queued-task", "running-task"])

            self.assertEqual([item["status"] for item in result["items"]], ["error", "error"])
            self.assertTrue(all("已中断" in item.get("error", "") for item in result["items"]))

    def test_cancel_queued_task(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "image_tasks.json"

            def slow_handler(_payload):
                time.sleep(5)
                return {"data": [{"url": "http://example.test/image.png"}]}

            service = self.make_service(path, handler=slow_handler)
            service.submit_generation(
                OWNER,
                client_task_id="cancel-queued",
                prompt="cancel me",
                model="gpt-image-2",
                size=None,
                base_url="http://local.test",
            )

            result = service.cancel_task(OWNER, "cancel-queued")
            self.assertEqual(result["status"], "cancelled")
            self.assertEqual(result["error"], "任务已取消")

            list_result = service.list_tasks(OWNER, ["cancel-queued"])
            self.assertEqual(list_result["items"][0]["status"], "cancelled")

    def test_cancel_running_task(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "image_tasks.json"

            def slow_handler(_payload):
                time.sleep(5)
                return {"data": [{"url": "http://example.test/image.png"}]}

            service = self.make_service(path, handler=slow_handler)
            service.submit_generation(
                OWNER,
                client_task_id="cancel-running",
                prompt="cancel me running",
                model="gpt-image-2",
                size=None,
                base_url="http://local.test",
            )
            wait_for_task(service, OWNER, "cancel-running", "running")

            result = service.cancel_task(OWNER, "cancel-running")
            self.assertEqual(result["status"], "cancelled")

            list_result = service.list_tasks(OWNER, ["cancel-running"])
            self.assertEqual(list_result["items"][0]["status"], "cancelled")

    def test_cannot_cancel_success_task(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = self.make_service(Path(tmp_dir) / "image_tasks.json")
            service.submit_generation(
                OWNER,
                client_task_id="done-task",
                prompt="done",
                model="gpt-image-2",
                size=None,
                base_url="http://local.test",
            )
            wait_for_task(service, OWNER, "done-task", "success")

            with self.assertRaises(ValueError) as ctx:
                service.cancel_task(OWNER, "done-task")
            self.assertIn("cannot cancel task in success status", str(ctx.exception))

    def test_cannot_cancel_nonexistent_task(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = self.make_service(Path(tmp_dir) / "image_tasks.json")
            with self.assertRaises(ValueError) as ctx:
                service.cancel_task(OWNER, "nonexistent")
            self.assertIn("task not found", str(ctx.exception))

    def test_retry_error_task(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "image_tasks.json"
            call_count = 0

            def flaky_handler(_payload):
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    raise RuntimeError("first attempt fails")
                return {"data": [{"url": "http://example.test/retry.png"}]}

            service = self.make_service(path, handler=flaky_handler)
            service.submit_generation(
                OWNER,
                client_task_id="retry-error",
                prompt="retry me",
                model="gpt-image-2",
                size=None,
                base_url="http://local.test",
            )
            wait_for_task(service, OWNER, "retry-error", "error")

            result = service.retry_task(OWNER, "retry-error", "http://local.test")
            self.assertEqual(result["status"], "queued")

            final = wait_for_task(service, OWNER, "retry-error", "success")
            self.assertEqual(final["data"][0]["url"], "http://example.test/retry.png")
            self.assertEqual(call_count, 2)

    def test_retry_cancelled_task(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "image_tasks.json"

            def slow_handler(_payload):
                time.sleep(5)
                return {"data": [{"url": "http://example.test/image.png"}]}

            service = self.make_service(path, handler=slow_handler)
            service.submit_generation(
                OWNER,
                client_task_id="retry-cancelled",
                prompt="retry after cancel",
                model="gpt-image-2",
                size=None,
                base_url="http://local.test",
            )
            service.cancel_task(OWNER, "retry-cancelled")

            result = service.retry_task(OWNER, "retry-cancelled", "http://local.test")
            self.assertEqual(result["status"], "queued")

    def test_cannot_retry_running_task(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "image_tasks.json"

            def slow_handler(_payload):
                time.sleep(5)
                return {"data": [{"url": "http://example.test/image.png"}]}

            service = self.make_service(path, handler=slow_handler)
            service.submit_generation(
                OWNER,
                client_task_id="no-retry-running",
                prompt="running",
                model="gpt-image-2",
                size=None,
                base_url="http://local.test",
            )
            wait_for_task(service, OWNER, "no-retry-running", "running")

            with self.assertRaises(ValueError) as ctx:
                service.retry_task(OWNER, "no-retry-running", "http://local.test")
            self.assertIn("cannot retry task in running status", str(ctx.exception))

    def test_cancelled_task_persists_across_restarts(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "image_tasks.json"

            def slow_handler(_payload):
                time.sleep(5)
                return {"data": [{"url": "http://example.test/image.png"}]}

            service = self.make_service(path, handler=slow_handler)
            service.submit_generation(
                OWNER,
                client_task_id="persist-cancelled",
                prompt="cancel persist",
                model="gpt-image-2",
                size=None,
                base_url="http://local.test",
            )
            service.cancel_task(OWNER, "persist-cancelled")

            reloaded = self.make_service(path)
            result = reloaded.list_tasks(OWNER, ["persist-cancelled"])
            self.assertEqual(result["items"][0]["status"], "cancelled")

    def test_cancelled_not_recovered_on_startup(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "image_tasks.json"
            path.write_text(
                json.dumps(
                    {
                        "tasks": [
                            {
                                "id": "cancelled-task",
                                "owner_id": "owner-1",
                                "status": "cancelled",
                                "mode": "generate",
                                "model": "gpt-image-2",
                                "prompt": "test prompt",
                                "created_at": "2099-01-01 00:00:00",
                                "updated_at": "2099-01-01 00:00:00",
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )

            service = self.make_service(path)
            result = service.list_tasks(OWNER, ["cancelled-task"])

            self.assertEqual(result["items"][0]["status"], "cancelled")
            self.assertNotIn("已中断", result["items"][0].get("error", ""))

    def test_task_stores_prompt(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = self.make_service(Path(tmp_dir) / "image_tasks.json")
            service.submit_generation(
                OWNER,
                client_task_id="prompt-task",
                prompt="a beautiful sunset",
                model="gpt-image-2",
                size="1024x1024",
                base_url="http://local.test",
            )
            wait_for_task(service, OWNER, "prompt-task", "success")

            result = service.list_tasks(OWNER, ["prompt-task"])
            self.assertEqual(result["items"][0].get("prompt"), "a beautiful sunset")


if __name__ == "__main__":
    unittest.main()
