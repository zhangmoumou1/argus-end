import json
from collections import defaultdict
from datetime import datetime, timedelta
from typing import List, Dict

from sqlalchemy import desc, func, and_, asc
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.crud import Mapper, ModelWrapper, connect
from app.crud.test_case.ConstructorDao import ConstructorDao
from app.crud.test_case.TestCaseAssertsDao import TestCaseAssertsDao
from app.crud.test_case.TestCaseDirectory import PityTestcaseDirectoryDao
from app.crud.test_case.TestCaseOutParametersDao import PityTestCaseOutParametersDao
from app.crud.test_case.TestcaseDataDao import PityTestcaseDataDao
from app.enums.ConstructorEnum import ConstructorType
from app.middleware.RedisManager import RedisHelper
from app.models import async_session
from app.models.constructor import Constructor
from app.models.out_parameters import PityTestCaseOutParameters
from app.models.project import Project
from app.models.test_case import TestCase
from app.models.testcase_asserts import TestCaseAsserts
from app.models.testcase_data import PityTestcaseData
from app.models.testcase_directory import PityTestcaseDirectory
from app.models.user import User
from app.schema.testcase_out_parameters import PityTestCaseVariablesDto
from app.schema.testcase_schema import TestCaseForm, TestCaseInfo


@ModelWrapper(TestCase)
class TestCaseDao(Mapper):
    @classmethod
    async def list_test_case(cls, directory_id: int = None, name: str = "", create_user: str = None):
        try:
            filters = [TestCase.deleted_at == 0]
            if directory_id:
                parents = await PityTestcaseDirectoryDao.get_directory_son(directory_id)
                filters = [TestCase.deleted_at == 0, TestCase.directory_id.in_(parents)]
            if name:
                filters.append(TestCase.name.like(f"%{name}%"))
            if create_user:
                filters.append(TestCase.create_user == create_user)
            async with async_session() as session:
                sql = select(TestCase).where(*filters).order_by(TestCase.name.asc())
                result = await session.execute(sql)
                return result.scalars().all()
        except Exception as e:
            cls.__log__.error(f"获取测试用例失败: {str(e)}")
            raise Exception(f"获取测试用例失败: {str(e)}")

    @staticmethod
    async def get_test_case_by_directory_id(directory_id: int):
        try:
            async with async_session() as session:
                sql = select(TestCase).where(TestCase.deleted_at == 0,
                                             TestCase.directory_id == directory_id).order_by(TestCase.name.asc())
                result = await session.execute(sql)
                ans = []
                case_map = dict()
                for item in result.scalars():
                    ans.append({"title": item.name, "value": f"testcase_{item.id}", "key": f"testcase_{item.id}"})
                    case_map[item.id] = item.name
                return ans, case_map
        except Exception as e:
            TestCaseDao.__log__.error(f"获取测试用例失败: {str(e)}")
            raise Exception(f"获取测试用例失败: {str(e)}")

    @staticmethod
    async def get_case_children(case_id: int):
        data = await TestCaseAssertsDao.list_test_case_asserts(case_id)
        return [dict(key=f"asserts_{d.id}", title=d.name, case_id=case_id) for d in data]

    @staticmethod
    async def get_case_children_length(case_id: int):
        data = await TestCaseAssertsDao.list_test_case_asserts(case_id)
        return len(data)

    @staticmethod
    async def _insert(session, case_id: int, user_id: int, form: TestCaseInfo, **fields: tuple):
        for field, model_info in fields.items():
            md, model = model_info
            field_data = getattr(form, field)
            for f in field_data:
                if hasattr(f, "case_id"):
                    setattr(f, "case_id", case_id)
                    data = model(**f.dict(), user_id=user_id)
                else:
                    data = model(**f.dict(), user_id=user_id, case_id=case_id)
                await md.insert(model=data, session=session, not_begin=True)

    @staticmethod
    async def insert_test_case(session, data: TestCaseInfo, user_id: int) -> TestCase:
        query = await session.execute(
            select(TestCase).where(TestCase.directory_id == data.case.directory_id, TestCase.name == data.case.name,
                                   TestCase.deleted_at == 0))
        if query.scalars().first() is not None:
            raise Exception("用例名称已存在")
        cs = TestCase(**data.case.dict(), create_user=user_id)
        session.add(cs)
        await session.flush()
        session.expunge(cs)
        await TestCaseDao._insert(session, cs.id, user_id, data, constructor=(ConstructorDao, Constructor),
                                  asserts=(TestCaseAssertsDao, TestCaseAsserts),
                                  out_parameters=(PityTestCaseOutParametersDao, PityTestCaseOutParameters),
                                  data=(PityTestcaseDataDao, PityTestcaseData))
        return cs

    @staticmethod
    def _copy_case_model(source: TestCase, directory_id: int, name: str, user_id: int) -> TestCase:
        return TestCase(
            name=name,
            request_type=source.request_type,
            url=source.url,
            directory_id=directory_id,
            status=source.status,
            priority=source.priority,
            create_user=user_id,
            body_type=source.body_type,
            base_path=source.base_path,
            tag=source.tag,
            request_headers=source.request_headers,
            case_type=source.case_type,
            body=source.body,
            request_method=source.request_method,
            api_service_id=getattr(source, "api_service_id", 0) or 0,
            api_endpoint_id=getattr(source, "api_endpoint_id", 0) or 0,
            api_version_id=getattr(source, "api_version_id", 0) or 0,
            api_version_no=getattr(source, "api_version_no", None),
            api_bind_mode=getattr(source, "api_bind_mode", "pinned") or "pinned",
            api_pending_update=getattr(source, "api_pending_update", 0) or 0,
        )

    @staticmethod
    def _next_copy_name(source_name: str, used_names: set) -> str:
        base = (source_name or "未命名用例")[:32]
        if base not in used_names:
            used_names.add(base)
            return base
        suffix_index = 1
        while True:
            suffix = "-副本" if suffix_index == 1 else f"-副本{suffix_index}"
            max_base_len = max(1, 32 - len(suffix))
            candidate = f"{base[:max_base_len]}{suffix}"
            if candidate not in used_names:
                used_names.add(candidate)
                return candidate
            suffix_index += 1

    @staticmethod
    def _remap_constructor_json(raw_json: str, case_id_map: dict) -> str:
        try:
            data = json.loads(raw_json or "{}")
        except Exception:
            return raw_json
        for key in ("case_id", "constructor_case_id"):
            value = data.get(key)
            if value in case_id_map:
                data[key] = case_id_map[value]
        return json.dumps(data, ensure_ascii=False)

    @staticmethod
    async def copy_test_cases(id_list: List[int], directory_id: int, user_id: int) -> List[int]:
        if not id_list:
            raise Exception("请选择需要复制的用例")
        async with async_session() as session:
            async with session.begin():
                directory = (await session.execute(
                    select(PityTestcaseDirectory).where(
                        PityTestcaseDirectory.id == directory_id,
                        PityTestcaseDirectory.deleted_at == 0,
                    )
                )).scalars().first()
                if directory is None:
                    raise Exception("目标目录不存在")

                case_result = await session.execute(
                    select(TestCase).where(TestCase.id.in_(id_list), TestCase.deleted_at == 0)
                )
                case_map = {item.id: item for item in case_result.scalars().all()}
                source_cases = [case_map[item] for item in id_list if item in case_map]
                if not source_cases:
                    raise Exception("未找到可复制的用例")

                name_result = await session.execute(
                    select(TestCase.name).where(TestCase.directory_id == directory_id, TestCase.deleted_at == 0)
                )
                used_names = {item[0] for item in name_result.all()}
                old_new_map = {}
                new_case_ids = []

                for source in source_cases:
                    new_name = TestCaseDao._next_copy_name(source.name, used_names)
                    copied = TestCaseDao._copy_case_model(source, directory_id, new_name, user_id)
                    session.add(copied)
                    await session.flush()
                    old_new_map[source.id] = copied.id
                    new_case_ids.append(copied.id)

                old_ids = list(old_new_map.keys())
                asserts = (await session.execute(
                    select(TestCaseAsserts).where(TestCaseAsserts.case_id.in_(old_ids), TestCaseAsserts.deleted_at == 0)
                )).scalars().all()
                for item in asserts:
                    session.add(TestCaseAsserts(item.name, old_new_map[item.case_id], item.assert_type,
                                                item.expected, item.actually, user_id))

                test_data = (await session.execute(
                    select(PityTestcaseData).where(PityTestcaseData.case_id.in_(old_ids), PityTestcaseData.deleted_at == 0)
                )).scalars().all()
                for item in test_data:
                    session.add(PityTestcaseData(item.env, old_new_map[item.case_id], item.name, item.json_data, user_id))

                out_parameters = (await session.execute(
                    select(PityTestCaseOutParameters).where(
                        PityTestCaseOutParameters.case_id.in_(old_ids),
                        PityTestCaseOutParameters.deleted_at == 0,
                    )
                )).scalars().all()
                for item in out_parameters:
                    session.add(PityTestCaseOutParameters(item.name, item.source, old_new_map[item.case_id], user_id,
                                                          expression=item.expression, match_index=item.match_index))

                constructors = (await session.execute(
                    select(Constructor).where(Constructor.case_id.in_(old_ids), Constructor.deleted_at == 0)
                    .order_by(Constructor.case_id.asc(), Constructor.suffix.asc(), Constructor.index.asc())
                )).scalars().all()
                for item in constructors:
                    constructor_json = TestCaseDao._remap_constructor_json(item.constructor_json, old_new_map)
                    session.add(Constructor(item.type, item.name, item.enable, constructor_json,
                                            old_new_map[item.case_id], item.public, user_id,
                                            value=item.value or "", suffix=item.suffix, index=item.index or 0))
                return new_case_ids

    @classmethod
    async def update_test_case(cls, test_case: TestCaseForm, user_id: int) -> TestCase:
        try:
            async with async_session() as session:
                async with session.begin():
                    query = await session.execute(
                        select(TestCase).where(TestCase.id == test_case.id, TestCase.deleted_at == 0))
                    data = query.scalars().first()
                    if data is None:
                        raise Exception("用例不存在")
                    cls.update_model(data, test_case, user_id)
                    await session.flush()
                    session.expunge(data)
                    return data
        except Exception as e:
            cls.__log__.error(f"编辑用例失败: {str(e)}")
            raise Exception(f"编辑用例失败: {str(e)}")

    @staticmethod
    async def query_test_case(case_id: int) -> dict:
        try:
            async with async_session() as session:
                sql = select(TestCase).where(TestCase.id == case_id, TestCase.deleted_at == 0)
                result = await session.execute(sql)
                data = result.scalars().first()
                if data is None:
                    raise Exception("用例不存在")
                asserts = await TestCaseAssertsDao.async_list_test_case_asserts(data.id)
                constructors = await ConstructorDao.list_constructor(case_id)
                constructors_case = await TestCaseDao.query_test_case_by_constructors(constructors)
                test_data = await PityTestcaseDataDao.list_testcase_data(case_id)
                parameters = await PityTestCaseOutParametersDao.select_list(case_id=case_id,
                                                                            _sort=(asc(PityTestCaseOutParameters.id),))
                return dict(asserts=asserts, constructors=constructors, case=data, constructors_case=constructors_case,
                            test_data=test_data, out_parameters=parameters)
        except Exception as e:
            TestCaseDao.__log__.error(f"查询用例失败: {str(e)}")
            raise Exception(f"查询用例失败: {str(e)}")

    @staticmethod
    async def query_test_case_by_constructors(constructors: List[Constructor]):
        try:
            constructors = [json.loads(x.constructor_json).get("case_id") for x in constructors if x.type == 0]
            async with async_session() as session:
                sql = select(TestCase).where(TestCase.id.in_(constructors), TestCase.deleted_at == 0)
                result = await session.execute(sql)
                data = result.scalars().all()
                return {x.id: x for x in data}
        except Exception as e:
            TestCaseDao.__log__.error(f"查询用例失败: {str(e)}")
            raise Exception(f"查询用例失败: {str(e)}")

    @staticmethod
    async def query_test_case_out_parameters(session, case_list: List[PityTestCaseVariablesDto], case_set=None,
                                             var_list=None):
        if len(case_list) == 0:
            return
        if case_set is None:
            case_set = set(list(c.case_id for c in case_list))
        if var_list is None:
            var_list = []
        cs_list = list(c.case_id for c in case_list)
        step_case = list()
        name_dict = {c.case_id: c.step_name for c in case_list}
        out = select(PityTestCaseOutParameters).where(PityTestCaseOutParameters.case_id.in_(cs_list),
                                                      PityTestCaseOutParameters.deleted_at == 0)
        parameters = await session.execute(out)
        for p in parameters.scalars().all():
            var_list.append(dict(stepName=name_dict[p.case_id], name="${%s}" % p.name))
        sql = select(Constructor).where(Constructor.case_id.in_(cs_list), Constructor.deleted_at == 0)
        steps = await session.execute(sql)
        for s in steps.scalars().all():
            if s.value:
                var_list.append(dict(stepName=s.name, name="${%s}" % s.value))
                continue
            if s.type == ConstructorType.testcase:
                data = json.loads(s.constructor_json)
                case_id = data.get("constructor_case_id") or data.get("case_id")
                if not case_id:
                    continue
                if case_id in case_set:
                    raise Exception("场景存在循环依赖")
                case_set.add(case_id)
                step_case.append(PityTestCaseVariablesDto(case_id=case_id, step_name=s.name))
        await TestCaseDao.query_test_case_out_parameters(session, step_case, case_set, var_list)

    @staticmethod
    async def async_query_test_case(case_id) -> [TestCase, str]:
        try:
            async with async_session() as session:
                result = await session.execute(
                    select(TestCase).where(TestCase.id == case_id, TestCase.deleted_at == 0))
                data = result.scalars().first()
                if data is None:
                    raise Exception(f"用例id: {case_id}不存在, 可能已经被删除")
                return data
        except Exception as e:
            TestCaseDao.__log__.error(f"查询用例失败: {str(e)}")
            raise Exception(f"查询用例失败: {str(e)}")

    @classmethod
    async def list_testcase_tree(cls, projects: List[Project]) -> [List, dict]:
        try:
            result = []
            project_map = {}
            project_index = {}
            for p in projects:
                project_map[p.id] = p.name
                result.append({"label": p.name, "value": p.id, "key": p.id, "children": []})
                project_index[p.id] = len(result) - 1
            async with async_session() as session:
                query = await session.execute(select(TestCase).where(
                    TestCase.project_id.in_(project_map.keys()),
                    TestCase.deleted_at == 0
                ))
                data = query.scalars().all()
                for d in data:
                    result[project_index[d.project_id]]["children"].append({"label": d.name, "value": d.id, "key": d.id})
                return result
        except Exception as e:
            cls.__log__.error(f"获取用例列表失败: {str(e)}")
            raise Exception("获取用例列表失败")

    @staticmethod
    async def select_constructor(case_id: int) -> List[Constructor]:
        try:
            async with async_session() as session:
                query = await session.execute(select(Constructor).where(Constructor.case_id == case_id,
                                                                        Constructor.deleted_at == 0).order_by(
                    desc(Constructor.created_at)))
                return query.scalars().all()
        except Exception as e:
            TestCaseDao.__log__.error(f"查询构造数据失败: {str(e)}")

    @staticmethod
    async def async_select_constructor(case_id: int) -> List[Constructor]:
        try:
            async with async_session() as session:
                sql = select(Constructor).where(Constructor.case_id == case_id,
                                                Constructor.deleted_at == 0).order_by(Constructor.index)
                data = await session.execute(sql)
                return data.scalars().all()
        except Exception as e:
            TestCaseDao.__log__.error(f"查询构造数据失败: {str(e)}")

    @staticmethod
    async def collect_data(case_id: int, data: List):
        pre = dict(id=f"pre_{case_id}", label="前置条件", children=list())
        suffix = dict(id=f"suffix_{case_id}", label="后置条件", children=list())
        await TestCaseDao.collect_constructor(case_id, pre, suffix)
        data.append(pre)
        asserts = dict(id=f"asserts_{case_id}", label="断言", children=list())
        await TestCaseDao.collect_asserts(case_id, asserts)
        data.append(asserts)
        data.append(suffix)

    @staticmethod
    async def collect_constructor(case_id, parent, suffix):
        constructors = await TestCaseDao.async_select_constructor(case_id)
        for c in constructors:
            temp = dict(id=f"constructor_{c.id}", label=f"{c.name}", children=list())
            if c.type == ConstructorType.testcase:
                temp["label"] = "[CASE]: " + temp["label"]
                json_data = json.loads(c.constructor_json)
                await TestCaseDao.collect_data(json_data.get("case_id"), temp.get("children"))
            elif c.type == ConstructorType.sql:
                temp["label"] = "[SQL]: " + temp["label"]
            elif c.type == ConstructorType.redis:
                temp["label"] = "[REDIS]: " + temp["label"]
            elif c.type == ConstructorType.py_script:
                temp["label"] = "[PyScript]: " + temp["label"]
            elif c.type == ConstructorType.http:
                temp["label"] = "[HTTP Request]: " + temp["label"]
            if c.suffix:
                suffix.get("children").append(temp)
            else:
                parent.get("children").append(temp)

    @staticmethod
    async def collect_asserts(case_id, parent):
        asserts = await TestCaseAssertsDao.async_list_test_case_asserts(case_id)
        for a in asserts:
            temp = dict(id=f"assert_{a.id}", label=f"{a.name}", children=list())
            parent.get("children").append(temp)

    @staticmethod
    async def get_xmind_data(case_id: int):
        data = await TestCaseDao.query_test_case(case_id)
        cs = data.get("case")
        result = dict(id=f"case_{case_id}", label=f"{cs.name}({cs.id})")
        children = list()
        await TestCaseDao.collect_data(case_id, children)
        result["children"] = children
        return result

    @classmethod
    async def generate_sql(cls):
        return select(TestCase.create_user, func.count(TestCase.id)) \
            .outerjoin(User, and_(User.deleted_at == 0, TestCase.create_user == User.id)).where(
            TestCase.deleted_at == 0).group_by(TestCase.create_user).order_by(desc(func.count(TestCase.id)))

    @classmethod
    @RedisHelper.cache("rank")
    @connect
    async def query_user_case_list(cls, session: AsyncSession = None) -> Dict[str, List]:
        ans = dict()
        sql = await cls.generate_sql()
        query = await session.execute(sql)
        for i, q in enumerate(query.all()):
            user, count = q
            ans[str(user)] = [count, i + 1]
        return ans

    @classmethod
    @RedisHelper.cache("rank_detail")
    @connect
    async def query_user_case_rank(cls, session: AsyncSession = None) -> List:
        ans = []
        sql = await cls.generate_sql()
        query = await session.execute(sql)
        for i, q in enumerate(query.all()):
            user, count = q
            ans.append(dict(id=user, count=count, rank=i + 1))
        return ans

    @staticmethod
    async def query_weekly_user_case(user_id: int, start_time: datetime, end_time: datetime) -> List:
        ans = defaultdict(int)
        async with async_session() as session:
            async with session.begin():
                sql = select(TestCase.created_at, func.count(TestCase.id)).where(
                    TestCase.create_user == user_id,
                    TestCase.deleted_at == 0, TestCase.created_at.between(start_time, end_time)).group_by(
                    TestCase.created_at).order_by(asc(TestCase.created_at))
                query = await session.execute(sql)
                for i, q in enumerate(query.all()):
                    date, count = q
                    ans[date.strftime("%Y-%m-%d")] += count
        return await TestCaseDao.fill_data(start_time, end_time, ans)

    @staticmethod
    async def fill_data(start_time: datetime, end_time: datetime, data: dict):
        start = start_time
        ans = []
        while start <= end_time:
            date = start.strftime("%Y-%m-%d")
            ans.append(dict(date=date, count=data.get(date, 0)))
            start += timedelta(days=1)
        return ans
