"""
UI测试计划执行完毕后触发通知
由于 ui_test.py 为DRM加密文件，此模块作为外部调用入口
"""
from datetime import datetime

from app.crud.auth.UserDao import UserDao
from app.crud.config.NotificationChannelDao import NotificationChannelDao
from app.crud.config.NotificationConfigDao import NotificationConfigDao
from app.crud.config.NotificationGroupDao import NotificationGroupDao
from app.crud.config.NotificationTemplateDao import NotificationTemplateDao
from app.models import async_session
from loguru import logger
from sqlalchemy import text


class UiNotice:
    """UI测试通知发送器"""

    @staticmethod
    async def notify(plan_id: int, run_id: int = 0):
        """
        根据测试计划ID发送通知
        需在UI测试执行完毕后调用
        当 plan_id=0 时会从 run_id 反查 plan_id
        """
        try:
            if plan_id == 0 and run_id > 0:
                async with async_session() as s:
                    r = await s.execute(
                        text("SELECT plan_id FROM pity_ui_test_run WHERE id=:id"),
                        {"id": run_id}
                    )
                    row = r.mappings().first()
                    if row:
                        plan_id = int(row["plan_id"])
            if plan_id <= 0:
                logger.warning(f"[UiNotice] 无法获取计划ID (run_id={run_id})")
                return
            plan = await UiNotice._get_plan(plan_id)
            if plan is None:
                logger.warning(f"[UiNotice] 计划 {plan_id} 不存在")
                return

            # 收集接收人
            receiver_ids = set()

            # 优先使用通知配置
            if plan.get("notification_config_id"):
                config = await NotificationConfigDao.get_config(plan["notification_config_id"])
                if config:
                    if config.receiver:
                        for rid in config.receiver.split(","):
                            if rid.strip().isdigit():
                                receiver_ids.add(int(rid))
                    if config.group_ids:
                        gids = [int(x) for x in config.group_ids.split(",") if x.strip().isdigit()]
                        member_ids = await NotificationGroupDao.get_members_by_groups(gids)
                        receiver_ids.update(member_ids)

                    # 加载渠道
                    channel_ids = [int(x) for x in config.channel_ids.split(",") if x.strip().isdigit()]
                    channels = await NotificationChannelDao.list_by_ids(channel_ids)

                    # 加载模板
                    template = None
                    if config.template_id:
                        template = await NotificationTemplateDao.get_template(config.template_id)

                    if not channels:
                        logger.warning(f"[UiNotice] 配置 {config.id} 无可用渠道")
                        return
                else:
                    logger.warning(f"[UiNotice] 通知配置 {plan['notification_config_id']} 不存在")
                    return
            else:
                # 无通知配置，使用计划本身的 msg_type/receiver
                if not plan.get("receiver") or not plan.get("msg_type"):
                    return
                # 模拟旧版渠道
                from app.models.notification_channel import PityNotificationChannel
                msg_types = plan["msg_type"].split(",")
                channels = []
                for mt in msg_types:
                    mt = mt.strip()
                    if not mt.isdigit():
                        continue
                    # 为每个msg_type创建临时channel对象（仅用于发送）
                    ch = PityNotificationChannel("legacy", int(mt), "{}", 0)
                    ch.enabled = True
                    channels.append(ch)
                template = None
                if plan.get("receiver"):
                    for rid in plan["receiver"].split(","):
                        if rid.strip().isdigit():
                            receiver_ids.add(int(rid))

            if not receiver_ids:
                logger.debug("[UiNotice] 无接收人")
                return

            # 获取用户联系方式
            users = await UserDao.list_user_touch(*list(receiver_ids))
            if not users:
                logger.debug("[UiNotice] 接收人无联系方式")
                return

            # 构建报告数据
            run_result = await UiNotice._get_run_result(run_id or plan.get("last_run_id"))
            report = await UiNotice._build_report(plan, run_result)

            # DingTalk 专用字段
            ding_users = [r.get("phone") for r in users if r.get("phone")]
            report['notification_user'] = " ".join(map(lambda x: f"@{x}", ding_users)) if ding_users else ""
            report['result_color'] = '#67C23A' if report.get('plan_result') == '通过' else '#E6A23C'

            # 发送
            for channel in channels:
                if not getattr(channel, 'enabled', True):
                    continue
                try:
                    await UiNotice._send(channel, template, plan, report, users, ding_users)
                except Exception as e:
                    logger.warning(f"[UiNotice] 渠道 {channel.name} 发送失败: {e}")

            logger.info(f"[UiNotice] 计划 {plan_id} 通知发送完成")

        except Exception as e:
            logger.exception(f"[UiNotice] 通知异常: {e}")

    @staticmethod
    async def _get_plan(plan_id: int) -> dict:
        """从数据库查询UI测试计划"""
        async with async_session() as session:
            result = await session.execute(
                text("SELECT * FROM pity_ui_test_plan WHERE id = :id AND deleted_at = 0"),
                {"id": plan_id}
            )
            row = result.mappings().first()
            if row:
                return dict(row)
            return None

    @staticmethod
    async def _get_run_result(run_id: int) -> dict:
        """获取运行结果统计，从 run 表提取 result_payload 中的统计数据"""
        if not run_id:
            return {}
        async with async_session() as session:
            result = await session.execute(
                text("SELECT * FROM pity_ui_test_run WHERE id = :id"),
                {"id": run_id}
            )
            row = result.mappings().first()
            if row:
                row_dict = dict(row)
                # 解析 result_payload JSON 中的统计数据
                rp = row_dict.get("result_payload")
                if rp:
                    try:
                        import json as _json
                        payload = _json.loads(rp) if isinstance(rp, str) else rp
                        if isinstance(payload, dict):
                            # 从 result_payload 提取统计数据
                            # 兼容两种 key 命名：runner 上报的 success_case_count 和通用的 passed/failed
                            for key, payload_key in [
                                ('success_count', 'success_case_count'),
                                ('failed_count', 'failed_case_count'),
                                ('skipped_count', 'skipped_case_count'),
                                ('passed', 'passed'),
                                ('failed', 'failed'),
                                ('skipped', 'skipped'),
                                ('error', 'error'),
                                ('total', 'total'),
                            ]:
                                if payload_key in payload and row_dict.get(key) is None:
                                    row_dict[key] = int(payload[payload_key])
                            # 如果上述都没取到，从 case_results 逐条统计
                            if row_dict.get('success_count') is None and 'case_results' in payload:
                                cr = payload['case_results']
                                if isinstance(cr, list):
                                    sc = fc = sk = 0
                                    for c in cr:
                                        st = (c.get('status') or '').lower() if isinstance(c, dict) else ''
                                        if st == 'success':
                                            sc += 1
                                        elif st == 'failed':
                                            fc += 1
                                        elif st == 'skipped':
                                            sk += 1
                                    row_dict['success_count'] = sc
                                    row_dict['failed_count'] = fc
                                    row_dict['skipped_count'] = sk
                            # 提取执行人
                            if 'executor' in payload:
                                row_dict['executor_name'] = str(payload.get('executor', ''))
                            # 提取耗时
                            if 'elapsed_ms' in payload:
                                row_dict['duration'] = int(payload['elapsed_ms'])
                    except Exception:
                        pass

                # 查找执行人名称
                create_user = row_dict.get("create_user", 0)
                if create_user and not row_dict.get("executor_name"):
                    try:
                        u_row = await session.execute(
                            text("SELECT name FROM pity_user WHERE id=:id AND deleted_at=0"),
                            {"id": int(create_user)}
                        )
                        u = u_row.mappings().first()
                        if u:
                            row_dict['executor_name'] = u['name']
                    except Exception:
                        pass

                return row_dict

            # 如果没有找到run，尝试从run_detail统计
            detail = await session.execute(
                text("SELECT "
                     "COUNT(1) AS total, "
                     "SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) AS success_count, "
                     "SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed_count, "
                     "SUM(CASE WHEN status = 'skipped' THEN 1 ELSE 0 END) AS skipped_count, "
                     "SUM(CASE WHEN status NOT IN ('success','failed','skipped') THEN 1 ELSE 0 END) AS error_count "
                     "FROM pity_ui_test_run_detail WHERE run_id = :id"),
                {"id": run_id}
            )
            d = detail.mappings().first()
            if d:
                return dict(d)
            return {}

    @staticmethod
    async def _build_report(plan: dict, run_result: dict) -> dict:
        """构建报告数据用于模板渲染"""
        import json as _json

        total = run_result.get("total", 0) or (
            run_result.get("success_count", 0) + run_result.get("failed_count", 0)
            + run_result.get("error_count", 0) + run_result.get("skipped_count", 0))
        # 如果 total 还是 0 但 result_payload 有 case_count
        if total == 0:
            total = run_result.get("case_count", 0)
        success = run_result.get("success_count", 0) or run_result.get("passed", 0)
        failed = run_result.get("failed_count", 0) or run_result.get("failed", 0)
        error = run_result.get("error_count", 0) or run_result.get("error", 0)
        skip = run_result.get("skipped_count", 0) or run_result.get("skipped", 0)

        # 如果上述统计为 0，尝试从 result_payload 的 summary/stats 或 case_results 中读取
        if total == 0 and success == 0 and failed == 0 and error == 0 and skip == 0:
            rp = run_result.get("result_payload")
            if rp:
                try:
                    import json as _json
                    payload = _json.loads(rp) if isinstance(rp, str) else rp
                    if isinstance(payload, dict):
                        summary = payload.get("summary") or payload.get("stats") or payload
                        success = int(summary.get("passed", summary.get("success_case_count", 0)))
                        failed = int(summary.get("failed", summary.get("failed_case_count", 0)))
                        error = int(summary.get("error", 0))
                        skip = int(summary.get("skipped", summary.get("skipped_case_count", 0)))
                        total = success + failed + error + skip
                        # 如果 case_results 存在且上述为 0，逐条统计
                        if total == 0 and 'case_results' in payload:
                            cr = payload['case_results']
                            if isinstance(cr, list):
                                sc = fc = sk = 0
                                for c in cr:
                                    st = (c.get('status') or '').lower() if isinstance(c, dict) else ''
                                    if st == 'success':
                                        sc += 1
                                    elif st == 'failed':
                                        fc += 1
                                    elif st == 'skipped':
                                        sk += 1
                                success = sc
                                failed = fc
                                skip = sk
                                total = sc + fc + sk + error
                except Exception:
                    pass

        # 执行人
        executor = run_result.get("executor_name", "") or "pity机器人"

        pass_rate = plan.get("pass_rate", 80)
        if total > 0:
            actual_rate = int((success / total) * 100)
        else:
            actual_rate = 100

        plan_result = "通过"
        if failed > 0 or error > 0:
            if actual_rate < pass_rate:
                plan_result = "失败"
            else:
                plan_result = "警告"

        # 生成报告 URL
        from config import Config
        run_id = run_result.get("id", 0)
        if hasattr(Config, 'SERVER_REPORT'):
            report_url = f"{Config.SERVER_REPORT.rstrip('/')}/#/share/ui-report/{run_id}"
        else:
            report_url = ""

        return {
            "plan_name": plan.get("name", ""),
            "env": plan.get("env_name", ""),
            "executor": executor,
            "plan_result": plan_result,
            "success": success,
            "failed": failed,
            "error": error,
            "skip": skip,
            "total": total,
            "pass_rate": pass_rate,
            "start_time": run_result.get("created_at", "") if isinstance(run_result.get("created_at"), str)
            else (run_result.get("created_at").strftime("%Y-%m-%d %H:%M:%S") if run_result.get("created_at") else ""),
            "end_time": run_result.get("updated_at", "") if isinstance(run_result.get("updated_at"), str)
            else (run_result.get("updated_at").strftime("%Y-%m-%d %H:%M:%S") if run_result.get("updated_at") else ""),
            "cost": UiNotice._format_duration(
                run_result.get("duration") or run_result.get("elapsed_ms") or run_result.get("cost", "")),
            "report_url": report_url,
        }

    @staticmethod
    def _format_duration(ms):
        """将毫秒转为可读格式"""
        if not ms:
            return ""
        seconds = float(ms) / 1000
        if seconds < 60:
            return f"{seconds:.1f}s"
        minutes = int(seconds // 60)
        secs = int(seconds % 60)
        if minutes < 60:
            return f"{minutes}m{secs}s"
        hours = int(minutes // 60)
        minutes = int(minutes % 60)
        return f"{hours}h{minutes}m{secs}s"

    @staticmethod
    async def _send(channel, template, plan, report, users, ding_users):
        """通过指定渠道发送"""
        import json as json_module
        from app.core.msg.dingtalk import DingTalk
        from app.core.msg.wecom import WeCom
        from app.core.msg.feishu import FeiShu
        from app.core.msg.mail import Email

        cfg = json_module.loads(channel.config_json) if channel.config_json else {}
        channel_type = channel.channel_type

        # 如果是从旧版msg_type走的，config_json为空，构建默认配置
        if not cfg and hasattr(channel, 'channel_type'):
            if channel_type == 0:
                from app.core.configuration import SystemConfiguration
                sys_cfg = SystemConfiguration.get_config().get("email", {})
                cfg = sys_cfg
            elif channel_type == 1:
                cfg = {"webhook_url": plan.get("project_dingtalk_url", "")}

        # 渲染内容
        subject = f"UI测试计划【{plan.get('name', '')}】执行完毕"
        content = None
        if template:
            try:
                content = template.content_template.format(
                    notification_user=report.get("notification_user", ""),
                    plan_name=report.get("plan_name", ""),
                    env=report.get("env", ""),
                    executor=report.get("executor", ""),
                    plan_result=report.get("plan_result", ""),
                    result_color=report.get("result_color", "#000000"),
                    success=report.get("success", 0),
                    failed=report.get("failed", 0),
                    error=report.get("error", 0),
                    skipped=report.get("skip", 0),
                    duration=report.get("cost", ""),
                    start_time=report.get("start_time", ""),
                    end_time=report.get("end_time", ""),
                    pass_rate=report.get("pass_rate", 100),
                    report_url=report.get("report_url", ""),
                )
                if template.subject_template:
                    subject = template.subject_template.format(
                        plan_name=report.get("plan_name", ""),
                        env=report.get("env", ""),
                        plan_result=report.get("plan_result", ""))
            except Exception:
                content = None

        if not content:
            content = (
                f"UI测试计划: {report.get('plan_name', '')}\n"
                f"环境: {report.get('env', '')}\n"
                f"结果: {report.get('plan_result', '')}\n"
                f"成功: {report.get('success', 0)} 失败: {report.get('failed', 0)} "
                f"跳过: {report.get('skip', 0)} 出错: {report.get('error', 0)}\n"
                f"耗时: {report.get('cost', '')}"
            )

        if channel_type == 0:  # Email
            html = Email.render_html(plan_name=report.get("plan_name", ""), **report)
            await Email.send_msg(subject, html, None, *[r.get("email") for r in users])

        elif channel_type == 1:  # DingTalk
            ding = DingTalk(cfg.get("webhook_url", ""), cfg.get("secret"))
            await ding.send_msg(subject, content, None, ding_users,
                                link=report.get("report_url", ""))

        elif channel_type == 2:  # WeCom
            wc = WeCom(cfg.get("webhook_url", ""))
            await wc.send_msg(subject, content)

        elif channel_type == 3:  # Feishu
            fs = FeiShu(cfg.get("webhook_url", ""))
            await fs.send_msg(subject, content)
