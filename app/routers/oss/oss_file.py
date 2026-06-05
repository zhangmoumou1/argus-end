import json
import os
from typing import List

from fastapi import APIRouter, File, Depends, UploadFile, Form, Header
from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud.auth.UserDao import UserDao
from app.crud.oss.PityOssDao import PityOssDao
from app.enums.OperationEnum import OperationType
from app.handler.fatcory import PityResponse
from app.middleware.oss import OssClient, get_avatar_bucket_name, get_public_bucket_name, normalize_oss_upload_result
from app.middleware.Jwt import UserToken
from app.models import async_session
from app.models.oss_file import PityOssFile
from app.models.operation_log import PityOperationLog
from app.models.user import User
from app.routers import Permission, get_session
from config import Config

router = APIRouter(prefix="/oss")


def _oss_log_payload(action: str, filepath: str, bucket_name: str = "", object_key: str = "", file_size: str = ""):
    return {
        "action": action,
        "file_path": filepath,
        "bucket_name": bucket_name,
        "object_key": object_key,
        "file_size": file_size,
    }


async def _insert_oss_action_log(user_id: int, action: str, filepath: str, bucket_name: str = "",
                                 object_key: str = "", file_size: str = "", session=None):
    payload = _oss_log_payload(action, filepath, bucket_name, object_key, file_size)
    log_model = PityOperationLog(
        user_id,
        OperationType.EXECUTE,
        f"OSS对象{action}",
        "oss",
        json.dumps(payload, ensure_ascii=False),
    )
    if session is not None:
        session.add(log_model)
        return
    async with async_session() as db_session:
        if db_session.in_transaction():
            db_session.add(log_model)
            return
        async with db_session.begin():
            db_session.add(log_model)


async def _upsert_oss_record(user_id: int, filepath: str, upload_meta: dict, file_size: int, session: AsyncSession = None):
    record = await PityOssDao.query_record(file_path=filepath, deleted_at=0, session=session)
    human_size = PityOssFile.get_size(file_size)
    if record is not None:
        old = PityOssFile(
            record.create_user,
            record.file_path,
            record.bucket_name,
            record.object_key,
            record.file_size,
            id=record.id,
        )
        record.file_path = filepath
        record.bucket_name = upload_meta["bucket_name"]
        record.object_key = upload_meta["object_key"]
        record.file_size = human_size
        record.update_user = user_id
        if session is not None:
            await session.flush()
            await PityOssDao.insert_log(
                session,
                user_id,
                OperationType.UPDATE,
                record,
                old,
                record.id,
                changed=["file_path", "bucket_name", "object_key", "file_size"],
            )
        else:
            await PityOssDao.update_record_by_id(user_id, record, log=True)
    else:
        model = PityOssFile(
            user_id,
            filepath,
            upload_meta["bucket_name"],
            upload_meta["object_key"],
            human_size,
        )
        if session is not None:
            session.add(model)
            await session.flush()
            await PityOssDao.insert_log(session, user_id, OperationType.INSERT, model, key=model.id)
        else:
            await PityOssDao.insert(model=model, log=True)
    await _insert_oss_action_log(
        user_id,
        "上传",
        filepath,
        upload_meta["bucket_name"],
        upload_meta["object_key"],
        human_size,
        session=session,
    )
    return human_size


async def _upload_single_object(user_id: int, filepath: str, upload_file: UploadFile, bucket_name: str = None,
                                session: AsyncSession = None):
    file_content = await upload_file.read()
    client = OssClient.get_oss_client()
    upload_result, file_size = await client.create_file(
        filepath,
        file_content,
        bucket_name=bucket_name,
        content_type=upload_file.content_type,
    )
    upload_meta = normalize_oss_upload_result(client, upload_result, filepath, bucket_name=bucket_name)
    await _upsert_oss_record(user_id, filepath, upload_meta, file_size, session=session)
    return upload_meta, file_size


