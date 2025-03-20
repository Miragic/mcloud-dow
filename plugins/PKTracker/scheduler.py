import sqlite3
from datetime import datetime
import croniter
from common.log import logger
from bridge.reply import Reply, ReplyType
from bridge.context import Context, ContextType
from channel.channel import Channel

class TaskScheduler:
    def __init__(self, db_path, channel: Channel = None):
        self.db_path = db_path
        self.channel = channel

    def check_reminders(self):
        """检查并触发到期的提醒"""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        
        now = datetime.now()
        
        try:
            # 获取所有启用的任务
            c.execute("""SELECT task_id, group_id, task_name, reminder_time 
                        FROM t_task WHERE enable=1 AND reminder_time IS NOT NULL""")
            
            for task in c.fetchall():
                task_id, group_id, task_name, reminder_time = task
                
                # 检查是否到达提醒时间
                cron = croniter.croniter(reminder_time, now)
                next_time = cron.get_prev(datetime)
                
                if (now - next_time).total_seconds() < 60:  # 在1分钟内
                    # TODO: 发送提醒消息
                    self._send_reminder(group_id, task_name)
                    
        except Exception as e:
            logger.exception(f"[PKTracker] 检查提醒异常: {str(e)}")
        finally:
            conn.close()

    def process_weekly_rewards(self):
        """处理每周奖励"""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        
        try:
            # 获取所有启用周奖励的任务
            c.execute("""SELECT task_id FROM t_task 
                        WHERE week_checkin_reward_enabled=1""")
            
            for (task_id,) in c.fetchall():
                # 获取本周打卡次数最多的用户
                c.execute("""SELECT user_id, COUNT(*) as cnt 
                           FROM t_checkin_log 
                           WHERE task_id=? AND checkin_time >= date('now', '-7 days')
                           GROUP BY user_id 
                           ORDER BY cnt DESC 
                           LIMIT 1""", (task_id,))
                
                result = c.fetchone()
                if result:
                    user_id, count = result
                    # 发放奖励
                    c.execute("""INSERT INTO t_bonus (task_id, user_id, type, amount, date_awarded)
                                VALUES (?, ?, 'week', 3, date('now'))""",
                             (task_id, user_id))
            
            conn.commit()
        except Exception as e:
            logger.exception(f"[PKTracker] 处理周奖励异常: {str(e)}")
            conn.rollback()
        finally:
            conn.close()

    def process_monthly_rewards(self):
        """处理每月奖励"""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        
        try:
            # 获取所有启用月奖励的任务
            c.execute("""SELECT task_id FROM t_task 
                        WHERE month_checkin_reward_enabled=1""")
            
            for (task_id,) in c.fetchall():
                # 获取本月打卡次数最多的用户
                c.execute("""SELECT user_id, COUNT(*) as cnt 
                           FROM t_checkin_log 
                           WHERE task_id=? AND checkin_time >= date('now', 'start of month')
                           GROUP BY user_id 
                           ORDER BY cnt DESC 
                           LIMIT 1""", (task_id,))
                
                result = c.fetchone()
                if result:
                    user_id, count = result
                    # 发放奖励
                    c.execute("""INSERT INTO t_bonus (task_id, user_id, type, amount, date_awarded)
                                VALUES (?, ?, 'month', 5, date('now'))""",
                             (task_id, user_id))
            
            conn.commit()
        except Exception as e:
            logger.exception(f"[PKTracker] 处理月奖励异常: {str(e)}")
            conn.rollback()
        finally:
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
                    u.name,
                    up.checkin_count,
                    up.checkin_count + up.bonus_points as total_points
                FROM user_points up
                JOIN t_user u ON up.user_id = u.user_id
                ORDER BY total_points DESC
                LIMIT 10
            """, (task_id,))
            
            rankings = c.fetchall()
            
            # 生成排行榜消息
            message = f"📊 [{task_name}] 排行榜 TOP 10\n"
            message += "===================\n"
            for idx, (name, checkins, points) in enumerate(rankings, 1):
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

    def _send_reminder(self, group_id, task_name):
        """发送提醒消息"""
        try:
            message = f"⏰ 打卡提醒\n任务: [{task_name}]\n快来打卡啦~ 发送 PKTracker [{task_name}] 你的打卡内容 即可完成打卡"
            
            if self.channel:
                context = Context(ContextType.TEXT, message, group_id)
                reply = Reply(ReplyType.TEXT, message)
                self.channel.send(reply, context)
                
        except Exception as e:
            logger.exception(f"[PKTracker] 发送提醒消息异常: {str(e)}")

    def start_scheduler(self):
        """启动调度器"""
        try:
            # 每天检查提醒
            self.check_reminders()
            
            # 获取当前时间
            now = datetime.now()
            
            # 每周日晚上处理周奖励
            if now.weekday() == 6 and now.hour == 23:
                self.process_weekly_rewards()
                
            # 每月最后一天晚上处理月奖励
            if (now + timedelta(days=1)).day == 1 and now.hour == 23:
                self.process_monthly_rewards()
                
        except Exception as e:
            logger.exception(f"[PKTracker] 调度器执行异常: {str(e)}")