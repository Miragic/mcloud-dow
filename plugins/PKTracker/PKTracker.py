# encoding:utf-8
import json
import os
import re
import sqlite3
from datetime import datetime, timedelta

import plugins
from bridge.context import ContextType
from bridge.reply import Reply, ReplyType
from common.log import logger
from plugins import *
from .scheduler import TaskScheduler

@plugins.register(
    name="PKTracker",
    desire_priority=10,
    hidden=False,
    enabled=True,
    desc="微信群打卡PK插件",
    version="1.0.0",
    author="Miragic",
)
class PKTracker(Plugin):
    def __init__(self):
        super().__init__()
        try:
            self.config = super().load_config()
            if not self.config:
                self.config = self._load_config_template()
            
            # 初始化数据库
            self.db_path = os.path.join(os.path.dirname(__file__), "pkTracker.db")
            self.init_database()
            
            # 初始化任务调度器
            self.scheduler = TaskScheduler(self.db_path)
            
            # 注册事件处理器
            self.handlers[Event.ON_HANDLE_CONTEXT] = self.on_handle_context
            
            logger.info("[PKTracker] 初始化成功")
        except Exception as e:
            logger.error(f"[PKTracker] 初始化异常：{e}")
            raise "[PKTracker] init failed, ignore "

    def on_handle_context(self, e_context: EventContext):
        """处理消息事件"""
        context = e_context["context"]
        if context.type != ContextType.TEXT:
            return
        
        content = context.content
        if not content.startswith("PKTracker"):
            return
            
        try:
            # 解析命令
            parts = content.split()
            if len(parts) < 2:
                reply = Reply(ReplyType.TEXT, "格式错误,请输入正确的指令")
                e_context["reply"] = reply
                e_context.action = EventAction.BREAK_PASS
                return
                
            command = parts[1]
            
            # 处理打卡命令
            if command.startswith("[") and command.endswith("]"):
                task_name = command[1:-1]
                if len(parts) < 3:
                    reply_text = "请输入打卡内容"
                else:
                    content = " ".join(parts[2:])
                    reply_text = self.handle_checkin(context.from_user_id, context.group_id, task_name, content)
                    
            # 处理管理员命令
            elif command == "设置频率":
                if not self.is_admin(context.group_id, context.from_user_id):
                    reply_text = "只有管理员可以设置频率"
                elif len(parts) != 4:
                    reply_text = "格式错误,请使用: PKTracker 设置频率 [任务名称] [日/周/月]"
                else:
                    reply_text = self.set_frequency(context.group_id, parts[2], parts[3])
                    
            # 处理查询命令
            elif command == "积分排名":
                reply_text = self.get_ranking(context.group_id, parts[2] if len(parts) > 2 else None)
                
            else:
                reply_text = "未知命令,请检查输入"
                
            reply = Reply(ReplyType.TEXT, reply_text)
            e_context["reply"] = reply
            e_context.action = EventAction.BREAK_PASS
            
        except Exception as e:
            logger.exception(f"[PKTracker] 处理消息异常: {str(e)}")
            reply = Reply(ReplyType.ERROR, "处理命令时出错,请稍后再试")
            e_context["reply"] = reply
            e_context.action = EventAction.BREAK_PASS

    def handle_checkin(self, user_id, group_id, task_name, content):
        """处理打卡"""
        try:
            conn = sqlite3.connect(self.db_path)
            c = conn.cursor()

            # 检查任务是否存在
            c.execute("SELECT task_id FROM t_task WHERE group_id=? AND task_name=? AND enable=1",
                      (group_id, task_name))
            task = c.fetchone()
            if not task:
                return f"任务 [{task_name}] 不存在或未启用"

            task_id = task[0]
            now = datetime.now()

            # 检查用户是否已存在,不存在则添加
            c.execute("SELECT 1 FROM t_user WHERE user_id=?", (user_id,))
            if not c.fetchone():
                c.execute("INSERT INTO t_user (user_id, name) VALUES (?, ?)",
                          (user_id, f"用户{user_id[-6:]}"))

            # 检查今日是否已打卡
            today = now.strftime('%Y-%m-%d')
            c.execute("""SELECT 1 FROM t_checkin_log 
                        WHERE task_id=? AND user_id=? AND date(checkin_time)=?""",
                      (task_id, user_id, today))
            if c.fetchone():
                return f"❌ 今日已打卡,请明天再来~"

            # 记录打卡
            c.execute("""INSERT INTO t_checkin_log (task_id, user_id, checkin_time, content)
                        VALUES (?, ?, ?, ?)""",
                      (task_id, user_id, now.strftime('%Y-%m-%d %H:%M:%S'), content))

            # 计算奖励
            bonus = self._calculate_bonus(c, task_id, user_id, now)

            conn.commit()
            return f"✅ 打卡成功!\n获得 {bonus} 积分 🎉"

        except Exception as e:
            logger.exception(f"[PKTracker] 打卡异常: {str(e)}")
            return "❌ 打卡失败,请稍后重试"
        finally:
            if 'conn' in locals() and conn is not None:
                conn.close()

    def _calculate_bonus(self, cursor, task_id, user_id, checkin_time):
        """计算打卡奖励"""
        total_bonus = 1  # 基础打卡积分
        
        # 获取任务配置
        cursor.execute("""SELECT first_checkin_reward_enabled, first_checkin_reward,
                         consecutive_checkin_reward_enabled, consecutive_checkin_reward
                         FROM t_task WHERE task_id=?""", (task_id,))
        task_info = cursor.fetchone()
        
        # 检查首次打卡奖励
        if task_info[0]:  # first_checkin_reward_enabled
            today = checkin_time.date()
            cursor.execute("""SELECT COUNT(*) FROM t_checkin_log 
                            WHERE task_id=? AND date(checkin_time)=?""",
                         (task_id, today))
            if cursor.fetchone()[0] == 1:
                total_bonus += task_info[1]  # first_checkin_reward
                
        # 检查连续打卡奖励
        if task_info[2]:  # consecutive_checkin_reward_enabled
            cursor.execute("""SELECT date(checkin_time) FROM t_checkin_log
                            WHERE task_id=? AND user_id=?
                            ORDER BY checkin_time DESC LIMIT 3""",
                         (task_id, user_id))
            dates = cursor.fetchall()
            
            if len(dates) == 3:
                date1 = datetime.strptime(dates[0][0], '%Y-%m-%d')
                date2 = datetime.strptime(dates[1][0], '%Y-%m-%d')
                date3 = datetime.strptime(dates[2][0], '%Y-%m-%d')
                
                if (date1 - date2).days == 1 and (date2 - date3).days == 1:
                    total_bonus += task_info[3]  # consecutive_checkin_reward
        
        return total_bonus

    def get_help_text(self, **kwargs):
        return "微信群打卡PK插件\n" \
               "支持的命令:\n" \
               "1. PKTracker [任务名称] 任务内容 - 打卡\n" \
               "2. PKTracker [任务名称] 积分排名 - 查看排名\n" \
               "3. PKTracker 设置频率 [任务名称] [日/周/月] - 设置任务频率(管理员)\n" \
               "4. PKTracker 设置提醒 [任务名称] [时间] - 设置提醒时间(管理员)"

    def _load_config_template(self):
        """加载配置模板"""
        try:
            plugin_config_path = os.path.join(self.path, "config.json.template")
            if os.path.exists(plugin_config_path):
                with open(plugin_config_path, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception as e:
            logger.exception(e)
        return {}

    def init_database(self):
        """初始化数据库表结构"""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()

        # 创建任务表
        c.execute('''CREATE TABLE IF NOT EXISTS t_task
                       (task_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        group_id TEXT NOT NULL,
                        task_name TEXT NOT NULL,
                        frequency TEXT CHECK(frequency IN ('day','week','month')),
                        first_checkin_reward_enabled INTEGER DEFAULT 1,
                        first_checkin_reward INTEGER DEFAULT 3,
                        week_checkin_reward_enabled INTEGER DEFAULT 1,
                        week_checkin_reward INTEGER DEFAULT 3,
                        month_checkin_reward_enabled INTEGER DEFAULT 1,
                        month_checkin_reward INTEGER DEFAULT 5,
                        consecutive_checkin_reward_enabled INTEGER DEFAULT 1,
                        consecutive_checkin_reward INTEGER DEFAULT 3,
                        reminder_time TEXT,
                        enable INTEGER DEFAULT 1)''')

        # 创建打卡记录表
        c.execute('''CREATE TABLE IF NOT EXISTS t_checkin_log
                       (checkin_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        task_id INTEGER,
                        user_id TEXT NOT NULL,
                        checkin_time DATETIME NOT NULL,
                        content TEXT,
                        FOREIGN KEY(task_id) REFERENCES t_task(task_id))''')

        # 创建用户表
        c.execute('''CREATE TABLE IF NOT EXISTS t_user
                       (user_id TEXT PRIMARY KEY,
                        name TEXT NOT NULL)''')

        # 创建管理员表
        c.execute('''CREATE TABLE IF NOT EXISTS t_admin
                       (group_id TEXT NOT NULL,
                        user_id TEXT NOT NULL,
                        PRIMARY KEY(group_id, user_id))''')

        # 创建积分表
        c.execute('''CREATE TABLE IF NOT EXISTS t_bonus
                       (bonus_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        task_id INTEGER,
                        user_id TEXT NOT NULL,
                        type TEXT CHECK(type IN ('first_checkin','consecutive','week','month')),
                        amount INTEGER NOT NULL,
                        date_awarded DATE NOT NULL,
                        FOREIGN KEY(task_id) REFERENCES t_task(task_id))''')

        conn.commit()
        conn.close()

    def is_admin(self, group_id, user_id):
        """检查用户是否为管理员"""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute("SELECT 1 FROM t_admin WHERE group_id=? AND user_id=?",
                  (group_id, user_id))
        result = c.fetchone() is not None
        conn.close()
        return result

    def set_frequency(self, group_id: str, task_name: str, frequency: str) -> str:
        """设置任务打卡频率
        Args:
            group_id: 群组ID
            task_name: 任务名称
            frequency: 打卡频率(日/周/月)
        Returns:
            str: 设置结果提示
        """
        # 验证频率参数
        freq_map = {"日": "day", "周": "week", "月": "month"}
        if frequency not in freq_map:
            return "❌ 频率设置失败: 频率只能是 日/周/月"

        try:
            conn = sqlite3.connect(self.db_path)
            c = conn.cursor()

            # 检查任务是否存在
            c.execute("""SELECT task_id FROM t_task 
                        WHERE group_id=? AND task_name=?""",
                      (group_id, task_name))
            task = c.fetchone()

            if task:
                # 更新已存在的任务
                c.execute("""UPDATE t_task 
                            SET frequency=? 
                            WHERE group_id=? AND task_name=?""",
                          (freq_map[frequency], group_id, task_name))
            else:
                # 创建新任务
                c.execute("""INSERT INTO t_task 
                            (group_id, task_name, frequency) 
                            VALUES (?, ?, ?)""",
                          (group_id, task_name, freq_map[frequency]))

            conn.commit()
            return f"✅ 成功设置任务 [{task_name}] 的打卡频率为: {frequency}"

        except Exception as e:
            logger.exception(f"[PKTracker] 设置任务频率异常: {str(e)}")
            return "❌ 设置失败,请稍后重试"
        finally:
            conn.close()

    def get_ranking(self, group_id: str, task_name: str = None) -> str:
        """获取群内打卡排行榜
        Args:
            group_id: 群组ID
            task_name: 任务名称,为None时显示所有任务排名
        Returns:
            str: 排行榜信息
        """
        try:
            conn = sqlite3.connect(self.db_path)
            c = conn.cursor()

            if task_name:
                # 检查任务是否存在
                c.execute("""SELECT task_id FROM t_task 
                            WHERE group_id=? AND task_name=? AND enable=1""",
                          (group_id, task_name))
                task = c.fetchone()
                if not task:
                    return f"❌ 任务 [{task_name}] 不存在或未启用"

                task_filter = f"AND cl.task_id = {task[0]}"
                title = f"[{task_name}]"
            else:
                task_filter = ""
                title = "[全部任务]"

            # 获取排行榜数据
            c.execute(f"""
                WITH user_points AS (
                    SELECT 
                        cl.user_id,
                        COUNT(*) as checkin_count,
                        COALESCE(SUM(b.amount), 0) as bonus_points,
                        MAX(cl.checkin_time) as last_checkin
                    FROM t_checkin_log cl
                    LEFT JOIN t_bonus b ON cl.task_id = b.task_id AND cl.user_id = b.user_id
                    WHERE cl.task_id IN (SELECT task_id FROM t_task WHERE group_id=? AND enable=1)
                    {task_filter}
                    GROUP BY cl.user_id
                )
                SELECT 
                    u.name,
                    up.checkin_count,
                    up.checkin_count + up.bonus_points as total_points,
                    up.last_checkin
                FROM user_points up
                JOIN t_user u ON up.user_id = u.user_id
                ORDER BY total_points DESC, last_checkin ASC
                LIMIT 10
            """, (group_id,))

            rankings = c.fetchall()

            if not rankings:
                return f"📊 {title} 暂无打卡记录"

            # 生成排行榜消息
            message = f"📊 {title} 排行榜 TOP 10\n"
            message += "===================\n"

            for idx, (name, checkins, points, last_checkin) in enumerate(rankings, 1):
                medal = "🥇" if idx == 1 else "🥈" if idx == 2 else "🥉" if idx == 3 else "👑"
                last_time = datetime.strptime(last_checkin, '%Y-%m-%d %H:%M:%S').strftime('%m-%d %H:%M')
                message += f"{medal} {idx}. {name}\n"
                message += f"   打卡: {checkins}次 | 总积分: {points} | 最后打卡: {last_time}\n"

            return message

        except Exception as e:
            logger.exception(f"[PKTracker] 获取排行榜异常: {str(e)}")
            return "❌ 获取排行榜失败,请稍后重试"
        finally:
            conn.close()