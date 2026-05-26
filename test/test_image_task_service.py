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

    def test_startup_does_not_affect_cancelled_tasks(self):
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
            result = service.list_tasks(OWNER, ["cancelled-task", "running-task"])

            cancelled = next((t for t in result["items"] if t["id"] == "cancelled-task"), None)
            running = next((t for t in result["items"] if t["id"] == "running-task"), None)
            self.assertIsNotNone(cancelled)
            self.assertEqual(cancelled["status"], "cancelled")
            self.assertIsNotNone(running)
            self.assertEqual(running["status"], "error")
            self.assertIn("已中断", running.get("error", ""))

    def test_cancel_queued_task(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "image_tasks.json"
            barrier = [False]

            def slow_handler(_payload):
                while not barrier[0]:
                    time.sleep(0.01)
                return {"data": [{"url": "http://example.test/image.png"}]}

            service = self.make_service(path, handler=slow_handler)
            service.submit_generation(
                OWNER,
                client_task_id="cancel-queued",
                prompt="cat",
                model="gpt-image-2",
                size=None,
                base_url="http://local.test",
            )
            result = service.list_tasks(OWNER, ["cancel-queued"])
            self.assertEqual(result["items"][0]["status"], "queued")

            cancelled = service.cancel_task(OWNER, client_task_id="cancel-queued")
            self.assertEqual(cancelled["status"], "cancelled")
            self.assertIn("已取消", cancelled.get("error", ""))

            barrier[0] = True
            time.sleep(0.2)
            result = service.list_tasks(OWNER, ["cancel-queued"])
            self.assertEqual(result["items"][0]["status"], "cancelled")

    def test_cancel_running_task(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "image_tasks.json"
            started = [False]
            done = [False]

            def slow_handler(_payload):
                started[0] = True
                while not done[0]:
                    time.sleep(0.01)
                return {"data": [{"url": "http://example.test/image.png"}]}

            service = self.make_service(path, handler=slow_handler)
            service.submit_generation(
                OWNER,
                client_task_id="cancel-running",
                prompt="dog",
                model="gpt-image-2",
                size=None,
                base_url="http://local.test",
            )

            deadline = time.time() + 2.0
            while time.time() < deadline and not started[0]:
                time.sleep(0.02)
            self.assertTrue(started[0], "task did not start running")

            cancelled = service.cancel_task(OWNER, client_task_id="cancel-running")
            self.assertEqual(cancelled["status"], "cancelled")
            self.assertIn("已取消", cancelled.get("error", ""))

            done[0] = True
            time.sleep(0.2)
            result = service.list_tasks(OWNER, ["cancel-running"])
            self.assertEqual(result["items"][0]["status"], "cancelled")

    def test_cancel_non_existent_task_raises(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = self.make_service(Path(tmp_dir) / "image_tasks.json")
            with self.assertRaises(ValueError):
                service.cancel_task(OWNER, client_task_id="nonexistent")

    def test_cancel_already_terminal_task_raises(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = self.make_service(Path(tmp_dir) / "image_tasks.json")
            service.submit_generation(
                OWNER,
                client_task_id="terminal",
                prompt="cat",
                model="gpt-image-2",
                size=None,
                base_url="http://local.test",
            )
            wait_for_task(service, OWNER, "terminal", "success")

            with self.assertRaises(ValueError):
                service.cancel_task(OWNER, client_task_id="terminal")

    def test_retry_error_task(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "image_tasks.json"

            def fail_once(_payload):
                raise RuntimeError("mock image failure")

            service = self.make_service(path, handler=fail_once)
            service.submit_generation(
                OWNER,
                client_task_id="retry-me",
                prompt="cat",
                model="gpt-image-2",
                size="1024x1024",
                base_url="http://local.test",
            )
            task = wait_for_task(service, OWNER, "retry-me", "error")
            self.assertEqual(task["status"], "error")
            self.assertIn("mock image failure", task.get("error", ""))

            retried = service.retry_task(
                OWNER,
                client_task_id="retry-me",
                base_url="http://local.test",
            )
            self.assertEqual(retried["status"], "queued")
            self.assertEqual(retried["mode"], "generate")
            self.assertEqual(retried["model"], "gpt-image-2")
            self.assertEqual(retried["size"], "1024x1024")
            self.assertEqual(retried["prompt"], "cat")

            wait_for_task(service, OWNER, "retry-me", "error")

    def test_retry_non_error_task_raises(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = self.make_service(Path(tmp_dir) / "image_tasks.json")
            service.submit_generation(
                OWNER,
                client_task_id="success-task",
                prompt="cat",
                model="gpt-image-2",
                size=None,
                base_url="http://local.test",
            )
            wait_for_task(service, OWNER, "success-task", "success")

            with self.assertRaises(ValueError):
                service.retry_task(
                    OWNER,
                    client_task_id="success-task",
                    base_url="http://local.test",
                )

    def test_retry_prompt_is_preserved_in_storage(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "image_tasks.json"

            def fail_handler(_payload):
                raise RuntimeError("fail for retry")

            service = self.make_service(path, handler=fail_handler)
            service.submit_generation(
                OWNER,
                client_task_id="prompt-test",
                prompt="a beautiful sunset over the ocean",
                model="gpt-image-2",
                size="512x512",
                base_url="http://local.test",
            )
            task = wait_for_task(service, OWNER, "prompt-test", "error")
            self.assertEqual(task["prompt"], "a beautiful sunset over the ocean")

            reloaded = self.make_service(path)
            result = reloaded.list_tasks(OWNER, ["prompt-test"])
            self.assertEqual(result["items"][0]["prompt"], "a beautiful sunset over the ocean")

    def test_cancelled_task_persists_to_new_service_instance(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "image_tasks.json"
            barrier = [False]

            def slow_handler(_payload):
                while not barrier[0]:
                    time.sleep(0.01)
                return {"data": [{"url": "http://example.test/image.png"}]}

            service = self.make_service(path, handler=slow_handler)
            service.submit_generation(
                OWNER,
                client_task_id="persist-cancel",
                prompt="cat",
                model="gpt-image-2",
                size=None,
                base_url="http://local.test",
            )
            service.cancel_task(OWNER, client_task_id="persist-cancel")
            barrier[0] = True
            time.sleep(0.2)

            reloaded = self.make_service(path)
            result = reloaded.list_tasks(OWNER, ["persist-cancel"])
            self.assertEqual(result["items"][0]["status"], "cancelled")
            self.assertIn("已取消", result["items"][0].get("error", ""))


if __name__ == "__main__":
    unittest.main()
