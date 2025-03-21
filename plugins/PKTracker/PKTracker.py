# encoding:utf-8
import json
import os
import re
import sqlite3
from datetime import datetime, timedelta

import requests

import plugins
from bridge.context import ContextType
from bridge.reply import Reply, ReplyType
from common.log import logger
from lib.gewechat import GewechatClient
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

            # 读取根目录的 config.json 文件
            self.gewechat_config = self._load_root_config()
            if self.gewechat_config:
                self.app_id = self.gewechat_config.get("gewechat_app_id")
                self.base_url = self.gewechat_config.get("gewechat_base_url")
                self.token = self.gewechat_config.get("gewechat_token")
                # 初始化 GewechatClient
                self.client = GewechatClient(self.base_url, self.token)
            else:
                logger.error("[PKTracker] 无法加载根目录的 config.json 文件，GewechatClient 初始化失败")
                self.client = None
            
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
        receiver_value = context.kwargs.get("receiver")

        try:
            # 检查是否为群聊消息
            if not self.is_group_chat(receiver_value):
                reply = Reply(ReplyType.TEXT, "❌ 该功能仅支持在群聊中使用")
                e_context["reply"] = reply
                e_context.action = EventAction.BREAK_PASS
                return

            #群组id
            group_id = receiver_value
            session_id = context.kwargs.get("session_id", "")
            #用户id
            user_id = session_id.split('@@')[0]




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
                    reply_text = self.handle_checkin(user_id, group_id, task_name, content)
                    
            # 处理管理员命令
            elif command == "设置频率":
                if not self.is_admin(group_id, user_id):
                    reply_text = "只有管理员可以设置频率"
                elif len(parts) != 4:
                    reply_text = "格式错误,请使用: PKTracker 设置频率 [任务名称] [日/周/月]"
                else:
                    reply_text = self.set_frequency(group_id, parts[2], parts[3])
                    
            # 处理查询命令
            elif command == "积分排名":
                reply_text = self.get_ranking(group_id, parts[2] if len(parts) > 2 else None)
            
            # 处理任务列表命令
            elif command == "任务列表":
                reply_text = self.get_task_list(group_id)
            
            # 处理查看管理员命令
            elif command == "查看管理员":
                reply_text = self.get_admin_list(group_id)
                
            # 处理帮助命令
            elif command == "help":
                reply_text = self.get_help_text()

            # 处理添加管理员命令
            elif command == "添加管理员":
                if len(parts) != 3 or not (parts[2].startswith('[') and parts[2].endswith(']')):
                    reply_text = "格式错误,请使用: PKTracker 添加管理员 [用户名]"
                else:
                    user_name = parts[2][1:-1]  # 去掉方括号
                    u_id = self._get_user_nickname_by_nickname(user_name)
                    reply_text = self.add_admin(group_id, u_id, user_id, user_name)

            # 处理取消管理员命令
            elif command == "取消管理员":
                if len(parts) != 3 or not (parts[2].startswith('[') and parts[2].endswith(']')):
                    reply_text = "格式错误,请使用: PKTracker 取消管理员 [用户名]"
                else:
                    user_name = parts[2][1:-1]  # 去掉方括号
                    u_id = self._get_user_nickname_by_nickname(user_name)
                    reply_text = self.remove_admin(group_id, u_id, user_id, user_name)

            # 处理创建打卡任务命令
            elif command == "创建任务":
                if not self.is_admin(group_id, user_id):
                    reply_text = "只有管理员或者超级管理员可以创建任务"
                elif len(parts) < 3 or not (parts[2].startswith('[') and parts[2].endswith(']')):
                    reply_text = "格式错误,请使用: PKTracker 创建任务 [任务名称]"
                else:
                    task_name = parts[2][1:-1]  # 去掉方括号
                    reply_text = self.create_task(group_id, task_name)
        
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
        base_help = """📝 微信群打卡PK插件使用指南
    
    🔹 基础打卡指令:
      PKTracker [任务名称] 打卡内容
      例如: PKTracker [早起] 今天6点起床啦
    
    🔹 查看任务:
      - 查看任务列表:
        PKTracker 任务列表
      - 查看指定任务排名:
        PKTracker 积分排名 [任务名称]
      - 查看所有任务排名:
        PKTracker 积分排名
    
    🔹 管理员指令:
      1. 创建打卡任务:
         PKTracker 创建任务 [任务名称]
         例如: PKTracker 创建任务 [每日一练]
      2. 设置打卡频率:
         PKTracker 设置频率 [任务名称] [日/周/月]
      3. 设置提醒时间:
         PKTracker 设置提醒 [任务名称] [时间]
         时间格式: HH:MM (例如: 08:00)
      4. 查看管理员:
         PKTracker 查看管理员
    
        # 如果是超级管理员,添加超管命令说明
        if kwargs.get("user_id") and self.is_super_admin(kwargs["user_id"]):
            base_help += ""

    🔸 超级管理员指令:
      - 添加管理员:
        PKTracker 添加管理员 [用户名]
        例如: PKTracker 添加管理员 [张三]
      - 取消管理员:
        PKTracker 取消管理员 [用户名]
        例如: PKTracker 取消管理员 [张三]
    
        base_help += ""
    
    🔸 积分规则:
      - 基础打卡: 1分
      - 首次打卡: +3分
      - 连续打卡: +3分
      - 周冠军: +3分
      - 月冠军: +5分
    
    💡 Tips: 
      - 每个任务每天只能打卡一次
      - 连续打卡3天可获得额外奖励
      - 打卡内容要认真填写哦~"""
    
        return base_help

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
                        enable INTEGER DEFAULT 1,
                        create_time DATETIME DEFAULT CURRENT_TIMESTAMP,
                        update_time DATETIME DEFAULT CURRENT_TIMESTAMP)''')
    
        # 创建打卡记录表
        c.execute('''CREATE TABLE IF NOT EXISTS t_checkin_log
                       (checkin_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        task_id INTEGER,
                        user_id TEXT NOT NULL,
                        checkin_time DATETIME NOT NULL,
                        content TEXT,
                        create_time DATETIME DEFAULT CURRENT_TIMESTAMP,
                        update_time DATETIME DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY(task_id) REFERENCES t_task(task_id))''')
    
        # 创建用户表
        c.execute('''CREATE TABLE IF NOT EXISTS t_user
                       (user_id TEXT PRIMARY KEY,
                        name TEXT NOT NULL,
                        create_time DATETIME DEFAULT CURRENT_TIMESTAMP,
                        update_time DATETIME DEFAULT CURRENT_TIMESTAMP)''')
    
        # 创建管理员表
        c.execute('''CREATE TABLE IF NOT EXISTS t_admin
                       (group_id TEXT NOT NULL,
                        user_id TEXT NOT NULL,
                        create_time DATETIME DEFAULT CURRENT_TIMESTAMP,
                        update_time DATETIME DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY(group_id, user_id))''')
    
        # 创建积分表
        c.execute('''CREATE TABLE IF NOT EXISTS t_bonus
                       (bonus_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        task_id INTEGER,
                        user_id TEXT NOT NULL,
                        type TEXT CHECK(type IN ('first_checkin','consecutive','week','month')),
                        amount INTEGER NOT NULL,
                        date_awarded DATE NOT NULL,
                        create_time DATETIME DEFAULT CURRENT_TIMESTAMP,
                        update_time DATETIME DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY(task_id) REFERENCES t_task(task_id))''')
    
        # 创建触发器,用于自动更新update_time
        c.execute('''CREATE TRIGGER IF NOT EXISTS tg_task_update 
                   AFTER UPDATE ON t_task
                   BEGIN
                       UPDATE t_task SET update_time = CURRENT_TIMESTAMP
                       WHERE task_id = NEW.task_id;
                   END;''')
    
        c.execute('''CREATE TRIGGER IF NOT EXISTS tg_checkin_log_update 
                   AFTER UPDATE ON t_checkin_log
                   BEGIN
                       UPDATE t_checkin_log SET update_time = CURRENT_TIMESTAMP
                       WHERE checkin_id = NEW.checkin_id;
                   END;''')
    
        c.execute('''CREATE TRIGGER IF NOT EXISTS tg_user_update 
                   AFTER UPDATE ON t_user
                   BEGIN
                       UPDATE t_user SET update_time = CURRENT_TIMESTAMP
                       WHERE user_id = NEW.user_id;
                   END;''')
    
        c.execute('''CREATE TRIGGER IF NOT EXISTS tg_admin_update 
                   AFTER UPDATE ON t_admin
                   BEGIN
                       UPDATE t_admin SET update_time = CURRENT_TIMESTAMP
                       WHERE group_id = NEW.group_id AND user_id = NEW.user_id;
                   END;''')
    
        c.execute('''CREATE TRIGGER IF NOT EXISTS tg_bonus_update 
                   AFTER UPDATE ON t_bonus
                   BEGIN
                       UPDATE t_bonus SET update_time = CURRENT_TIMESTAMP
                       WHERE bonus_id = NEW.bonus_id;
                   END;''')
    
        conn.commit()
        conn.close()

    def is_admin(self, group_id, user_id):
        #检查用户是否是超级管理员
        if user_id in self.config.get("super_admins", []):
            return True

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
                    up.user_id,
                    up.checkin_count,
                    up.checkin_count + up.bonus_points as total_points,
                    up.last_checkin
                FROM user_points up
                ORDER BY total_points DESC, last_checkin ASC
                LIMIT 10
            """, (group_id,))

            rankings = c.fetchall()

            if not rankings:
                return f"📊 {title} 暂无打卡记录"

            # 获取所有用户的昵称
            user_ids = [row[0] for row in rankings]
            nickname_map = self._get_nickname_by_user_ids(user_ids)

            # 生成排行榜消息
            message = f"📊 {title} 排行榜 TOP 10\n"
            message += "===================\n"

            for idx, (user_id, checkins, points, last_checkin) in enumerate(rankings, 1):
                medal = "🥇" if idx == 1 else "🥈" if idx == 2 else "🥉" if idx == 3 else "👑"
                last_time = datetime.strptime(last_checkin, '%Y-%m-%d %H:%M:%S').strftime('%m-%d %H:%M')
                nickname = nickname_map.get(user_id, user_id)
                message += f"{medal} {idx}. {nickname}\n"
                message += f"   打卡: {checkins}次 | 总积分: {points} | 最后打卡: {last_time}\n"

            return message

        except Exception as e:
            logger.exception(f"[PKTracker] 获取排行榜异常: {str(e)}")
            return "❌ 获取排行榜失败,请稍后重试"
        finally:
            conn.close()

    def create_task(self, group_id: str, task_name: str) -> str:
        """创建新的打卡任务
        Args:
            group_id: 群组ID
            task_name: 任务名称
        Returns:
            str: 创建结果提示
        """
        try:
            conn = sqlite3.connect(self.db_path)
            c = conn.cursor()
            
            # 检查任务名是否已存在
            c.execute("""SELECT 1 FROM t_task 
                        WHERE group_id=? AND task_name=?""", 
                     (group_id, task_name))
            if c.fetchone():
                return f"❌ 任务 [{task_name}] 已存在"
                
            # 创建新任务
            c.execute("""INSERT INTO t_task 
                        (group_id, task_name, frequency, enable) 
                        VALUES (?, ?, 'day', 1)""", 
                     (group_id, task_name))
            
            conn.commit()
            return f"""✅ 任务 [{task_name}] 创建成功!
    🔸 默认设置:
      - 打卡频率: 每日
      - 首次打卡奖励: +3分
      - 连续打卡奖励: +3分
    可使用以下命令修改设置:
      - PKTracker 设置频率 [{task_name}] [日/周/月]
      - PKTracker 设置提醒 [{task_name}] [时间]"""
            
        except Exception as e:
            logger.exception(f"[PKTracker] 创建任务异常: {str(e)}")
            return "❌ 创建任务失败,请稍后重试"
        finally:
            conn.close()

    def is_super_admin(self, user_id: str) -> bool:
        """检查用户是否为超级管理员"""
        return user_id in self.config.get("super_admins", [])
    
    def add_admin(self, group_id: str, user_id: str, operator_id: str, user_name: str) -> str:
        """添加管理员
        Args:
            group_id: 群组ID
            user_id: 被添加的用户ID
            operator_id: 操作者ID
        Returns:
            str: 操作结果提示
        """
        if not self.is_super_admin(operator_id):
            return "❌ 只有超级管理员才能添加管理员"
            
        try:
            conn = sqlite3.connect(self.db_path)
            c = conn.cursor()
            
            # 检查是否已经是管理员
            c.execute("SELECT 1 FROM t_admin WHERE group_id=? AND user_id=?", 
                     (group_id, user_id))
            if c.fetchone():
                return f"❌ 用户 {user_name} 已经是管理员了"
                
            # 添加管理员
            c.execute("INSERT INTO t_admin (group_id, user_id) VALUES (?, ?)",
                     (group_id, user_id))
            
            conn.commit()
            admin_list = self.get_admin_list(group_id)
            return f"✅ 已将用户 {user_name} 设置为管理员\n\n{admin_list}"
            
        except Exception as e:
            logger.exception(f"[PKTracker] 添加管理员异常: {str(e)}")
            return "❌ 添加管理员失败,请稍后重试"
        finally:
            conn.close()
    
    def remove_admin(self, group_id: str, user_id: str, operator_id: str, user_name: str) -> str:
        """取消管理员
        Args:
            group_id: 群组ID
            user_id: 被取消的用户ID
            operator_id: 操作者ID
            user_name: 用户名称
        Returns:
            str: 操作结果提示
        """
        if not self.is_super_admin(operator_id):
            return "❌ 只有超级管理员才能取消管理员"
            
        try:
            conn = sqlite3.connect(self.db_path)
            c = conn.cursor()
            
            # 检查是否是超级管理员
            if user_id in self.config.get("super_admins", []):
                return "❌ 无法取消超级管理员的权限"
            
            # 检查是否是管理员
            c.execute("SELECT 1 FROM t_admin WHERE group_id=? AND user_id=?", 
                     (group_id, user_id))
            if not c.fetchone():
                return f"❌ 用户 {user_name} 不是管理员"
                
            # 取消管理员
            c.execute("DELETE FROM t_admin WHERE group_id=? AND user_id=?",
                     (group_id, user_id))
            
            conn.commit()
            admin_list = self.get_admin_list(group_id)
            return f"✅ 已取消用户 {user_name} 的管理员权限\n\n{admin_list}"
            
        except Exception as e:
            logger.exception(f"[PKTracker] 取消管理员异常: {str(e)}")
            return "❌ 取消管理员失败,请稍后重试"
        finally:
            conn.close()

    def is_group_chat(self, chat_id: str) -> bool:
        """判断是否是群聊消息
        Args:
            chat_id: 聊天ID
        Returns:
            bool: 是否为群聊
        """
        return chat_id.endswith('@chatroom')

    def _load_root_config(self):
        """加载根目录的 config.json 文件"""
        try:
            root_config_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "config.json")
            if os.path.exists(root_config_path):
                with open(root_config_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            else:
                logger.error(f"[PKTracker] 根目录的 config.json 文件不存在: {root_config_path}")
                return None
        except Exception as e:
            logger.error(f"[PKTracker] 加载根目录的 config.json 文件失败: {e}")
            return None

    def _get_user_nickname_by_nickname(self, nickname):
        """根据昵称或备注名获取用户 ID"""
        try:
            # 获取所有联系人列表
            contacts_response = self.client.fetch_contacts_list(self.app_id)
            print(f"[PKTracker] fetch_contacts_list 返回数据: {contacts_response}")  # 打印返回数据
            if contacts_response.get('ret') == 200:
                # 提取好友的 wxid 列表
                wxids = contacts_response.get('data', {}).get('friends', [])
                print(f"[PKTracker] 提取的 wxids: {wxids}")  # 打印提取的 wxids

                # 如果 wxids 为空，直接返回 None
                if not wxids:
                    logger.error("[PKTracker] 未找到有效的 wxid")
                    return None

                # 分批获取详细信息（每次最多 20 个 wxid）
                for i in range(0, len(wxids), 20):
                    batch_wxids = wxids[i:i + 20]  # 每次最多 20 个 wxid
                    # 获取当前批次的详细信息
                    detail_response = self.client.get_detail_info(self.app_id, batch_wxids)
                    print(f"[PKTracker] get_detail_info 返回数据: {detail_response}")  # 打印详细信息
                    if detail_response.get('ret') == 200:
                        details = detail_response.get('data', [])
                        # 遍历详细信息，查找匹配的昵称或备注名
                        for detail in details:
                            # 检查昵称或备注名是否匹配
                            if detail.get('nickName') == nickname or detail.get('remark') == nickname:
                                return detail.get('userName')  # 返回 wxid
        except Exception as e:
            logger.error(f"[PKTracker] 获取用户信息失败: {e}")
            return None

    def _get_user_nickname(self, user_id):
        """获取用户昵称"""
        try:
            response = requests.post(
                f"{conf().get('gewechat_base_url')}/contacts/getBriefInfo",
                json={
                    "appId": conf().get('gewechat_app_id'),
                    "wxids": [user_id]
                },
                headers={
                    "X-GEWE-TOKEN": conf().get('gewechat_token')
                }
            )
            if response.status_code == 200:
                data = response.json()
                if data.get('ret') == 200 and data.get('data'):
                    return data['data'][0].get('nickName', user_id)
            return user_id
        except Exception as e:
            logger.error(f"[PKTracker] 获取用户昵称失败: {e}")
            return user_id

    def _get_nickname_by_user_ids(self, user_ids):
        """批量获取用户昵称"""
        if not user_ids:
            return {}

        try:
            response = requests.post(
                f"{conf().get('gewechat_base_url')}/contacts/getBriefInfo",
                json={
                    "appId": conf().get('gewechat_app_id'),
                    "wxids": user_ids
                },
                headers={
                    "X-GEWE-TOKEN": conf().get('gewechat_token')
                }
            )

            if response.status_code == 200:
                data = response.json()
                if data.get('ret') == 200 and data.get('data'):
                    # 构造 user_id -> nickName 映射
                    user_map = {item.get("userName", uid): item.get("nickName", uid) for item, uid in
                                zip(data['data'], user_ids)}
                    return user_map

            # 如果请求失败或数据不完整，返回默认映射
            return {uid: uid for uid in user_ids}

        except Exception as e:
            logger.error(f"[PKTracker] 批量获取用户昵称失败: {e}")
            return {uid: uid for uid in user_ids}

    def get_task_list(self, group_id: str) -> str:
        """获取群内任务列表
        Args:
            group_id: 群组ID
        Returns:
            str: 任务列表信息
        """
        try:
            conn = sqlite3.connect(self.db_path)
            c = conn.cursor()
            
            # 获取所有任务信息
            c.execute("""
                SELECT 
                    task_name,
                    frequency,
                    first_checkin_reward_enabled,
                    first_checkin_reward,
                    consecutive_checkin_reward_enabled,
                    consecutive_checkin_reward,
                    reminder_time,
                    enable,
                    (SELECT COUNT(*) FROM t_checkin_log WHERE task_id = t.task_id) as total_checkins
                FROM t_task t
                WHERE group_id = ?
                ORDER BY enable DESC, task_name ASC
            """, (group_id,))
            
            tasks = c.fetchall()
            
            if not tasks:
                return "📝 当前群组暂无任务"
                
            # 生成任务列表消息
            message = "📝 任务列表\n==================="
            
            for task in tasks:
                (name, freq, first_enabled, first_reward, 
                 consec_enabled, consec_reward, reminder, enable, total_checkins) = task
                
                # 转换频率显示
                freq_map = {"day": "每日", "week": "每周", "month": "每月"}
                freq_text = freq_map.get(freq, freq)
                
                # 状态emoji
                status = "✅" if enable else "❌"
                
                message += f"\n\n{status} [{name}]"
                message += f"\n🔸 打卡频率: {freq_text}"
                message += f"\n🔸 总打卡次数: {total_checkins}次"
                
                # 奖励信息
                rewards = []
                if first_enabled:
                    rewards.append(f"首次打卡+{first_reward}分")
                if consec_enabled:
                    rewards.append(f"连续打卡+{consec_reward}分")
                message += f"\n🔸 奖励设置: {', '.join(rewards)}"
                
                # 提醒时间
                if reminder:
                    message += f"\n🔸 提醒时间: {reminder}"
                    
            return message
            
        except Exception as e:
            logger.exception(f"[PKTracker] 获取任务列表异常: {str(e)}")
            return "❌ 获取任务列表失败,请稍后重试"
        finally:
            conn.close()

    def get_admin_list(self, group_id: str) -> str:
        """获取群内管理员列表
        Args:
            group_id: 群组ID
        Returns:
            str: 管理员列表信息
        """
        try:
            conn = sqlite3.connect(self.db_path)
            c = conn.cursor()
            
            # 获取所有管理员ID
            c.execute("""SELECT user_id FROM t_admin WHERE group_id=?""", (group_id,))
            admin_ids = [row[0] for row in c.fetchall()]
            
            # 获取超级管理员ID
            super_admin_ids = self.config.get("super_admins", [])
            
            # 合并所有管理员ID
            all_admin_ids = list(set(admin_ids + super_admin_ids))
            
            if not all_admin_ids:
                return "👥 当前群组暂无管理员"
            
            # 获取管理员昵称
            nickname_map = self._get_nickname_by_user_ids(all_admin_ids)
            
            # 生成管理员列表消息
            message = "👥 管理员列表\n==================="
            
            # 先显示超级管理员
            for user_id in super_admin_ids:
                if user_id in nickname_map and nickname_map[user_id]:  # 添加昵称非空检查
                    message += f"\n\n👑 超级管理员: {nickname_map[user_id]}"
            
            # 显示普通管理员
            for user_id in admin_ids:
                if user_id not in super_admin_ids and user_id in nickname_map and nickname_map[user_id]:  # 添加昵称非空检查
                    message += f"\n\n⭐ 管理员: {nickname_map[user_id]}"
                    
            return message
                
        except Exception as e:
            logger.exception(f"[PKTracker] 获取管理员列表异常: {str(e)}")
            return "❌ 获取管理员列表失败,请稍后重试"
        finally:
            conn.close()