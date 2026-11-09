"""Offline menu policy, persistence snapshot, API validation and authorization tests."""
import asyncio
import importlib.util
import sys
import types
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from sqlalchemy.orm import declarative_base

ROOT = Path(__file__).resolve().parents[1]


def load_file(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


policy = load_file("menu_policy_test", "app/core/menu_policy.py")
fake_models = types.ModuleType("app.models")
fake_models.Base = declarative_base()
fake_models.async_session = None
with patch.dict(sys.modules, {"app.models": fake_models}):
    model = load_file("menu_model_test", "app/models/menu_permission.py")


class FakeOperationLog:
    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs


op_module = types.ModuleType("app.models.operation_log")
op_module.ArgusOperationLog = FakeOperationLog
fake_enum = types.ModuleType("app.enums.OperationEnum")
fake_enum.OperationType = types.SimpleNamespace(UPDATE=1)
with patch.dict(sys.modules, {
    "app.models": fake_models,
    "app.models.menu_permission": model,
    "app.models.operation_log": op_module,
    "app.enums.OperationEnum": fake_enum,
    "app.core.menu_policy": policy,
}):
    service = load_file("menu_service_test", "app/core/menu_permission.py")


class FakeSession:
    def __init__(self):
        self.execute = AsyncMock()
        self.added = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    def begin(self):
        return self

    def add(self, item):
        self.added.append(item)


class Permission:
    def __init__(self, role=0):
        self.role = role

    async def __call__(self, token: str = __import__("fastapi").Header(...)):
        role = int(token)
        if role < self.role:
            from fastapi import HTTPException
            raise HTTPException(403)
        return {"id": 10, "role": role}


class Response:
    @staticmethod
    def success(data=None, code=0, msg=""):
        return {"data": data, "code": code, "msg": msg}

    @staticmethod
    def failed(msg, code=110):
        return {"code": code, "msg": msg}

    @staticmethod
    def forbidden():
        return {"code": 403}


def module_stub(name, **attrs):
    module = types.ModuleType(name)
    module.__dict__.update(attrs)
    return module


with patch.dict(sys.modules, {
    "app.core.menu_permission": service,
    "app.handler.fatcory": module_stub("response", ArgusResponse=Response),
    "app.routers": module_stub("routers", Permission=Permission),
    "config": module_stub("config", Config=types.SimpleNamespace(ADMIN=2)),
}):
    router = load_file("menu_router_test", "app/routers/menu.py")


class MenuPolicyTests(unittest.TestCase):
    def test_default_roles_keep_existing_menu_ranges(self):
        self.assertEqual(policy.default_paths(0), policy.default_paths(1))
        self.assertNotIn("/system/menu", policy.default_paths(0))
        self.assertIn("/system/menu", policy.default_paths(2))

    def test_group_selection_expands_to_leaves(self):
        self.assertEqual(policy.normalize_paths(0, ["/asset"]), [
            "/asset/interface", "/asset/mock", "/asset/record",
        ])

    def test_regular_role_can_clear_all(self):
        self.assertEqual(policy.normalize_paths(0, []), [])

    def test_admin_cannot_remove_recovery_entry(self):
        self.assertEqual(policy.normalize_paths(2, []), ["/system/menu"])

    def test_reject_unknown_and_admin_only_paths(self):
        for role, paths in [(0, ["/unknown"]), (0, ["/system"]), (1, ["/system/user"]), (3, [])]:
            with self.subTest(role=role, paths=paths), self.assertRaises(ValueError):
                policy.normalize_paths(role, paths)

    def test_single_child_does_not_grant_siblings(self):
        self.assertEqual(policy.normalize_paths(0, ["/asset/mock"]), ["/asset/mock"])

    def test_snapshot_distinguishes_unsaved_and_saved_empty(self):
        self.assertTrue(service.snapshot(0)["paths"])
        row = types.SimpleNamespace(paths=[], updated_at=datetime.now())
        saved = service.snapshot(0, row)
        self.assertTrue(saved["configured"])
        self.assertEqual(saved["paths"], [])

    def test_snapshot_ignores_removed_menu(self):
        row = types.SimpleNamespace(paths=["/removed", "/project"], updated_at=datetime.now())
        self.assertEqual(service.snapshot(0, row)["paths"], ["/project"])

    def test_save_empty_uses_upsert_and_audit(self):
        session = FakeSession()
        with patch.object(service, "async_session", return_value=session):
            value = asyncio.run(service.MenuPermissionService.save(0, [], 10))
        self.assertEqual(value["paths"], [])
        self.assertTrue(value["configured"])
        statement = session.execute.call_args[0][0]
        from sqlalchemy.dialects import mysql
        compiled = statement.compile(dialect=mysql.dialect())
        self.assertIn("ON DUPLICATE KEY UPDATE", str(compiled))
        self.assertEqual(compiled.params["paths"], [])
        self.assertEqual(len(session.added), 1)


class ASGIClient:
    """Call the ASGI application in process without a server or httpx."""
    def __init__(self, app):
        self.app = app

    def request(self, method, path, headers=None, json=None):
        import json as json_module
        body = json_module.dumps(json).encode() if json is not None else b""
        messages = []
        consumed = False

        async def receive():
            nonlocal consumed
            if not consumed:
                consumed = True
                return {"type": "http.request", "body": body, "more_body": False}
            return {"type": "http.disconnect"}

        async def send(message):
            messages.append(message)

        scope = {
            "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
            "method": method, "scheme": "http", "path": path,
            "raw_path": path.encode(), "query_string": b"", "root_path": "",
            "headers": [(key.lower().encode(), str(value).encode()) for key, value in {
                "content-type": "application/json", **(headers or {})
            }.items()],
            "server": ("offline", 80), "client": ("127.0.0.1", 1000),
        }
        asyncio.run(self.app(scope, receive, send))
        status = next(item["status"] for item in messages if item["type"] == "http.response.start")
        payload = b"".join(item.get("body", b"") for item in messages if item["type"] == "http.response.body")
        return types.SimpleNamespace(status_code=status, json=lambda: json_module.loads(payload))

    def get(self, path, **kwargs):
        return self.request("GET", path, **kwargs)

    def put(self, path, **kwargs):
        return self.request("PUT", path, **kwargs)


class MenuApiTests(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(router.router)
        self.client = ASGIClient(app)

    def test_manage_requires_admin(self):
        for role in (0, 1):
            self.assertEqual(self.client.get("/menu/manage", headers={"token": str(role)}).status_code, 403)

    def test_save_requires_admin(self):
        self.assertEqual(self.client.put("/menu/permissions", headers={"token": "0"},
            json={"role": 0, "paths": []}).status_code, 403)

    def test_manage_returns_all_role_snapshots(self):
        overview = {"tree": policy.MENU_ITEMS, "roles": policy.ROLES,
                    "permissions": [service.snapshot(role) for role in (0, 1, 2)]}
        with patch.object(service.MenuPermissionService, "overview", new=AsyncMock(return_value=overview)):
            response = self.client.get("/menu/manage", headers={"token": "2"})
        self.assertEqual([item["role"] for item in response.json()["data"]["permissions"]], [0, 1, 2])

    def test_server_rechecks_current_admin_role(self):
        with patch.object(service.MenuPermissionService, "overview", new=AsyncMock()) as overview:
            response = asyncio.run(router.menu_management({"id": 10, "role": 0}))
        self.assertEqual(response["code"], 403)
        overview.assert_not_awaited()

    def test_me_uses_current_role(self):
        with patch.object(service.MenuPermissionService, "get", new=AsyncMock(return_value={"paths": []})) as get:
            response = self.client.get("/menu/me", headers={"token": "1"})
        self.assertEqual(response.status_code, 200)
        get.assert_awaited_once_with(1)

    def test_invalid_path_never_writes(self):
        response = self.client.put("/menu/permissions", headers={"token": "2"},
            json={"role": 0, "paths": ["/not-real"]})
        self.assertEqual(response.json()["code"], 101)

    def test_body_rejects_boolean_role_and_non_string_paths(self):
        for body in [{"role": True, "paths": []}, {"role": 0, "paths": [3]}, {"role": 0}]:
            self.assertEqual(self.client.put("/menu/permissions", headers={"token": "2"}, json=body).status_code, 422)


if __name__ == "__main__":
    unittest.main()
