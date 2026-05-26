from __future__ import annotations

import base64
import unittest
from unittest import mock

from test.optional_stubs import install_curl_cffi_stub, install_fastapi_stubs, install_pil_stub, install_pybase64_stub, install_pydantic_stub, install_starlette_stub, install_tiktoken_stub

install_curl_cffi_stub()
install_fastapi_stubs()
install_pil_stub()
install_pybase64_stub()
install_pydantic_stub()
install_starlette_stub()
install_tiktoken_stub()

from fastapi import FastAPI
from fastapi.testclient import TestClient

import api.image_tasks as image_tasks_module


AUTH_HEADERS = {"Authorization": "Bearer webchat2api"}
PNG_BYTES = b"\x89PNG\r\n\x1a\n"
DATA_IMAGE_URL = f"data:image/png;base64,{base64.b64encode(PNG_BYTES).decode('ascii')}"


class FakeImageTaskService:
    def __init__(self):
        self.generation_calls = []
        self.edit_calls = []
        self.cancel_calls = []
        self.retry_calls = []

    def submit_generation(self, identity, **kwargs):
        self.generation_calls.append((identity, kwargs))
        return {
            "key": f"{identity['id']}:{kwargs['client_task_id']}",
            "id": kwargs["client_task_id"],
            "owner_id": identity["id"],
            "owner_name": identity["name"],
            "status": "success",
            "mode": "generate",
            "prompt": kwargs["prompt"],
            "created_at": "2026-01-01 00:00:00",
            "updated_at": "2026-01-01 00:00:00",
            "data": [{"url": f"{kwargs['base_url']}/images/fake.png"}],
        }

    def submit_edit(self, identity, **kwargs):
        self.edit_calls.append((identity, kwargs))
        return {
            "key": f"{identity['id']}:{kwargs['client_task_id']}",
            "id": kwargs["client_task_id"],
            "owner_id": identity["id"],
            "owner_name": identity["name"],
            "status": "queued",
            "mode": "edit",
            "prompt": kwargs["prompt"],
            "input_count": len(kwargs["images"]),
            "created_at": "2026-01-01 00:00:00",
            "updated_at": "2026-01-01 00:00:00",
        }

    def list_tasks(self, identity, ids):
        if not ids and identity.get("role") == "admin":
            return {
                "items": [
                    {
                        "key": "owner-1:task-1",
                        "id": "task-1",
                        "owner_id": "owner-1",
                        "owner_name": "Owner",
                        "status": "running",
                        "mode": "generate",
                        "prompt": "cat",
                        "created_at": "2026-01-01 00:00:00",
                        "updated_at": "2026-01-01 00:00:00",
                    },
                    {
                        "key": "owner-2:task-2",
                        "id": "task-2",
                        "owner_id": "owner-2",
                        "owner_name": "Other",
                        "status": "error",
                        "mode": "edit",
                        "prompt": "fix image",
                        "input_count": 2,
                        "error": "boom",
                        "created_at": "2026-01-01 00:00:00",
                        "updated_at": "2026-01-01 00:00:00",
                    },
                ],
                "missing_ids": [],
            }
        return {
            "items": [
                {
                    "key": f"owner-1:{task_id}",
                    "id": task_id,
                    "owner_id": "owner-1",
                    "owner_name": "Owner",
                    "status": "success",
                    "mode": "generate",
                    "prompt": "cat",
                    "created_at": "2026-01-01 00:00:00",
                    "updated_at": "2026-01-01 00:00:00",
                    "data": [{"url": "http://testserver/images/fake.png"}],
                }
                for task_id in ids
                if task_id != "missing"
            ],
            "missing_ids": [task_id for task_id in ids if task_id == "missing"],
        }

    def cancel_task(self, identity, key):
        self.cancel_calls.append((identity, key))
        return {
            "key": key,
            "id": key.split(":", 1)[1],
            "owner_id": key.split(":", 1)[0],
            "owner_name": "Owner",
            "status": "cancelled",
            "mode": "generate",
            "prompt": "cat",
            "error": "任务已取消",
            "created_at": "2026-01-01 00:00:00",
            "updated_at": "2026-01-01 00:00:00",
        }

    def retry_task(self, identity, key, *, base_url):
        self.retry_calls.append((identity, key, base_url))
        return {
            "key": key,
            "id": key.split(":", 1)[1],
            "owner_id": key.split(":", 1)[0],
            "owner_name": "Owner",
            "status": "queued",
            "mode": "edit",
            "prompt": "fix image",
            "input_count": 2,
            "created_at": "2026-01-01 00:00:00",
            "updated_at": "2026-01-01 00:00:00",
        }


