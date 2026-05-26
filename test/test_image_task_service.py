from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path

from services.image_task_service import ImageTaskService, TASK_STATUS_CANCELLED


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
                                "prompt": "test",
                                "base_url": "http://test",
                                "created_at": "2099-01-01 00:00:00",
                                "updated_at": "2099-01-01 00:00:00",
                            },
                            {
                                "id": "running-task",
                                "owner_id": "owner-1",
                                "status": "running",
                                "mode": "generate",
                                "model": "gpt-image-2",
                                "prompt": "test",
                                "base_url": "http://test",
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

    def test_cancel_task_marks_as_cancelled(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "image_tasks.json"
            service = self.make_service(path)
            
            service.submit_generation(
                OWNER,
                client_task_id="cancel-test",
                prompt="cancel me",
                model="gpt-image-2",
                size=None,
                base_url="http://local.test",
            )
            
            # 立即取消
            cancelled = service.cancel_task(OWNER, "cancel-test")
            self.assertEqual(cancelled["status"], TASK_STATUS_CANCELLED)
            
            result = service.list_tasks(OWNER, ["cancel-test"])
            self.assertEqual(result["items"][0]["status"], TASK_STATUS_CANCELLED)
    
    def test_retry_failed_task(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "image_tasks.json"
            
            calls = 0
            def failing_then_succeeding(_payload):
                nonlocal calls
                calls += 1
                if calls == 1:
                    raise ValueError("first attempt fails")
                return {"data": [{"url": "http://example.test/retry.png"}]}
            
            service = self.make_service(path, failing_then_succeeding)
            
            # 第一次提交应该失败
            service.submit_generation(
                OWNER,
                client_task_id="retry-test",
                prompt="retry me",
                model="gpt-image-2",
                size=None,
                base_url="http://local.test",
            )
            wait_for_task(service, OWNER, "retry-test", "error")
            self.assertEqual(calls, 1)
            
            # 重试任务
            retried = service.retry_task(OWNER, "retry-test")
            self.assertEqual(retried["status"], "queued")
            
            # 等待第二次尝试成功
            task = wait_for_task(service, OWNER, "retry-test", "success")
            self.assertEqual(task["data"][0]["url"], "http://example.test/retry.png")
            self.assertEqual(calls, 2)
    
    def test_cannot_retry_running_task(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "image_tasks.json"
            
            def slow_handler(_payload):
                time.sleep(0.5)
                return {"data": [{"url": "http://example.test/image.png"}]}
            
            service = self.make_service(path, slow_handler)
            
            service.submit_generation(
                OWNER,
                client_task_id="no-retry-running",
                prompt="test",
                model="gpt-image-2",
                size=None,
                base_url="http://local.test",
            )
            
            with self.assertRaises(ValueError):
                service.retry_task(OWNER, "no-retry-running")
    
    def test_cannot_cancel_already_completed_task(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "image_tasks.json"
            service = self.make_service(path)
            
            service.submit_generation(
                OWNER,
                client_task_id="no-cancel-complete",
                prompt="test",
                model="gpt-image-2",
                size=None,
                base_url="http://local.test",
            )
            wait_for_task(service, OWNER, "no-cancel-complete", "success")
            
            with self.assertRaises(ValueError):
                service.cancel_task(OWNER, "no-cancel-complete")
    
    def test_task_persists_cancelled_status(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "image_tasks.json"
            service = self.make_service(path)
            
            service.submit_generation(
                OWNER,
                client_task_id="persist-cancel",
                prompt="test",
                model="gpt-image-2",
                size=None,
                base_url="http://local.test",
            )
            service.cancel_task(OWNER, "persist-cancel")
            
            reloaded = self.make_service(path)
            result = reloaded.list_tasks(OWNER, ["persist-cancel"])
            
            self.assertEqual(result["items"][0]["status"], TASK_STATUS_CANCELLED)
    
    def test_list_all_tasks_for_admin(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "image_tasks.json"
            service = self.make_service(path)
            
            service.submit_generation(
                OWNER,
                client_task_id="task-1",
                prompt="test 1",
                model="gpt-image-2",
                size=None,
                base_url="http://local.test",
            )
            service.submit_generation(
                OTHER_OWNER,
                client_task_id="task-2",
                prompt="test 2",
                model="gpt-image-2",
                size=None,
                base_url="http://local.test",
            )
            
            all_tasks = service.list_all_tasks(OWNER)
            self.assertEqual(len(all_tasks["items"]), 2)


if __name__ == "__main__":
    unittest.main()
