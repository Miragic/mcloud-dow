import sqlite3
from common.log import logger

class TaskManager:
    def __init__(self, db_path):
        self.db_path = db_path

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

    def create_task(self, group_id: str, task_name: str) -> str:
        try:
            conn = sqlite3.connect(self.db_path)
            c = conn.cursor()

            # 检查任务名是否已存在
            c.execute("""SELECT 1 FROM t_task 
                        WHERE group_id=? AND task_name=?""",
                      (group_id, task_name))
            if c.fetchone():
                return f"❌ 任务 [{task_name}] 已存在"

            # 创建新任务,设置默认值
            c.execute("""INSERT INTO t_task 
                        (group_id, task_name, frequency, max_checkins, enable) 
                        VALUES (?, ?, 'day', 1, 1)""",
                      (group_id, task_name))

            conn.commit()
            return f"""✅ 任务 [{task_name}] 创建成功!
    🔸 默认设置:
      - 打卡频率: 每日
      - 打卡次数: 1次
      - 首次打卡奖励: +3分
      - 连续打卡奖励: +3分
    可使用以下命令修改设置:
      - PKTracker 设置频率 [{task_name}] [日/周/月]
      - PKTracker 设置次数 [{task_name}] [次数]
      - PKTracker 设置提醒 [{task_name}] [时间]"""

        except Exception as e:
            logger.exception(f"[PKTracker] 创建任务异常: {str(e)}")
            return "❌ 创建任务失败,请稍后重试"
        finally:
            conn.close()

    def set_max_checkins(self, group_id: str, task_name: str, max_checkins: int) -> str:
        """设置任务打卡次数限制
        Args:
            group_id: 群组ID
            task_name: 任务名称
            max_checkins: 最大打卡次数
        Returns:
            str: 设置结果提示
        """
        if max_checkins < 1:
            return "❌ 打卡次数必须大于0"

        try:
            conn = sqlite3.connect(self.db_path)
            c = conn.cursor()

            # 检查任务是否存在
            c.execute("""SELECT task_id FROM t_task 
                        WHERE group_id=? AND task_name=?""",
                      (group_id, task_name))
            if not c.fetchone():
                return f"❌ 任务 [{task_name}] 不存在"

            # 更新打卡次数
            c.execute("""UPDATE t_task 
                        SET max_checkins=? 
                        WHERE group_id=? AND task_name=?""",
                      (max_checkins, group_id, task_name))

            conn.commit()
            return f"✅ 成功设置任务 [{task_name}] 的最大打卡次数为: {max_checkins}"

        except Exception as e:
            logger.exception(f"[PKTracker] 设置打卡次数异常: {str(e)}")
            return "❌ 设置失败,请稍后重试"
        finally:
            conn.close()