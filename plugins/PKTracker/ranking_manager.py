import sqlite3
from datetime import datetime
from common.log import logger

class RankingManager:
    def __init__(self, db_path, user_manager):
        self.db_path = db_path
        self.user_manager = user_manager

    def get_user_bonus_detail(self, group_id: str, user_name: str = None, sender_id: str = None, page: int = 1) -> str:
        """获取用户的积分详情
        
        Args:
            group_id: 群组ID
            user_name: 用户名称，可选
            sender_id: 发送者ID，可选
            page: 页码，默认为1
            
        Returns:
            str: 积分详情信息
        """
        try:
            # 如果没有指定用户名，则查询发送者的积分详情
            user_id = sender_id
            display_name = "你"
            if user_name:
                user_id = self.user_manager._get_user_id_by_nickname(user_name)
                if not user_id:
                    return f"❌ 未找到用户 [{user_name}]"
                display_name = user_name

            conn = sqlite3.connect(self.db_path)
            c = conn.cursor()

            # 先获取总记录数
            c.execute("""
                SELECT COUNT(*)
                FROM t_checkin_log cl
                JOIN t_task t ON cl.task_id = t.task_id
                WHERE t.group_id = ? AND cl.user_id = ? AND t.enable = 1
            """, (group_id, user_id))
            
            total_records = c.fetchone()[0]
            page_size = 5  # 修改为每页5条
            total_pages = (total_records + page_size - 1) // page_size
            
            # 确保页码有效
            page = max(1, min(page, total_pages)) if total_pages > 0 else 1
            offset = (page - 1) * page_size

            # 获取分页数据
            c.execute("""
                SELECT 
                    t.task_name,
                    cl.checkin_time,
                    cl.content,
                    SUM(b.bonus_value) as total_bonus
                FROM t_checkin_log cl
                JOIN t_task t ON cl.task_id = t.task_id
                LEFT JOIN t_bonus b ON cl.checkin_id = b.checkin_id
                WHERE t.group_id = ? AND cl.user_id = ? AND t.enable = 1
                GROUP BY cl.checkin_id
                ORDER BY cl.checkin_time DESC
                LIMIT ? OFFSET ?
            """, (group_id, user_id, page_size, offset))

            records = c.fetchall()
            
            if not records:
                if page > 1:
                    return f"❌ 第{page}页没有记录"
                return f"📊 {display_name} 暂无打卡记录"

            message = f"📊 {display_name}的打卡记录 (第{page}/{total_pages}页)\n"
            message += "===================\n\n"

            for task_name, checkin_time, content, total_bonus in records:
                message += f"[{task_name}] {checkin_time} (+{total_bonus}分)\n"
                if content:
                    message += f"内容: {content}\n"
                message += "\n"

            return message

        except Exception as e:
            logger.exception(f"[PKTracker] 获取用户积分详情异常: {str(e)}")
            return "❌ 获取用户积分详情失败,请稍后重试"
        finally:
            if 'conn' in locals() and conn is not None:
                conn.close()

    def get_ranking(self, group_id: str, task_name: str = None) -> str:
        try:
            conn = sqlite3.connect(self.db_path)
            c = conn.cursor()

            # 检查任务是否存在
            if task_name:
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

            # 修改查询以使用新的积分表结构
            c.execute(f"""
                WITH task_points AS (
                    SELECT 
                        cl.user_id,
                        t.task_name,
                        COUNT(*) as task_checkin_count,
                        SUM(COALESCE(b.bonus_value, 0)) as task_total_points
                    FROM t_checkin_log cl
                    JOIN t_task t ON cl.task_id = t.task_id
                    LEFT JOIN t_bonus b ON cl.checkin_id = b.checkin_id
                    WHERE t.group_id = ? AND t.enable = 1 {task_filter}
                    GROUP BY cl.user_id, t.task_id, t.task_name
                ),
                user_points AS (
                    SELECT 
                        cl.user_id,
                        COUNT(DISTINCT cl.checkin_id) as total_checkins,
                        SUM(COALESCE(b.bonus_value, 0)) as total_points,
                        MAX(cl.checkin_time) as last_checkin,
                        GROUP_CONCAT(DISTINCT tp.task_name || ':' || tp.task_checkin_count || ':' || tp.task_total_points) as task_details
                    FROM t_checkin_log cl
                    JOIN t_task t ON cl.task_id = t.task_id
                    LEFT JOIN t_bonus b ON cl.checkin_id = b.checkin_id
                    LEFT JOIN task_points tp ON cl.user_id = tp.user_id
                    WHERE t.group_id = ? AND t.enable = 1 {task_filter}
                    GROUP BY cl.user_id
                )
                SELECT 
                    up.user_id,
                    up.total_checkins,
                    up.total_points,
                    up.last_checkin,
                    up.task_details
                FROM user_points up
                ORDER BY up.total_points DESC, up.last_checkin ASC
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