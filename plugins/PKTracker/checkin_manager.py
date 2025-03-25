from datetime import datetime, timedelta
import sqlite3
from common.log import logger

class CheckinManager:
    def __init__(self, db_path):
        self.db_path = db_path

    def handle_checkin(self, user_id, group_id, task_name, content):
        """处理打卡"""
        try:
            conn = sqlite3.connect(self.db_path)
            c = conn.cursor()

            # 检查任务是否存在
            c.execute("""SELECT task_id, frequency, max_checkins FROM t_task 
                        WHERE group_id=? AND task_name=? AND enable=1""",
                      (group_id, task_name))
            task = c.fetchone()
            if not task:
                return f"任务 [{task_name}] 不存在或未启用"

            task_id, frequency, max_checkins = task
            now = datetime.now()

            # 根据频率检查打卡次数
            if frequency == 'day':
                date_start = now.strftime('%Y-%m-%d')
                date_end = (now + timedelta(days=1)).strftime('%Y-%m-%d')
            elif frequency == 'week':
                date_start = (now - timedelta(days=now.weekday())).strftime('%Y-%m-%d')
                date_end = (now - timedelta(days=now.weekday()) + timedelta(days=7)).strftime('%Y-%m-%d')
            else:  # month
                date_start = now.strftime('%Y-%m-01')
                if now.month == 12:
                    date_end = f"{now.year + 1}-01-01"
                else:
                    date_end = f"{now.year}-{now.month + 1:02d}-01"

            # 检查当前周期内的打卡次数
            c.execute("""SELECT COUNT(*) FROM t_checkin_log 
                        WHERE task_id=? AND user_id=? 
                        AND checkin_time >= ? AND checkin_time < ?""",
                      (task_id, user_id, date_start, date_end))
            current_checkins = c.fetchone()[0]

            if current_checkins >= max_checkins:
                period_map = {'day': '今日', 'week': '本周', 'month': '本月'}
                return f"❌ {period_map[frequency]}已达到最大打卡次数 ({max_checkins}次)"

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