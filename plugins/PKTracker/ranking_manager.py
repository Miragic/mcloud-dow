import sqlite3
from datetime import datetime
from common.log import logger

class RankingManager:
    def __init__(self, db_path, user_manager):
        self.db_path = db_path
        self.user_manager = user_manager

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

            # 修改查询以合并相同任务的统计
            c.execute(f"""
                WITH task_points AS (
                    SELECT 
                        cl.user_id,
                        t.task_name,
                        COUNT(*) as task_checkin_count,
                        COUNT(*) + COALESCE(SUM(b.amount), 0) as task_total_points
                    FROM t_checkin_log cl
                    JOIN t_task t ON cl.task_id = t.task_id
                    LEFT JOIN t_bonus b ON cl.task_id = b.task_id AND cl.user_id = b.user_id
                    WHERE t.group_id = ? AND t.enable = 1 {task_filter}
                    GROUP BY cl.user_id, t.task_id, t.task_name
                ),
                user_points AS (
                    SELECT 
                        cl.user_id,
                        COUNT(*) as total_checkins,
                        COALESCE(SUM(b.amount), 0) as bonus_points,
                        MAX(cl.checkin_time) as last_checkin,
                        GROUP_CONCAT(DISTINCT tp.task_name || ':' || tp.task_checkin_count || ':' || tp.task_total_points) as task_details
                    FROM t_checkin_log cl
                    LEFT JOIN t_bonus b ON cl.task_id = b.task_id AND cl.user_id = b.user_id
                    LEFT JOIN task_points tp ON cl.user_id = tp.user_id
                    WHERE cl.task_id IN (SELECT task_id FROM t_task WHERE group_id=? AND enable=1) {task_filter}
                    GROUP BY cl.user_id
                )
                SELECT 
                    up.user_id,
                    up.total_checkins,
                    up.total_checkins + up.bonus_points as total_points,
                    up.last_checkin,
                    up.task_details
                FROM user_points up
                ORDER BY total_points DESC, last_checkin ASC
                LIMIT 10
            """, (group_id, group_id))

            rankings = c.fetchall()

            if not rankings:
                return f"📊 {title} 暂无打卡记录"

            # 获取所有用户的昵称
            user_ids = [row[0] for row in rankings]
            nickname_map = self.user_manager._get_nickname_by_user_ids(user_ids)

            # 生成排行榜消息
            message = f"📊 {title} 排行榜 TOP 10\n"
            message += "===================\n"

            # 修改排行榜消息生成部分
            for idx, (user_id, checkins, points, last_checkin, task_details) in enumerate(rankings, 1):
                medal = "🥇" if idx == 1 else "🥈" if idx == 2 else "🥉" if idx == 3 else "👑"
                last_time = datetime.strptime(last_checkin, '%Y-%m-%d %H:%M:%S').strftime('%Y-%m-%d %H:%M:%S')
                nickname = nickname_map.get(user_id, user_id)

                message += f"{medal} {idx}. {nickname}\n"
                message += f"   总打卡: {checkins}次 | 总积分: {points}\n"

                # 添加各任务打卡和积分详情
                if task_details:
                    task_list = []
                    for task_info in task_details.split(','):
                        task_name, count, task_points = task_info.split(':')
                        task_list.append(f"[{task_name}]{count}次/{task_points}分")
                    message += f"   任务详情: {' '.join(task_list)}\n"

                message += f"   最后打卡: {last_time}\n"

            return message

        except Exception as e:
            logger.exception(f"[PKTracker] 获取排行榜异常: {str(e)}")
            return "❌ 获取排行榜失败,请稍后重试"
        finally:
            conn.close()