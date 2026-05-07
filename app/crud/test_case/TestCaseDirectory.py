import time
from collections import defaultdict
from datetime import datetime

from sqlalchemy import select, asc, or_, func, update

from app.crud import Mapper
from app.models import async_session
from app.schema.testcase_directory import PityTestcaseDirectoryForm, PityTestcaseDirectoryUpdateForm
from app.models.testcase_directory import PityTestcaseDirectory
from app.utils.logger import Log


class PityTestcaseDirectoryDao(Mapper):
    log = Log("PityTestcaseDirectoryDao")

    @staticmethod
    async def query_directory(directory_id: int):
        try:
            async with async_session() as session:
                sql = select(PityTestcaseDirectory).where(PityTestcaseDirectory.id == directory_id,
                                                          PityTestcaseDirectory.deleted_at == 0)
                result = await session.execute(sql)
                return result.scalars().first()
        except Exception as e:
            PityTestcaseDirectoryDao.log.error(f"获取目录详情失败: {str(e)}")
            raise Exception(f"获取目录详情失败: {str(e)}")

    @staticmethod
    async def list_directory(project_id: int):
        try:
            async with async_session() as session:
                sql = select(PityTestcaseDirectory) \
                    .where(PityTestcaseDirectory.deleted_at == 0,
                           PityTestcaseDirectory.project_id == project_id) \
                    .order_by(asc(PityTestcaseDirectory.sort_index), asc(PityTestcaseDirectory.name))
                result = await session.execute(sql)
                return result.scalars().all()
        except Exception as e:
            PityTestcaseDirectoryDao.log.error(f"获取用例目录失败, error: {e}")
            raise Exception(f"获取用例目录失败, error: {e}")

    @staticmethod
    async def insert_directory(form: PityTestcaseDirectoryForm, user: int):
        try:
            async with async_session() as session:
                async with session.begin():
                    sql = select(PityTestcaseDirectory).where(PityTestcaseDirectory.deleted_at == 0,
                                                              PityTestcaseDirectory.name == form.name,
                                                              PityTestcaseDirectory.parent == form.parent,
                                                              PityTestcaseDirectory.project_id == form.project_id)
                    result = await session.execute(sql)
                    if result.scalars().first() is not None:
                        raise Exception("目录已存在")
                    if form.sort_index is None:
                        max_sql = select(func.max(PityTestcaseDirectory.sort_index)).where(
                            PityTestcaseDirectory.deleted_at == 0,
                            PityTestcaseDirectory.project_id == form.project_id,
                            PityTestcaseDirectory.parent == form.parent,
                        )
                        max_res = await session.execute(max_sql)
                        form.sort_index = (max_res.scalar() or -1) + 1
                    else:
                        form.sort_index = max(0, int(form.sort_index))
                        await session.execute(
                            update(PityTestcaseDirectory)
                            .where(
                                PityTestcaseDirectory.deleted_at == 0,
                                PityTestcaseDirectory.project_id == form.project_id,
                                PityTestcaseDirectory.parent == form.parent,
                                PityTestcaseDirectory.sort_index >= form.sort_index,
                            )
                            .values(sort_index=PityTestcaseDirectory.sort_index + 1)
                        )
                    session.add(PityTestcaseDirectory(form, user))
        except Exception as e:
            PityTestcaseDirectoryDao.log.error(f"创建目录失败, error: {e}")
            raise Exception(f"创建目录失败: {e}")

    @staticmethod
    async def update_directory(form: PityTestcaseDirectoryUpdateForm, user: int):
        try:
            async with async_session() as session:
                async with session.begin():
                    sql = select(PityTestcaseDirectory).where(
                        PityTestcaseDirectory.id == form.id,
                        PityTestcaseDirectory.deleted_at == 0,
                        PityTestcaseDirectory.project_id == form.project_id,
                    )
                    result = await session.execute(sql)
                    current = result.scalars().first()
                    if current is None:
                        raise Exception("目录不存在")

                    old_parent = current.parent
                    old_index = current.sort_index or 0
                    fields_set = getattr(form, "model_fields_set", getattr(form, "__fields_set__", set()))
                    target_parent = form.parent if "parent" in fields_set else old_parent

                    if target_parent == current.id:
                        raise Exception("目录不能移动到自身下")
                    if target_parent is not None:
                        parent_map = defaultdict(list)
                        all_sql = select(PityTestcaseDirectory.id, PityTestcaseDirectory.parent).where(
                            PityTestcaseDirectory.deleted_at == 0,
                            PityTestcaseDirectory.project_id == form.project_id,
                        )
                        all_res = await session.execute(all_sql)
                        for did, p in all_res.all():
                            parent_map[p].append(did)
                        descendants = []
                        PityTestcaseDirectoryDao.get_sub_son(parent_map, parent_map.get(current.id), descendants)
                        if target_parent in descendants:
                            raise Exception("目录不能移动到自己的子目录")

                    if form.name:
                        dup_sql = select(PityTestcaseDirectory).where(
                            PityTestcaseDirectory.deleted_at == 0,
                            PityTestcaseDirectory.project_id == form.project_id,
                            PityTestcaseDirectory.parent == target_parent,
                            PityTestcaseDirectory.name == form.name,
                            PityTestcaseDirectory.id != form.id,
                        )
                        dup_res = await session.execute(dup_sql)
                        if dup_res.scalars().first() is not None:
                            raise Exception("同级目录下名称已存在")

                    max_sql = select(func.max(PityTestcaseDirectory.sort_index)).where(
                        PityTestcaseDirectory.deleted_at == 0,
                        PityTestcaseDirectory.project_id == form.project_id,
                        PityTestcaseDirectory.parent == target_parent,
                    )
                    max_res = await session.execute(max_sql)
                    max_index = max_res.scalar()
                    max_index = -1 if max_index is None else max_index

                    if form.sort_index is None:
                        new_index = max_index + 1
                    else:
                        new_index = max(0, int(form.sort_index))
                        sibling_max = max_index if target_parent == old_parent else max_index + 1
                        new_index = min(new_index, sibling_max)

                    if target_parent == old_parent:
                        if new_index > old_index:
                            await session.execute(
                                update(PityTestcaseDirectory)
                                .where(
                                    PityTestcaseDirectory.deleted_at == 0,
                                    PityTestcaseDirectory.project_id == form.project_id,
                                    PityTestcaseDirectory.parent == old_parent,
                                    PityTestcaseDirectory.id != current.id,
                                    PityTestcaseDirectory.sort_index > old_index,
                                    PityTestcaseDirectory.sort_index <= new_index,
                                )
                                .values(sort_index=PityTestcaseDirectory.sort_index - 1)
                            )
                        elif new_index < old_index:
                            await session.execute(
                                update(PityTestcaseDirectory)
                                .where(
                                    PityTestcaseDirectory.deleted_at == 0,
                                    PityTestcaseDirectory.project_id == form.project_id,
                                    PityTestcaseDirectory.parent == old_parent,
                                    PityTestcaseDirectory.id != current.id,
                                    PityTestcaseDirectory.sort_index >= new_index,
                                    PityTestcaseDirectory.sort_index < old_index,
                                )
                                .values(sort_index=PityTestcaseDirectory.sort_index + 1)
                            )
                    else:
                        await session.execute(
                            update(PityTestcaseDirectory)
                            .where(
                                PityTestcaseDirectory.deleted_at == 0,
                                PityTestcaseDirectory.project_id == form.project_id,
                                PityTestcaseDirectory.parent == old_parent,
                                PityTestcaseDirectory.sort_index > old_index,
                            )
                            .values(sort_index=PityTestcaseDirectory.sort_index - 1)
                        )
                        await session.execute(
                            update(PityTestcaseDirectory)
                            .where(
                                PityTestcaseDirectory.deleted_at == 0,
                                PityTestcaseDirectory.project_id == form.project_id,
                                PityTestcaseDirectory.parent == target_parent,
                                PityTestcaseDirectory.sort_index >= new_index,
                            )
                            .values(sort_index=PityTestcaseDirectory.sort_index + 1)
                        )

                    if form.name:
                        current.name = form.name
                    current.parent = target_parent
                    current.sort_index = new_index
                    current.update_user = user
                    current.updated_at = datetime.now()
        except Exception as e:
            PityTestcaseDirectoryDao.log.error(f"更新目录失败, error: {e}")
            raise Exception(f"更新目录失败: {e}")

    @staticmethod
    async def delete_directory(id: int, user: int):
        try:
            async with async_session() as session:
                async with session.begin():
                    sql = select(PityTestcaseDirectory).where(PityTestcaseDirectory.id == id,
                                                              PityTestcaseDirectory.deleted_at == 0)
                    result = await session.execute(sql)
                    query = result.scalars().first()
                    if query is None:
                        raise Exception("目录不存在")
                    query.deleted_at = int(time.time() * 1000)
                    query.update_user = user
        except Exception as e:
            PityTestcaseDirectoryDao.log.error(f"删除目录失败, error: {e}")
            raise Exception(f"删除目录失败: {e}")

    @staticmethod
    async def get_directory_tree(project_id: int, case_node=None, move: bool = False) -> (list, dict):
        res = await PityTestcaseDirectoryDao.list_directory(project_id)
        ans = list()
        ans_map = dict()
        case_map = dict()
        parent_map = defaultdict(list)
        for directory in res:
            if directory.parent is None:
                ans.append(dict(
                    title=directory.name,
                    key=directory.id,
                    value=directory.id,
                    label=directory.name,
                    sort_index=directory.sort_index,
                    children=list(),
                ))
            else:
                parent_map[directory.parent].append(directory.id)
            ans_map[directory.id] = directory
        for r in ans:
            await PityTestcaseDirectoryDao.get_directory(ans_map, parent_map, r.get('key'), r.get('children'), case_map,
                                                         case_node, move)
            if not move and not r.get('children'):
                r['disabled'] = True
        return ans, case_map

    @staticmethod
    async def get_directory(ans_map: dict, parent_map, parent, children, case_map, case_node=None, move=False):
        current = parent_map.get(parent)
        if case_node is not None:
            nodes, cs = await case_node(parent)
            children.extend(nodes)
            case_map.update(cs)
        if current is None:
            return
        for c in current:
            temp = ans_map.get(c)
            if case_node is None:
                child = list()
            else:
                child, cs = await case_node(temp.id)
                case_map.update(cs)
            children.append(dict(
                title=temp.name,
                key=temp.id,
                children=child,
                label=temp.name,
                value=temp.id,
                sort_index=temp.sort_index,
                disabled=len(child) == 0 and not move
            ))
            await PityTestcaseDirectoryDao.get_directory(ans_map, parent_map, temp.id, child, case_map, case_node,
                                                         move=move)

    @staticmethod
    async def get_directory_son(directory_id: int):
        parent_map = defaultdict(list)
        async with async_session() as session:
            ans = [directory_id]
            sql = select(PityTestcaseDirectory) \
                .where(PityTestcaseDirectory.deleted_at == 0,
                       or_(PityTestcaseDirectory.parent == directory_id, PityTestcaseDirectory.parent != None)) \
                .order_by(asc(PityTestcaseDirectory.sort_index), asc(PityTestcaseDirectory.name))
            result = await session.execute(sql)
            data = result.scalars().all()
            for d in data:
                parent_map[d.parent].append(d.id)
            son = parent_map.get(directory_id)
            PityTestcaseDirectoryDao.get_sub_son(parent_map, son, ans)
            return ans

    @staticmethod
    def get_sub_son(parent_map: dict, son: list, result: list):
        if not son:
            return
        for s in son:
            result.append(s)
            sons = parent_map.get(s)
            if not sons:
                continue
            PityTestcaseDirectoryDao.get_sub_son(parent_map, sons, result)
