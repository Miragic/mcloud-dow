# encoding:utf-8
import json
import os

import plugins
from bridge.context import ContextType
from bridge.reply import Reply, ReplyType
from common.log import logger
from lib.gewechat import GewechatClient
from plugins import Plugin, EventContext, EventAction, Event
from plugins.PKTracker.admin_manager import AdminManager
from plugins.PKTracker.checkin_manager import CheckinManager
from plugins.PKTracker.database import DatabaseManager
from plugins.PKTracker.ranking_manager import RankingManager
from plugins.PKTracker.task_manager import TaskManager
from plugins.PKTracker.user_manager import UserManager


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
            self.db_manager = DatabaseManager(self.db_path)
            
            # 初始化客户端
            self._init_client()
            
            # 初始化各个管理器
            self.task_manager = TaskManager(self.db_path)
            self.checkin_manager = CheckinManager(self.db_path)
            self.user_manager = UserManager(self.client, self.app_id)
            self.admin_manager = AdminManager(self.db_path, self.config, self.user_manager)
            self.ranking_manager = RankingManager(self.db_path, self.user_manager)
            
            # 注册事件处理器
            self.handlers[Event.ON_HANDLE_CONTEXT] = self.on_handle_context
            
            logger.info("[PKTracker] 初始化成功")
        except Exception as e:
            logger.error(f"[PKTracker] 初始化异常：{e}")
            raise "[PKTracker] init failed, ignore "

    def _init_client(self):
        """初始化微信客户端"""
        self.gewechat_config = self._load_root_config()
        if self.gewechat_config:
            self.app_id = self.gewechat_config.get("gewechat_app_id")
            self.base_url = self.gewechat_config.get("gewechat_base_url")
            self.token = self.gewechat_config.get("gewechat_token")
            self.client = GewechatClient(self.base_url, self.token)
        else:
            logger.error("[PKTracker] 无法加载根目录的 config.json 文件，GewechatClient 初始化失败")
            self.client = None

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

    def is_group_chat(self, chat_id: str) -> bool:
        """判断是否是群聊消息"""
        return chat_id.endswith('@chatroom')

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

            group_id = receiver_value
            session_id = context.kwargs.get("session_id", "")
            user_id = session_id.split('@@')[0]

            # 解析命令
            parts = content.split()
            if len(parts) < 2:
                reply = Reply(ReplyType.TEXT, "格式错误,请输入正确的指令")
                e_context["reply"] = reply
                e_context.action = EventAction.BREAK_PASS
                return
                
            command = parts[1]
            reply_text = self.handle_command(command, parts, user_id, group_id)
            
            reply = Reply(ReplyType.TEXT, reply_text)
            e_context["reply"] = reply
            e_context.action = EventAction.BREAK_PASS
            
        except Exception as e:
            logger.exception(f"[PKTracker] 处理消息异常: {str(e)}")
            reply = Reply(ReplyType.ERROR, "处理命令时出错,请稍后再试")
            e_context["reply"] = reply
            e_context.action = EventAction.BREAK_PASS

    def handle_command(self, command, parts, user_id, group_id):
        """处理各种命令"""
        # 处理打卡命令
        if command.startswith("[") and command.endswith("]"):
            task_name = command[1:-1]
            if len(parts) < 3:
                return "请输入打卡内容"
            content = " ".join(parts[2:])
            return self.checkin_manager.handle_checkin(user_id, group_id, task_name, content)
                
        # 处理管理员命令
        elif command == "设置频率":
            if not self.admin_manager.is_admin(group_id, user_id):
                return "只有管理员可以设置频率"
            elif len(parts) != 4:
                return "格式错误,请使用: PKTracker 设置频率 [任务名称] [日/周/月]"
            #取[]内的值
            parts[2] = parts[2].replace('[', '').replace(']', '')
            parts[3] = parts[3].replace('[', '').replace(']', '')
            return self.task_manager.set_frequency(group_id, parts[2], parts[3])
                    
        # 处理查询命令
        elif command == "积分榜":
            return self.ranking_manager.get_ranking(group_id, parts[2] if len(parts) > 2 else None)
            
        # 处理任务列表命令
        elif command == "任务列表":
            return self.task_manager.get_task_list(group_id)
            
        # 处理查看管理员命令
        elif command == "查看管理员":
            return self.admin_manager.get_admin_list(group_id)
                
        # 处理帮助命令
        elif command == "help":
            return self.get_help_text()

        # 处理添加管理员命令
        elif command == "添加管理员":
            if len(parts) != 3 or not (parts[2].startswith('[') and parts[2].endswith(']')):
                return "格式错误,请使用: PKTracker 添加管理员 [用户名]"
            user_name = parts[2][1:-1]
            # 获取用户ID
            u_id = self.user_manager._get_user_id_by_nickname(user_name)
            if not u_id:
                return f"❌ 未找到用户 [{user_name}]"
            return self.admin_manager.add_admin(group_id, u_id, user_id, user_name)

        # 处理取消管理员命令
        elif command == "取消管理员":
            if len(parts) != 3 or not (parts[2].startswith('[') and parts[2].endswith(']')):
                return "格式错误,请使用: PKTracker 取消管理员 [用户名]"
            user_name = parts[2][1:-1]
            # 获取用户ID
            u_id = self.user_manager._get_user_id_by_nickname(user_name)
            if not u_id:
                return f"❌ 未找到用户 [{user_name}]"
            return self.admin_manager.remove_admin(group_id, u_id, user_id, user_name)

        # 处理创建打卡任务命令
        elif command == "创建任务":
            if not self.admin_manager.is_admin(group_id, user_id):
                return "只有管理员或者超级管理员可以创建任务"
            elif len(parts) < 3 or not (parts[2].startswith('[') and parts[2].endswith(']')):
                return "格式错误,请使用: PKTracker 创建任务 [任务名称]"
            task_name = parts[2][1:-1]
            return self.task_manager.create_task(group_id, task_name)
        
        elif command == "设置次数":
            if not self.admin_manager.is_admin(group_id, user_id):
                return "只有管理员可以设置打卡次数"
            elif len(parts) != 4 or not (parts[2].startswith('[') and parts[2].endswith(']')):
                return "格式错误,请使用: PKTracker 设置次数 [任务名称] [次数]"
            try:
                task_name = parts[2][1:-1]
                max_checkins = int(parts[3])
                return self.task_manager.set_max_checkins(group_id, task_name, max_checkins)
            except ValueError:
                return "❌ 次数必须是整数且大于0"
        
        else:
            return "未知命令,请检查输入"

    def get_help_text(self, **kwargs):
        base_help = """📝 微信群打卡PK插件使用指南

    🔹 基础打卡指令:
      PKTracker [任务名称] 打卡内容
      例如: PKTracker [早起] 今天6点起床啦

    🔹 查看任务:
      - 查看任务列表:
        PKTracker 任务列表
      - 查看指定任务排名:
        PKTracker 积分榜 [任务名称]
      - 查看所有任务排名:
        PKTracker 积分榜

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