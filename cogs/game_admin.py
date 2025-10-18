# cogs/game_admin.py
import logging

import discord
from discord import app_commands
from discord.ext import commands

from modules.KiwiBot import KiwiBot

# Assuming server_admin.py is in a location Python can import from
from modules.server_admin import CommandExecutionError, RCONConnectionError, ServerManager, ServerNotFoundError, ServerStateError

log = logging.getLogger(__name__)


# --- The Main Cog Class ---
@app_commands.guild_only()
@app_commands.default_permissions(kick_members=True)
class GameAdmin(commands.Cog):
    """A cog for managing game servers via Discord."""

    server = app_commands.Group(
        name="server",
        description="Commands for game server administration.",
    )

    def __init__(self, bot: KiwiBot) -> None:
        self.bot = bot
        # The manager now comes directly from the bot instance
        self.manager: ServerManager | None = self.bot.server_manager
        # Point the group to the guild ID from the bot's config
        self.server.guild_ids = [bot.config.mc_guild_id] if bot.config.mc_guild_id else None

    # --- Autocomplete Callbacks ---

    async def _autocomplete_all_servers(
        self,
        _interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        if not self.manager:
            return []
        return [app_commands.Choice(name=srv, value=srv) for srv in self.manager.all_servers if current.lower() in srv.lower()][
            :25
        ]

    async def _autocomplete_online_servers(
        self,
        _interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        if not self.manager:
            return []
        return [
            app_commands.Choice(name=srv, value=srv) for srv in self.manager.online_servers if current.lower() in srv.lower()
        ][:25]

    async def _autocomplete_offline_servers(
        self,
        _interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        if not self.manager:
            return []
        return [
            app_commands.Choice(name=srv, value=srv) for srv in self.manager.offline_servers if current.lower() in srv.lower()
        ][:25]

    async def _autocomplete_rcon_servers(
        self,
        _interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        if not self.manager:
            return []

        choices = []
        for srv_name in self.manager.online_servers:
            if current.lower() in srv_name.lower():
                server_info = self.manager.all_servers.get(srv_name)
                if server_info and server_info.rcon_enabled:
                    choices.append(app_commands.Choice(name=srv_name, value=srv_name))
        return choices[:25]

    # --- Logging Helper ---

    async def _log_action(
        self,
        *,
        interaction: discord.Interaction,
        action: str,
        server: str,
        reason: str | None,
        color: discord.Color,
        details: str | None = None,
    ) -> None:
        """Send a standardized log message to the configured log channel."""
        log_channel_id = self.bot.config.game_admin_log_channel_id
        if not log_channel_id:
            return  # Logging is disabled

        log_channel = self.bot.get_channel(log_channel_id)
        if not isinstance(log_channel, discord.TextChannel):
            log.warning(
                "Log channel with ID %s not found or is not a text channel.",
                log_channel_id,
            )
            return

        embed = discord.Embed(
            title=f"Server Action: {action}",
            color=color,
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="Server", value=server, inline=True)
        embed.add_field(name="Moderator", value=interaction.user.mention, inline=True)
        if reason:
            embed.add_field(name="Reason", value=reason, inline=False)
        if details:
            embed.add_field(name="Details", value=details, inline=False)

        embed.set_footer(text=f"User ID: {interaction.user.id}")

        try:
            await log_channel.send(embed=embed)
        except discord.Forbidden:
            log.exception(
                "Missing permissions to send to log channel %s.",
                log_channel.name,
            )
        except discord.HTTPException:
            log.exception("Failed to send to log channel")

    # --- Commands ---

    @server.command(name="list", description="Shows the status of all managed servers.")
    async def list_servers(self, interaction: discord.Interaction) -> None:
        """Display an overview of all online and offline servers."""
        await interaction.response.defer()
        if not self.manager:
            await interaction.followup.send(
                "❌ The Server Manager is not running. Check bot logs.",
            )
            return

        embed = discord.Embed(
            title="Server Status Overview",
            color=discord.Color.blue(),
            timestamp=discord.utils.utcnow(),
        )

        online_list = "\n".join(f"- `{s}`" for s in self.manager.online_servers) or "None"
        offline_list = "\n".join(f"- `{s}`" for s in self.manager.offline_servers) or "None"

        embed.add_field(name="🟢 Online Servers", value=online_list, inline=False)
        embed.add_field(name="🔴 Offline Servers", value=offline_list, inline=False)
        embed.set_footer(text="Use /server status [name] for more details.")

        await interaction.followup.send(embed=embed)

    @server.command(
        name="status",
        description="Shows detailed status for a specific server.",
    )
    @app_commands.autocomplete(server=_autocomplete_all_servers)
    @app_commands.describe(server="The name of the server to inspect.")
    async def status(self, interaction: discord.Interaction, server: str) -> None:
        """Show detailed information about a single server."""
        await interaction.response.defer()
        if not self.manager:
            await interaction.followup.send(
                "❌ The Server Manager is not running. Check bot logs.",
            )
            return

        info = self.manager.all_servers.get(server)
        if not info:
            await interaction.followup.send(f"❌ Server `{server}` not found.")
            return

        color = discord.Color.green() if info.status.value == "online" else discord.Color.red()
        embed = discord.Embed(title=f"Status for `{info.name}`", color=color)
        embed.add_field(name="Status", value=info.status.value.title(), inline=True)
        embed.add_field(name="Address", value=f"`{info.ip}:{info.port}`", inline=True)
        embed.add_field(
            name="RCON",
            value=f"Enabled (`{info.rcon_port}`)" if info.rcon_enabled else "Disabled",
            inline=True,
        )
        embed.set_footer(text=f"Full Path: {info.path}")

        await interaction.followup.send(embed=embed)

    @server.command(name="start", description="Starts an offline server.")
    @app_commands.autocomplete(server=_autocomplete_offline_servers)
    @app_commands.describe(
        server="The server to start.",
        reason="The reason for starting the server.",
    )
    async def start(
        self,
        interaction: discord.Interaction,
        server: str,
        reason: str | None = None,
    ) -> None:
        """Handle the logic to start a game server."""
        await interaction.response.defer()
        if not self.manager:
            await interaction.followup.send(
                "❌ The Server Manager is not running. Check bot logs.",
            )
            return

        await self.manager.start(server)
        await interaction.followup.send(
            f"✅ **Start** command sent for `{server}` by {interaction.user.mention}.",
        )
        await self._log_action(
            interaction=interaction,
            action="Start",
            server=server,
            reason=reason,
            color=discord.Color.green(),
        )

    @server.command(name="stop", description="Stops an online server.")
    @app_commands.autocomplete(server=_autocomplete_online_servers)
    @app_commands.describe(
        server="The server to stop.",
        reason="The reason for stopping the server.",
    )
    async def stop(
        self,
        interaction: discord.Interaction,
        server: str,
        reason: str | None = None,
    ) -> None:
        """Handle the logic to stop a game server."""
        await interaction.response.defer()
        if not self.manager:
            await interaction.followup.send(
                "❌ The Server Manager is not running. Check bot logs.",
            )
            return

        await self.manager.stop(server)
        await interaction.followup.send(
            f"✅ **Stop** command sent for `{server}` by {interaction.user.mention}.",
        )
        await self._log_action(
            interaction=interaction,
            action="Stop",
            server=server,
            reason=reason,
            color=discord.Color.orange(),
        )

    @server.command(name="rcon", description="Sends a command to a server via RCON.")
    @app_commands.autocomplete(server=_autocomplete_rcon_servers)
    @app_commands.describe(
        server="The server to send the command to.",
        command="The RCON command to execute.",
        reason="The reason for running this command.",
    )
    async def rcon(
        self,
        interaction: discord.Interaction,
        server: str,
        command: str,
        reason: str | None = None,
    ) -> None:
        """Send an RCON command to an online server."""
        await interaction.response.defer()
        if not self.manager:
            await interaction.followup.send(
                "❌ The Server Manager is not running. Check bot logs.",
            )
            return

        response = await self.manager.run_rcon(server, command)

        response_content = response.strip() if response else "No response from server."
        # Truncate response to fit within Discord's message limit
        MAX_LENGTH = 1950
        if len(response_content) > MAX_LENGTH:
            response_content = response_content[:MAX_LENGTH] + "\n... (response truncated)"

        await interaction.followup.send(
            f"✅ RCON command sent to `{server}` by {interaction.user.mention}.\n```\n{response_content}\n```",
        )
        await self._log_action(
            interaction=interaction,
            action="RCON",
            server=server,
            reason=reason,
            color=discord.Color.dark_blue(),
            details=f"Command: `{command}`",
        )

    @server.command(
        name="refresh",
        description="Forces the bot to re-scan all server statuses.",
    )
    @app_commands.describe(reason="The reason for forcing a refresh.")
    async def refresh(
        self,
        interaction: discord.Interaction,
        reason: str | None = None,
    ) -> None:
        """Trigger a manual refresh of the server list."""
        await interaction.response.defer()
        if not self.manager:
            await interaction.followup.send(
                "❌ The Server Manager is not running. Check bot logs.",
            )
            return

        await self.manager.force_refresh()
        await interaction.followup.send(
            f"✅ Server list refresh initiated by {interaction.user.mention}.",
        )
        await self._log_action(
            interaction=interaction,
            action="Refresh",
            server="All",
            reason=reason,
            color=discord.Color.purple(),
        )

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        """Handle errors for all commands in this cog."""
        # Get the root cause of the error
        original = getattr(error, "original", error)

        # Ensure we have a response to send to
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)

        if isinstance(original, ServerNotFoundError | ServerStateError | RCONConnectionError):
            # These are "safe" errors to show the user
            await interaction.followup.send(f"⚠️ {original}")
        elif isinstance(original, CommandExecutionError):
            log.exception(
                "A server script failed for '%s': %s",
                interaction.command.name,
                {original.stderr},
            )
            await interaction.followup.send("❌ The server script failed to execute. Check bot logs for details.")
        else:
            log.exception("An unexpected error occurred in a game admin command.")
            await interaction.followup.send("❌ An unexpected error occurred.", ephemeral=True)


async def setup(bot: KiwiBot) -> None:
    """Add the GameAdmin cog to the bot."""
    if not bot.config.mc_guild_id or not bot.config.servers_path:
        log.error(
            "GameAdmin cog not loaded. Missing 'MC_GUILD_ID' or 'SERVERS_PATH' in config.",
        )
        return
    await bot.add_cog(GameAdmin(bot))