async def _query_oss_update_info_map(session, file_paths: List[str]):
    if not file_paths:
        return {}
    query = await session.execute(
        select(
            PityOssFile.file_path,
            PityOssFile.updated_at,
            PityOssFile.update_user,
            User.name.label("update_user_name"),
        ).outerjoin(User, User.id == PityOssFile.update_user)
        .where(PityOssFile.deleted_at == 0, PityOssFile.file_path.in_(file_paths))
    )
    result = {}
    for row in query.mappings().all():
        result[row["file_path"]] = {
            "update_user": row["update_user"],
            "update_user_name": row["update_user_name"],
            "local_updated_at": row["updated_at"].strftime("%Y-%m-%d %H:%M:%S") if row["updated_at"] else None,
        }
    return result


def _merge_oss_update_info(data: dict, update_info: dict):
    if not update_info:
        return data
    data.update(update_info)
    return data


def _parse_optional_user(token: str = None):
    if not token:
        return None
    try:
        return UserToken.parse_token(token)
    except Exception:
        return None


@router.post("/upload")
async def create_oss_file(filepath: str, file: UploadFile = File(...),
                          session=Depends(get_session),
                          user_info=Depends(Permission(Config.MEMBER))):
    try:
        bucket_name = get_public_bucket_name() or None
        async with session.begin():
            await _upload_single_object(user_info['id'], filepath, file, bucket_name=bucket_name, session=session)
        return PityResponse.success()
    except Exception as e:
        return PityResponse.failed(f"上传失败: {e}")


@router.post("/upload/batch")
async def create_oss_files(paths: str = Form(...), files: List[UploadFile] = File(...),
                           user_info=Depends(Permission(Config.MEMBER))):
    try:
        filepaths = json.loads(paths or "[]")
        if not isinstance(filepaths, list):
            raise Exception("paths格式不正确")
        if len(files) == 0:
            raise Exception("请选择上传文件")
        if len(files) > 100:
            raise Exception("单次最多上传100个对象")
        if len(filepaths) != len(files):
            raise Exception("上传路径与文件数量不匹配")
        bucket_name = get_public_bucket_name() or None
        result = []
        for index, upload_file in enumerate(files):
            filepath = str(filepaths[index] or "").strip()
            if not filepath:
                raise Exception(f"第{index + 1}个文件缺少目标路径")
            upload_meta, file_size = await _upload_single_object(user_info['id'], filepath, upload_file, bucket_name=bucket_name)
            result.append({
                "file_path": filepath,
                "bucket_name": upload_meta["bucket_name"],
                "object_key": upload_meta["object_key"],
                "file_size": PityOssFile.get_size(file_size),
            })
        return PityResponse.success({"count": len(result), "items": result})
    except Exception as e:
        return PityResponse.failed(f"批量上传失败: {e}")


@router.post("/avatar", summary="上传用户头像")
async def upload_avatar(file: UploadFile = File(...), user_info=Depends(Permission(Config.MEMBER))):
    try:
        file_content = await file.read()
        suffix = file.filename.split(".")[-1]
        filepath = f"user_{user_info['id']}.{suffix}"
        client = OssClient.get_oss_client()
        bucket_name = get_avatar_bucket_name() or None
        upload_result, _ = await client.create_file(
            filepath,
            file_content,
            base_path="avatar",
            bucket_name=bucket_name,
        )
        upload_meta = normalize_oss_upload_result(client, upload_result, filepath, bucket_name=bucket_name, base_path="avatar")
        file_url = upload_meta["file_url"]
        await UserDao.update_avatar(user_info['id'], file_url)
        return PityResponse.success(file_url)
    except Exception as e:
        return PityResponse.failed(f"上传头像失败: {e}")


@router.get("/list")
async def list_oss_file(filepath: str = '', recursive: bool = True, suffix: str = None, _=Depends(Permission(Config.MEMBER))):
    try:
        client = OssClient.get_oss_client()
        records = await client.list_objects(
            prefix=filepath or "",
            recursive=recursive,
            bucket_name=get_public_bucket_name() or None,
            suffix=suffix,
        )
        async with async_session() as session:
            update_info_map = await _query_oss_update_info_map(
                session,
                [item.get("file_path") for item in records if item.get("file_path")],
            )
        merged = [_merge_oss_update_info(item, update_info_map.get(item.get("file_path"))) for item in records]
        return PityResponse.success(merged)
    except Exception as e:
        return PityResponse.failed(f"获取失败: {e}")


