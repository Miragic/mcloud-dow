import sqlite3
from datetime import datetime, timedelta
from common.log import logger
from bridge.reply import Reply, ReplyType
from bridge.context import Context, ContextType
from channel.channel import Channel
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
import config as RobotConfig
from channel.chat_message import ChatMessage
import gc
import time
from channel import channel_factory


class TaskScheduler:
    def __init__(self, db_path, user_manager):
        self.db_path = db_path
        self.channel = None
        self.user_manager = user_manager
        self.scheduler = BackgroundScheduler()
        self._init_scheduler()

    def _init_scheduler(self):
        """初始化定时任务"""
        # 每分钟检查提醒
        self.scheduler.add_job(
            self.check_reminders,
            CronTrigger(minute='*'),
            id='check_reminders'
        )
        
        # 每天早上9:10发送排行榜
        self.scheduler.add_job(
            self.send_daily_ranking,
            CronTrigger(hour=9, minute=10),
            id='daily_ranking'
        )
        
        # 每周晚上23:00处理周奖励
        self.scheduler.add_job(
            self.process_weekly_rewards,
            CronTrigger(day_of_week='sun', hour=23),
            id='weekly_rewards'
        )
        
        # 每月最后一天23:00处理月奖励
        self.scheduler.add_job(
            self.process_monthly_rewards,
            CronTrigger(day='last', hour=23),
            id='monthly_rewards'
        )

    def start_scheduler(self):
        """启动调度器"""
        try:
            if not self.scheduler.running:
                self.scheduler.start()
                logger.info("[PKTracker] 定时任务调度器已启动")
        except Exception as e:
            logger.error(f"[PKTracker] 启动调度器异常: {str(e)}")

    def stop_scheduler(self):
        """停止调度器"""
        try:
            if self.scheduler.running:
                self.scheduler.shutdown()
                logger.info("[PKTracker] 定时任务调度器已停止")
        except Exception as e:
            logger.error(f"[PKTracker] 停止调度器异常: {str(e)}")

    def check_reminders(self):
        """检查并触发到期的提醒"""
        conn = None
        try:
            conn = sqlite3.connect(self.db_path)
            c = conn.cursor()
            
            now = datetime.now()
            current_time = now.strftime('%H:%M')
            
            # 获取所有启用的任务
            c.execute("""
                SELECT t.task_id, t.group_id, t.task_name, t.reminder_time, t.remind_text,
                       COUNT(DISTINCT cl.user_id) as checked_users
                FROM t_task t
                LEFT JOIN t_checkin_log cl ON t.task_id = cl.task_id 
                    AND date(cl.checkin_time) = date('now')
                WHERE t.enable = 1 
                    AND t.reminder_time = ?
                    AND t.reminder_time IS NOT NULL
                GROUP BY t.task_id
            """, (current_time,))
            
            tasks = c.fetchall()
            
            for task in tasks:
                task_id, group_id, task_name, reminder_time, remind_text, checked_users = task
                
                # 构建提醒消息
                message = f"⏰ 任务提醒 [{task_name}]\n"
                message += "===================\n\n"
                
                if remind_text:
                    message += f"📝 {remind_text}\n\n"
                
                message += f"🔸 今日已打卡: {checked_users}人\n"
                message += "\n💡 快来打卡啦~记得使用以下格式:\n"
                message += f"PKTracker [{task_name}] 打卡内容"

                # 发送提醒消息
                self._send_reminder(group_id, message)
                
                logger.info(f"[PKTracker] 已发送任务 [{task_name}] 的提醒消息到群组 {group_id}")
                
        except Exception as e:
            logger.error(f"[PKTracker] 检查提醒异常: {str(e)}")
        finally:
            if conn:
                conn.close()

    def _send_reminder(self, group_id: str, message: str, retry_cnt=0):
        """发送提醒消息
        
        Args:
            group_id: 群组ID
            message: 提醒消息
            retry_cnt: 重试次数
        """
        try:
            # 构建消息上下文
            context = Context(ContextType.TEXT, message)
            context["isgroup"] = True
            context["group_id"] = group_id
            context["msg"] = ChatMessage(None)
            context["msg"].is_group = True
            context["msg"].other_user_id = group_id
            
            # 构建回复消息
            reply = Reply(ReplyType.TEXT, message)
            
            # 获取当前 channel 类型并创建 channel
            channel_name = RobotConfig.conf().get("channel_type", "wx")
            channel = channel_factory.create_channel(channel_name)
            
            # 发送消息
            channel.send(reply, context)
            
            # 释放资源
            channel = None
            gc.collect()
            
            logger.info(f"[PKTracker] 成功发送消息到群组 {group_id}")
            
        except Exception as e:
            logger.error(f"[PKTracker] 发送提醒消息异常: {str(e)}")
            # 重试逻辑
            if retry_cnt < 2:
                time.sleep(3 + 3 * retry_cnt)
                self._send_reminder(group_id, message, retry_cnt + 1)

    def process_weekly_rewards(self):
        """处理每周奖励"""
        conn = None
        try:
            conn = sqlite3.connect(self.db_path)
            c = conn.cursor()
            
            # 获取所有启用周奖励的任务
            c.execute("""
                SELECT t.task_id, t.group_id, t.task_name, t.week_checkin_reward
                FROM t_task t
                WHERE t.enable = 1 
                    AND t.week_checkin_reward_enabled = 1
                    AND t.week_checkin_reward IS NOT NULL
            """)
            
            tasks = c.fetchall()
            for task_id, group_id, task_name, bonus in tasks:
                # 获取本周打卡次数最多的用户
                c.execute("""
                    SELECT 
                        user_id,
                        COUNT(*) as checkin_count
                    FROM t_checkin_log
                    WHERE task_id = ? 
                        AND checkin_time >= date('now', 'weekday 0', '-7 days')
                        AND checkin_time < date('now', 'weekday 0')
                    GROUP BY user_id
                    ORDER BY checkin_count DESC
                    LIMIT 1
                """, (task_id,))
                
                winner = c.fetchone()
                if winner:
                    user_id, checkin_count = winner
                    # 批量获取用户昵称
                    nicknames = self.user_manager._get_nickname_by_user_ids([user_id])
                    user_name = nicknames.get(user_id, "未知用户")
                    
                    # 记录奖励
                    c.execute("""
                        INSERT INTO t_bonus (task_id, user_id, type, amount, date_awarded)
                        VALUES (?, ?, 'week', ?, date('now'))
                    """, (task_id, user_id, bonus))
                    
                    # 发送获奖通知
                    message = f"🎉 周冠军公告 [{task_name}]\n"
                    message += "===================\n\n"
                    message += f"👑 本周冠军: {user_name}\n"
                    message += f"📊 打卡次数: {checkin_count}次\n"
                    message += f"🎁 奖励积分: {bonus}分\n"
                    message += "\n继续加油,下周等你来战！💪"
                    
                    self._send_reminder(group_id, message)
            
            conn.commit()
            
        except Exception as e:
            logger.error(f"[PKTracker] 处理周奖励异常: {str(e)}")
            if conn:
                conn.rollback()
        finally:
            if conn:
                conn.close()

    def process_monthly_rewards(self):
        """处理每月奖励"""
        conn = None
        try:
            conn = sqlite3.connect(self.db_path)
            c = conn.cursor()
            
            # 获取所有启用月奖励的任务
            c.execute("""
                SELECT t.task_id, t.group_id, t.task_name, t.month_checkin_reward
                FROM t_task t
                WHERE t.enable = 1 
                    AND t.month_checkin_reward_enabled = 1
                    AND t.month_checkin_reward IS NOT NULL
            """)
            
            tasks = c.fetchall()
            for task_id, group_id, task_name, bonus in tasks:
                # 获取上月打卡次数最多的用户
                c.execute("""
                    SELECT 
                        user_id,
                        COUNT(*) as checkin_count
                    FROM t_checkin_log
                    WHERE task_id = ? 
                        AND checkin_time >= date('now', 'start of month', '-1 month')
                        AND checkin_time < date('now', 'start of month')
                    GROUP BY user_id
                    ORDER BY checkin_count DESC
                    LIMIT 1
                """, (task_id,))
                
                winner = c.fetchone()
                if winner:
                    user_id, checkin_count = winner
                    # 批量获取用户昵称
                    nicknames = self.user_manager._get_nickname_by_user_ids([user_id])
                    user_name = nicknames.get(user_id, "未知用户")
                    
                    # 记录奖励
                    c.execute("""
                        INSERT INTO t_bonus (task_id, user_id, type, amount, date_awarded)
                        VALUES (?, ?, 'month', ?, date('now'))
                    """, (task_id, user_id, bonus))
                    
                    # 发送获奖通知
                    message = f"🎉 月度冠军公告 [{task_name}]\n"
                    message += "===================\n\n"
                    message += f"👑 本月冠军: {user_name}\n"
                    message += f"📊 打卡次数: {checkin_count}次\n"
                    message += f"🎁 奖励积分: {bonus}分\n"
                    message += "\n继续加油,下月等你来战！💪"
                    
                    self._send_reminder(group_id, message)
            
            conn.commit()
            
        except Exception as e:
            logger.error(f"[PKTracker] 处理月奖励异常: {str(e)}")
            if conn:
                conn.rollback()
        finally:
            if conn:
                conn.close()

    def send_ranking_list(self, task_id):
        """发送任务排行榜"""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        
        try:
            # 获取任务信息
            c.execute("""SELECT group_id, task_name FROM t_task WHERE task_id=?""", (task_id,))
            task = c.fetchone()
            if not task:
                return
                
            group_id, task_name = task
            
            # 获取排行榜数据
            c.execute("""
                WITH user_points AS (
                    SELECT 
                        cl.user_id,
                        COUNT(*) as checkin_count,
                        COALESCE(SUM(b.amount), 0) as bonus_points
                    FROM t_checkin_log cl
                    LEFT JOIN t_bonus b ON cl.task_id = b.task_id AND cl.user_id = b.user_id
                    WHERE cl.task_id = ?
                    GROUP BY cl.user_id
                )
                SELECT 
                    user_id,
                    checkin_count,
                    checkin_count + bonus_points as total_points
                FROM user_points
                ORDER BY total_points DESC
                LIMIT 10
            """, (task_id,))
            
            rankings = c.fetchall()
            
            # 批量获取所有用户的昵称
            user_ids = [user_id for user_id, _, _ in rankings]
            nicknames = self.user_manager._get_nickname_by_user_ids(user_ids)
            
            # 生成排行榜消息
            message = f"📊 [{task_name}] 排行榜 TOP 10\n"
            message += "===================\n"
            for idx, (user_id, checkins, points) in enumerate(rankings, 1):
                name = nicknames.get(user_id, "未知用户")
                medal = "🥇" if idx == 1 else "🥈" if idx == 2 else "🥉" if idx == 3 else "👑"
                message += f"{medal} {idx}. {name}\n"
                message += f"   打卡: {checkins}次 | 总积分: {points}\n"
            
            # 发送消息
            if self.channel:
                context = Context(ContextType.TEXT, message, group_id)
                reply = Reply(ReplyType.TEXT, message)
                self.channel.send(reply, context)
            
        except Exception as e:
            logger.exception(f"[PKTracker] 发送排行榜异常: {str(e)}")
        finally:
            conn.close()

    def send_daily_ranking(self):
        """发送每日任务排行榜"""
        conn = None
        try:
            conn = sqlite3.connect(self.db_path)
            c = conn.cursor()
            
            # 获取所有启用的任务
            c.execute("""
                SELECT task_id, group_id, task_name
                FROM t_task
                WHERE enable = 1
            """)
            
            tasks = c.fetchall()
            for task_id, group_id, task_name in tasks:
                # 调用现有的排行榜发送方法
                self.send_ranking_list(task_id)
                logger.info(f"[PKTracker] 已发送任务 [{task_name}] 的每日排行榜")
                
        except Exception as e:
            logger.error(f"[PKTracker] 发送每日排行榜异常: {str(e)}")
        finally:
            if conn:
                conn.close()