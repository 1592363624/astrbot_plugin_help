import collections
from typing import Dict, List, Optional

from astrbot.api import logger
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.core.config.astrbot_config import AstrBotConfig
from astrbot.core.message.components import Image
from astrbot.core.star.filter.command import CommandFilter
from astrbot.core.star.filter.command_group import CommandGroupFilter
from astrbot.core.star.star_handler import star_handlers_registry, StarHandlerMetadata
from .draw import AstrBotHelpDrawer


@register(
    "astrbot_plugin_help", "tinker", "查看所有命令，包括插件，返回一张帮助图片", "1.1.3"
)
class MyPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.drawer = AstrBotHelpDrawer(config)
        # 解析白名单配置
        self.whitelist_commands = self._parse_whitelist_config()
        # 关键：在初始化时调用动态创建方法
        self._create_whitelist_handlers()

    def _parse_whitelist_config(self) -> Dict[str, str]:
        """解析白名单配置，返回 {触发指令: 插件名} 的映射"""
        plugin_whitelist = getattr(self.config, "plugin_whitelist", []) if self.config is not None else []
        whitelist_config = {}

        for item in plugin_whitelist:
            if isinstance(item, str) and '-' in item:
                plugin_name, trigger_command = item.split('-', 1)
                whitelist_config[trigger_command.strip()] = plugin_name.strip()

        logger.info(f"解析白名单配置: {whitelist_config}")
        return whitelist_config

    def _create_whitelist_handlers(self):
        """动态创建白名单命令处理器"""
        for command, plugin_name in self.whitelist_commands.items():
            # 使用闭包正确绑定变量
            def make_handler(plugin_name):
                async def whitelist_handler(self, event: AstrMessageEvent):
                    """白名单插件帮助处理器"""
                    try:
                        help_msg = self.get_plugin_whitelist_commands()

                        # 只返回指定插件的帮助信息
                        if plugin_name in help_msg:
                            plugin_help = {plugin_name: help_msg[plugin_name]}
                            image = self.drawer.draw_help_image(plugin_help)
                            yield event.chain_result([Image.fromBytes(image)])
                        else:
                            yield event.plain_result(f"插件 {plugin_name} 未找到或未激活")
                    except Exception as e:
                        logger.error(f"处理白名单插件 {plugin_name} 帮助时出错: {e}")
                        yield event.plain_result("获取帮助信息时出错")

                return whitelist_handler

            # 创建处理器函数
            handler_func = make_handler(plugin_name)

            # 应用装饰器
            decorated_handler = filter.command(command)(handler_func)

            # 将装饰后的方法绑定到实例
            handler_name = f"whitelist_{command.replace(' ', '_')}"
            setattr(self, handler_name, decorated_handler.__get__(self, self.__class__))
            logger.info(f"已创建白名单命令处理器: {command} -> {plugin_name}")

    @filter.command("帮助", alias={"菜单", "功能"})
    async def get_help(self, event: AstrMessageEvent):
        """获取插件帮助信息"""
        raw = getattr(event.message_obj, "raw_message", None)
        # 仅白名单成员可以使用该命令,如果白名单为空则全部放行
        whitelist = getattr(self.config, "whitelist", []) if self.config is not None else []
        user_id = raw.get("user_id", None)
        if whitelist and user_id is not None:
            # 统一转换为字符串进行比较，确保类型兼容
            user_id_str = str(user_id)
            whitelist_str = [str(uid) for uid in whitelist]
            if user_id_str not in whitelist_str:
                logger.info(f"用户 {user_id_str} 不在白名单中，无法使用命令helps")
                return

        help_msg = self.get_all_commands()
        if not help_msg:
            yield event.plain_result("没有找到任何插件或命令")
            return
        image = self.drawer.draw_help_image(help_msg)
        yield event.chain_result([Image.fromBytes(image)])

    def get_all_commands(self) -> Dict[str, List[str]]:
        """获取所有其他插件及其命令列表, 格式为 {plugin_name: [command#desc]}"""
        plugin_commands: Dict[str, List[str]] = collections.defaultdict(list)
        try:
            all_stars_metadata = self.context.get_all_stars()
            all_stars_metadata = [star for star in all_stars_metadata if star.activated]
        except Exception as e:
            logger.error(f"获取插件列表失败: {e}")
            return {}
        if not all_stars_metadata:
            logger.warning("没有找到任何插件")
            return {}
        for star in all_stars_metadata:
            plugin_name = getattr(star, "name", "未知插件")
            plugin_instance = getattr(star, "star_cls", None)
            module_path = getattr(star, "module_path", None)
            if (
                    plugin_name == "astrbot"
                    or plugin_name == "astrbot_plugin_help"
                    or plugin_name == "astrbot-reminder"
            ):
                continue
            if (
                    not plugin_name
                    or not module_path
                    or not isinstance(plugin_instance, Star)
            ):
                logger.warning(
                    f"插件 '{plugin_name}' (模块: {module_path}) 的元数据无效或不完整，已跳过。"
                )
                continue
            if plugin_instance is self:
                continue
            for handler in star_handlers_registry:
                if not isinstance(handler, StarHandlerMetadata):
                    continue
                if handler.handler_module_path != module_path:
                    continue
                command_name: Optional[str] = None
                description: Optional[str] = handler.desc
                for filter_ in handler.event_filters:
                    if isinstance(filter_, CommandFilter):
                        command_name = filter_.command_name
                        break
                    elif isinstance(filter_, CommandGroupFilter):
                        command_name = filter_.group_name
                        break
                if command_name:
                    if description:
                        formatted_command = f"{command_name}#{description}"
                    else:
                        formatted_command = command_name
                    if formatted_command not in plugin_commands[plugin_name]:
                        plugin_commands[plugin_name].append(formatted_command)
        return dict(plugin_commands)

    def get_plugin_whitelist_commands(self) -> Dict[str, List[str]]:
        """获取白名单插件的命令信息"""
        plugin_commands: Dict[str, List[str]] = collections.defaultdict(list)

        plugin_whitelist = getattr(self.config, "plugin_whitelist", []) if self.config is not None else []

        if not plugin_whitelist:
            logger.info("插件白名单为空")
            return {}

        try:
            all_stars_metadata = self.context.get_all_stars()
            all_stars_metadata = [star for star in all_stars_metadata if star.activated]
        except Exception as e:
            logger.error(f"获取插件列表失败: {e}")
            return {}

        whitelist_config = {}
        for item in plugin_whitelist:
            if isinstance(item, str) and '-' in item:
                plugin_name, trigger_command = item.split('-', 1)
                whitelist_config[plugin_name.strip()] = trigger_command.strip()

        for star in all_stars_metadata:
            plugin_name = getattr(star, "name", "未知插件")
            plugin_instance = getattr(star, "star_cls", None)
            module_path = getattr(star, "module_path", None)

            if plugin_name not in whitelist_config:
                continue

            if (
                    not plugin_name
                    or not module_path
                    or not isinstance(plugin_instance, Star)
            ):
                logger.warning(
                    f"插件 '{plugin_name}' (模块: {module_path}) 的元数据无效或不完整，已跳过。"
                )
                continue

            if plugin_instance is self:
                continue

            for handler in star_handlers_registry:
                if not isinstance(handler, StarHandlerMetadata):
                    continue

                if handler.handler_module_path != module_path:
                    continue

                command_name: Optional[str] = None
                description: Optional[str] = handler.desc

                for filter_ in handler.event_filters:
                    if isinstance(filter_, CommandFilter):
                        command_name = filter_.command_name
                        break
                    elif isinstance(filter_, CommandGroupFilter):
                        command_name = filter_.group_name
                        break

                if command_name:
                    if description:
                        formatted_command = f"{command_name}#{description}"
                    else:
                        formatted_command = command_name

                    if formatted_command not in plugin_commands[plugin_name]:
                        plugin_commands[plugin_name].append(formatted_command)

        return dict(plugin_commands)