@router.get("/detail")
async def detail_oss_file(filepath: str, _=Depends(Permission(Config.MEMBER)), session=Depends(get_session)):
    try:
        client = OssClient.get_oss_client()
        detail = await client.get_object_detail(
            filepath,
            bucket_name=get_public_bucket_name() or None,
        )
        update_info_map = await _query_oss_update_info_map(session, [filepath])
        return PityResponse.success(_merge_oss_update_info(detail, update_info_map.get(filepath)))
    except Exception as e:
        return PityResponse.failed(f"获取详情失败: {e}")


@router.get("/delete")
async def delete_oss_file(filepath: str, is_dir: bool = False,
                          user_info=Depends(Permission(Config.MANAGER)), session=Depends(get_session)):
    try:
        client = OssClient.get_oss_client()
        bucket_name = get_public_bucket_name() or None
        if is_dir:
            async with session.begin():
                query = await session.execute(
                    select(PityOssFile).where(
                        PityOssFile.deleted_at == 0,
                        or_(
                            PityOssFile.file_path == filepath,
                            PityOssFile.file_path.like(f"{filepath}/%"),
                        ),
                    )
                )
                records = query.scalars().all()
                await client.delete_prefix(filepath, bucket_name=bucket_name)
                for item in records:
                    PityOssDao.delete_model(item, user_info["id"])
                await session.flush()
                await _insert_oss_action_log(
                    user_info["id"],
                    "删除目录",
                    filepath,
                    bucket_name or "",
                    filepath,
                    f"{len(records)}个对象",
                    session=session,
                )
            return PityResponse.success()

        log_bucket_name = ""
        object_key = filepath
        file_size = ""
        async with session.begin():
            record = await PityOssDao.query_record(file_path=filepath, deleted_at=0, session=session)
            if record is not None:
                log_bucket_name = getattr(record, "bucket_name", "") or ""
                object_key = getattr(record, "object_key", "") or filepath
                file_size = getattr(record, "file_size", "") or ""
            await client.delete_file(filepath, bucket_name=bucket_name)
            if record is not None:
                PityOssDao.delete_model(record, user_info["id"])
                await session.flush()
                await PityOssDao.insert_log(session, user_info["id"], OperationType.DELETE, record, key=record.id)
            await _insert_oss_action_log(
                user_info["id"],
                "删除",
                filepath,
                log_bucket_name,
                object_key,
                file_size,
                session=session,
            )
        return PityResponse.success()
    except Exception as e:
        return PityResponse.failed(f"删除失败: {e}")


# @router.post("/update")
# async def update_oss_file(filepath: str, file: UploadFile = File(...), user_info=Depends(Permission(Config.MEMBER))):
#     """
#     更新oss文件，路径不能变化
#     :param user_info:
#     :param filepath:
#     :param file:
#     :return:
#     """
#     try:
#         client = OssClient.get_oss_client()
#         file_content = await file.read()
#         await client.update_file(filepath, file_content)
#         return PityResponse.success()
#     except Exception as e:
#         return PityResponse.failed(f"修改失败: {e}")


@router.get("/download")
async def download_oss_file(filepath: str, token: str = Header(None), session=Depends(get_session)):
    """
    更新oss文件，路径不能变化
    :param filepath:
    :return:
    """
    try:
        parsed_user = _parse_optional_user(token)
        client = OssClient.get_oss_client()
        # 切割获取文件名
        path, filename = await client.download_file(filepath, bucket_name=get_public_bucket_name() or None)
        if parsed_user is not None:
            record = await PityOssDao.query_record(file_path=filepath, deleted_at=0, session=session)
            await _insert_oss_action_log(
                int(parsed_user.get("id") or 0),
                "下载",
                filepath,
                getattr(record, "bucket_name", "") if record is not None else "",
                getattr(record, "object_key", filepath) if record is not None else filepath,
                getattr(record, "file_size", "") if record is not None else "",
                session=session,
            )
        return PityResponse.file(path, filename)
    except Exception as e:
        return PityResponse.failed(f"下载失败: {e}")
