# cogs/reaction_roles.py

import logging
import re
from collections import defaultdict
from typing import TYPE_CHECKING, Literal, TypedDict, cast

import discord
from discord import app_commands
from discord.ext import commands

if TYPE_CHECKING:
    from modules.KiwiBot import KiwiBot
else:
    KiwiBot = commands.Bot

# A structured dictionary for analysis results, improving code clarity.
AnalysisStatus = Literal["OK", "ERROR", "WARN"]


class AnalysisResult(TypedDict):
    """Represents the analysis of a single line in a reaction role message."""

    status: AnalysisStatus
    line_content: str
    emoji_str: str | None
    role: discord.Role | None
    error_message: str | None


# Regex to find custom emojis (<:name:id> or <a:name:id>) and a broad range of Unicode emojis.
# While not 100% exhaustive of all Unicode emojis, this covers the vast majority.
EMOJI_REGEX = re.compile(
    r"<a?:\w+:\d+>|"
    r"[\U0001F1E6-\U0001F1FF]|"  # flags (iOS)
    r"[\U0001F300-\U0001F5FF]|"  # symbols & pictographs
    r"[\U0001F600-\U0001F64F]|"  # emoticons
    r"[\U0001F680-\U0001F6FF]|"  # transport & map symbols
    r"[\U0001F700-\U0001F77F]|"  # alchemical symbols
    r"[\U0001F780-\U0001F7FF]|"  # Geometric Shapes Extended
    r"[\U0001F800-\U0001F8FF]|"  # Supplemental Arrows-C
    r"[\U0001F900-\U0001F9FF]|"  # Supplemental Symbols and Pictographs
    r"[\U0001FA00-\U0001FA6F]|"  # Chess Symbols
    r"[\U0001FA70-\U0001FAFF]|"  # Symbols and Pictographs Extended-A
    r"[\u2600-\u26FF]|"  # miscellaneous symbols
    r"[\u2700-\u27BF]|"  # dingbats
    r"[\u2B50]",  # star
)
ROLE_MENTION_REGEX = re.compile(r"<@&(\d+)>")

log = logging.getLogger(__name__)


