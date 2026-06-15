from typing import List

from app.crud import Mapper, ModelWrapper
from app.models.notification_group import ArgusUserGroup, ArgusUserGroupMember


@ModelWrapper(ArgusUserGroup)
class NotificationGroupDao(Mapper):

    @classmethod
    async def list_groups(cls):
        return await cls.select_list(condition=[ArgusUserGroup.deleted_at == 0])

    @classmethod
    async def get_group(cls, group_id: int):
        return await cls.query_record(id=group_id, deleted_at=0)

    @classmethod
    async def add_members(cls, session, group_id: int, user_ids: List[int]):
        for uid in user_ids:
            session.add(ArgusUserGroupMember(group_id, uid))
        await session.flush()

    @classmethod
    async def get_members(cls, group_id: int):
        from app.crud import async_session
        from sqlalchemy import select
        async with async_session() as session:
            result = await session.execute(
                select(ArgusUserGroupMember.user_id)
                .where(ArgusUserGroupMember.group_id == group_id, ArgusUserGroupMember.deleted_at == 0)
            )
            return [row[0] for row in result.fetchall()]

    @classmethod
    async def get_members_by_groups(cls, group_ids: List[int]):
        if not group_ids:
            return []
        from app.crud import async_session
        from sqlalchemy import select
        async with async_session() as session:
            result = await session.execute(
                select(ArgusUserGroupMember.user_id)
                .where(ArgusUserGroupMember.group_id.in_(group_ids), ArgusUserGroupMember.deleted_at == 0)
            )
            return list(set(row[0] for row in result.fetchall()))

    @classmethod
    async def clear_members(cls, session, group_id: int):
        from sqlalchemy import update
        await session.execute(
            update(ArgusUserGroupMember)
            .where(ArgusUserGroupMember.group_id == group_id, ArgusUserGroupMember.deleted_at == 0)
            .values(deleted_at=0)
        )