class ImageTasksApiTests(unittest.TestCase):
    def setUp(self):
        self.fake_service = FakeImageTaskService()
        self.service_patcher = mock.patch.object(image_tasks_module, "image_task_service", self.fake_service)
        self.service_patcher.start()
        self.addCleanup(self.service_patcher.stop)
        app = FastAPI()
        app.include_router(image_tasks_module.create_router())
        self.client = TestClient(app)

    def test_create_generation_task(self):
        response = self.client.post(
            "/api/image-tasks/generations",
            headers=AUTH_HEADERS,
            json={"client_task_id": "task-1", "prompt": "cat", "model": "gpt-image-2"},
        )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["id"], "task-1")
        self.assertEqual(payload["status"], "success")
        self.assertEqual(len(self.fake_service.generation_calls), 1)

    def test_create_edit_task_accepts_multiple_images(self):
        response = self.client.post(
            "/api/image-tasks/edits",
            headers=AUTH_HEADERS,
            data={"client_task_id": "edit-1", "prompt": "edit", "model": "gpt-image-2"},
            files=[
                ("image", ("one.png", b"one", "image/png")),
                ("image", ("two.png", b"two", "image/png")),
            ],
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["id"], "edit-1")
        self.assertEqual(len(self.fake_service.edit_calls), 1)
        images = self.fake_service.edit_calls[0][1]["images"]
        self.assertEqual(len(images), 2)

    def test_create_edit_task_accepts_image_url(self):
        response = self.client.post(
            "/api/image-tasks/edits",
            headers=AUTH_HEADERS,
            data={
                "client_task_id": "edit-url-1",
                "prompt": "edit",
                "model": "gpt-image-2",
                "image_url": DATA_IMAGE_URL,
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(self.fake_service.edit_calls), 1)
        images = self.fake_service.edit_calls[0][1]["images"]
        self.assertEqual(images, [(PNG_BYTES, "image_url.png", "image/png")])

    def test_list_tasks_reports_missing_ids(self):
        response = self.client.get("/api/image-tasks?ids=task-1,missing", headers=AUTH_HEADERS)

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual([item["id"] for item in payload["items"]], ["task-1"])
        self.assertEqual(payload["missing_ids"], ["missing"])

    def test_list_tasks_returns_all_tasks_for_admin_without_ids(self):
        response = self.client.get("/api/image-tasks", headers=AUTH_HEADERS)

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual([item["key"] for item in payload["items"]], ["owner-1:task-1", "owner-2:task-2"])
        self.assertEqual([item["status"] for item in payload["items"]], ["running", "error"])

    def test_cancel_image_task(self):
        response = self.client.post("/api/image-tasks/cancel", headers=AUTH_HEADERS, json={"key": "owner-1:task-1"})

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["status"], "cancelled")
        self.assertEqual(self.fake_service.cancel_calls[0][1], "owner-1:task-1")

    def test_retry_image_task_uses_current_base_url(self):
        response = self.client.post("/api/image-tasks/retry", headers=AUTH_HEADERS, json={"key": "owner-2:task-2"})

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["status"], "queued")
        self.assertEqual(self.fake_service.retry_calls[0][1], "owner-2:task-2")
        self.assertEqual(self.fake_service.retry_calls[0][2], "http://testserver")


if __name__ == "__main__":
    unittest.main()