class ReactionRoles(commands.Cog):
    """Reaction role system for reactions on admin-authored messages.

    Includes a crucial security check to ensure roles have no permissions.
    """

    def __init__(self, bot: KiwiBot) -> None:
        self.bot = bot
        # --- Message Caching ---
        self.analysis_cache: dict[int, list[AnalysisResult]] = {}
        self.MAX_CACHE_SIZE = 128

        self.debug_reaction_role_menu = app_commands.ContextMenu(
            name="Debug Reaction Role",
            callback=self.debug_reaction_role,
        )
        self.bot.tree.add_command(self.debug_reaction_role_menu)

    async def cog_unload(self) -> None:
        """Remove the context menu command when the cog is unloaded."""
        self.bot.tree.remove_command(
            self.debug_reaction_role_menu.name,
            type=self.debug_reaction_role_menu.type,
        )

    async def _analyze_reaction_message(
        self,
        message: discord.Message,
    ) -> list[AnalysisResult]:
        """Analyze a message to determine its validity as a reaction role message.

        Caches results to avoid re-computing for the same message.
        This is the single source of truth for all reaction role logic.

        Returns:
            A list of AnalysisResult dictionaries, one for each parsed line.
            Returns an empty list if the message author is not an administrator.

        """
        # 1. Check Cache
        if message.id in self.analysis_cache:
            return self.analysis_cache[message.id]

        # The rest of the function performs the analysis if not found in cache.
        # This is the single source of truth for all reaction role logic.

        # 2. Perform Analysis
        results: list[AnalysisResult] = []
        if not isinstance(message.author, discord.Member):
            return []  # Cannot be a reaction role message if author isn't a guild member.

        # 1. Author Validation: Must be an manage_roles.
        if not message.author.guild_permissions.manage_roles:
            return []

        # 3. Line-by-Line Analysis
        for line in message.content.splitlines():
            clean_line = line.strip()
            if not clean_line:
                continue

            role_mentions = ROLE_MENTION_REGEX.findall(clean_line)
            emojis_found = EMOJI_REGEX.findall(clean_line)

            if len(emojis_found) != 1 or len(role_mentions) != 1:
                results.append(
                    {
                        "status": "WARN",
                        "line_content": clean_line,
                        "emoji_str": emojis_found[0] if emojis_found else None,
                        "role": None,
                        "error_message": "Line must contain exactly one emoji and one role mention.",
                    },
                )
                continue

            emoji_str = emojis_found[0]
            role = message.guild.get_role(int(role_mentions[0]))

            if not role:
                results.append(
                    {
                        "status": "WARN",
                        "line_content": clean_line,
                        "emoji_str": emoji_str,
                        "role": None,
                        "error_message": f"Role with ID {role_mentions[0]} not found.",
                    },
                )
                continue

            # 4. Security Check: Role must have NO permissions.
            if role.permissions.value != 0:
                results.append(
                    {
                        "status": "ERROR",
                        "line_content": clean_line,
                        "emoji_str": emoji_str,
                        "role": role,
                        "error_message": "Role has permissions and will be ignored for security.",
                    },
                )
                continue

            # 5. Bot Hierarchy Check
            if message.guild.me.top_role <= role:
                results.append(
                    {
                        "status": "ERROR",
                        "line_content": clean_line,
                        "emoji_str": emoji_str,
                        "role": role,
                        "error_message": "I cannot manage this role as it is higher than or equal to my own top role.",
                    },
                )
                continue

            # If all checks pass
            results.append(
                {
                    "status": "OK",
                    "line_content": clean_line,
                    "emoji_str": emoji_str,
                    "role": role,
                    "error_message": None,
                },
            )

        # 6. Manage Cache Size and Store Result
        if len(self.analysis_cache) >= self.MAX_CACHE_SIZE:
            # Remove the oldest item (FIFO)
            del self.analysis_cache[next(iter(self.analysis_cache))]

        self.analysis_cache[message.id] = results
        return results

    @commands.Cog.listener()
    async def on_raw_message_edit(self, payload: discord.RawMessageUpdateEvent) -> None:
        """Invalidate the cache if a potential reaction role message is edited."""
        if payload.message_id in self.analysis_cache:
            del self.analysis_cache[payload.message_id]
            log.info("Invalidated reaction role cache for edited message ID %s.", payload.message_id)

    @commands.Cog.listener()
    async def on_raw_reaction_add(
        self,
        payload: discord.RawReactionActionEvent,
    ) -> None:
        """Listen for a reaction being added to any message."""
        await self._handle_reaction_event(payload)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(
        self,
        payload: discord.RawReactionActionEvent,
    ) -> None:
        """Listen for a reaction being removed from any message."""
        await self._handle_reaction_event(payload)

    async def _handle_reaction_event(
        self,
        payload: discord.RawReactionActionEvent,
    ) -> None:
        """Shared logic for processing both reaction add and remove events."""
        if not payload.guild_id or payload.user_id == self.bot.user.id:
            return

        guild = self.bot.get_guild(payload.guild_id)
        if not guild:
            return

        try:
            # fetch_member is required as the member might not be in cache.
            member = await guild.fetch_member(payload.user_id)
        except discord.NotFound:
            return

        try:
            channel = await self.bot.fetch_channel(payload.channel_id)
            # We can cast here as fetch_channel on a guild channel ID will return a GuildChannel
            # which has the fetch_message method. fetch_message will fail if it's not messageable.
            message = await cast("discord.TextChannel", channel).fetch_message(
                payload.message_id,
            )
        except (discord.NotFound, discord.Forbidden):
            return

        payload_emoji_str = str(payload.emoji)

        if payload_emoji_str not in message.content:
            return

        analysis_results = await self._analyze_reaction_message(message)
        if not analysis_results:
            return

        for result in analysis_results:
            if result["status"] == "OK" and result["emoji_str"] == payload_emoji_str:
                target_role = cast("discord.Role", result["role"])

                try:
                    reason = f"Reaction Role {payload.message_id}"
                    if payload.event_type == "REACTION_ADD":
                        await member.add_roles(target_role, reason=reason)
                        log.info(
                            "Added role '%s' to '%s' in guild '%s'",
                            target_role.name,
                            member.display_name,
                            guild.name,
                        )
                    elif payload.event_type == "REACTION_REMOVE":
                        await member.remove_roles(target_role, reason=reason)
                        log.info(
                            "Removed role '%s' from '%s' in guild '%s'",
                            target_role.name,
                            member.display_name,
                            guild.name,
                        )
                except discord.Forbidden:
                    log.warning(
                        "Failed to modify role '%s' for '%s'. Check permissions and role hierarchy.",
                        target_role.name,
                        member.display_name,
                    )
                except discord.HTTPException:
                    log.exception("Network error while modifying role for '%s'", member.display_name)

                # Found our match, no need to check other lines.
                break

    @staticmethod
    def _format_analysis_report(
        analysis: list[AnalysisResult],
    ) -> list[str]:
        """Format the analysis results into a list of strings for a report."""
        if not analysis:
            return [
                "⚠️ This message is **not a valid reaction role message**.\n"
                "(Reason: The message author does not have `Manage Roles` permissions).",
            ]

        report_lines: list[str] = []
        aggregated_results: defaultdict[str, list[str]] = defaultdict(list)

        for result in analysis:
            line = result["line_content"]
            status_map = {
                "OK": "✅ **VALID**",
                "ERROR": f"❌ **ERROR**: {result['error_message']}",
                "WARN": f"⚠️ **WARN**: {result['error_message']}",
            }
            aggregated_results[status_map[result["status"]]].append(line)

        for header, lines in aggregated_results.items():
            report_lines.extend([f"\n{header}", "```", *lines, "```"])

        return report_lines

    @app_commands.default_permissions(manage_roles=True)
    async def debug_reaction_role(
        self,
        interaction: discord.Interaction,
        message: discord.Message,
    ) -> None:
        """Analyze a message for reaction role validity and DM an aggregated report."""
        await interaction.response.defer(ephemeral=True, thinking=True)

        report_lines = [
            f"**🔎 Debug Report for Message ID:** `{message.id}` in {message.channel.mention}\n",
        ]

        if not interaction.guild or not isinstance(
            interaction.guild.me,
            discord.Member,
        ):
            await interaction.followup.send(
                "Error: This command can only be used in a server.",
            )
            return

        if not interaction.guild.me.guild_permissions.manage_roles:
            report_lines.append(
                "❌ **CRITICAL: I do not have the `Manage Roles` permission! I cannot assign or remove any roles.**\n",
            )

        analysis = await self._analyze_reaction_message(message)

        report_lines.extend(self._format_analysis_report(analysis))
        report = "\n".join(report_lines)
        log.info(report)

        try:
            CHAR_LIMIT = 2000
            if len(report) <= CHAR_LIMIT:
                await interaction.user.send(report)
            else:
                report_chunks = [report[i : i + CHAR_LIMIT] for i in range(0, len(report), CHAR_LIMIT)]
                for chunk in report_chunks:
                    await interaction.user.send(chunk)

            # Confirm to the admin that the DM was sent
            await interaction.followup.send(
                "I have sent the debug report to your DMs.",
                ephemeral=True,
            )

        except discord.Forbidden:
            await interaction.followup.send(
                "I couldn't send you a DM. Please check your privacy settings. Here is the report:\n\n" + report,
                ephemeral=True,
            )


async def setup(bot: KiwiBot) -> None:
    """Add the ReactionRoles cog to the bot."""
    await bot.add_cog(ReactionRoles(bot))
    log.info("ReactionRoles cog loaded.")
