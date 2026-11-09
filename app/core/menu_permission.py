from datetime import datetime

from sqlalchemy import select
from sqlalchemy.dialects.mysql import insert

from app.core.menu_policy import MENU_ITEMS, ROLES, catalog_paths, default_paths, normalize_paths
from app.enums.OperationEnum import OperationType
from app.models import async_session
from app.models.menu_permission import MenuPermission
from app.models.operation_log import ArgusOperationLog


def snapshot(role, row=None):
    paths = default_paths(role) if row is None else normalize_paths(role, [
        path for path in row.paths if path in catalog_paths()
        and (role == 2 or not path.startswith("/system"))
    ])
    return {
        "role": role,
        "paths": paths,
        "configured": row is not None,
        "updated_at": row.updated_at.isoformat() if row is not None else None,
    }


class MenuPermissionService:
    @staticmethod
    async def get(role):
        async with async_session() as session:
            row = (await session.execute(
                select(MenuPermission).where(MenuPermission.role == role)
            )).scalars().first()
            return snapshot(role, row)

    @staticmethod
    async def overview():
        async with async_session() as session:
            rows = (await session.execute(select(MenuPermission))).scalars().all()
            by_role = {row.role: row for row in rows}
            return {
                "tree": MENU_ITEMS,
                "roles": ROLES,
                "permissions": [snapshot(role["id"], by_role.get(role["id"])) for role in ROLES],
            }

    @staticmethod
    async def save(role, paths, user_id):
        paths = normalize_paths(role, paths)
        now = datetime.now()
        async with async_session() as session:
            async with session.begin():
                statement = insert(MenuPermission).values(
                    role=role, paths=paths, update_user=user_id, updated_at=now
                )
                await session.execute(statement.on_duplicate_key_update(
                    paths=paths, update_user=user_id, updated_at=now
                ))
                session.add(ArgusOperationLog(
                    user_id, OperationType.UPDATE, "更新角色菜单权限", "菜单管理",
                    ", ".join(paths) or "清空菜单", key=role,
                ))
        return {"role": role, "paths": paths, "configured": True, "updated_at": now.isoformat()}
