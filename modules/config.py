import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Self

from .dtypes import ChannelId, GuildId, UserId

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class BotConfig:
    """A frozen dataclass to hold all bot configuration values."""

    token: str
    disboard_bot_id: UserId
    # Special case for leveling system which may operate on a privileged guild
    guild_id: GuildId | None
    swl_guild_id: GuildId | None
    udp_port: int | None
    mc_guild_id: GuildId | None
    game_admin_log_channel_id: ChannelId | None
    servers_path: Path | None
    twelvedata_api_key: str | None

    @classmethod
    def from_environment(cls) -> Self:
        """Load all configuration from environment variables."""

        def get_env_int(name: str, required: bool = True) -> int | None:
            """Safely get and convert an environment variable to an int."""
            value = os.getenv(name)
            if value:
                try:
                    return int(value)
                except ValueError:
                    log.exception("'%s' is not a valid integer. Check your .env file.", name)
                    if required:
                        raise
                    return None
            if required:
                msg = f"Required environment variable '{name}' is not set."
                raise KeyError(msg)
            return None

        def get_path(name: str) -> Path | None:
            """Safely get an environment variable as a Path object."""
            val = os.getenv(name)
            if not val:
                return None

            if not Path(val).exists():
                return None

            return Path(val)

        token = os.getenv("TOKEN")
        if not token:
            msg = "Required environment variable 'TOKEN' is not set."
            raise KeyError(msg)

        return cls(
            token=token,
            disboard_bot_id=UserId(get_env_int("DISBOARD_BOT_ID")),
            # Optional guild features
            guild_id=(GuildId(val) if (val := get_env_int("UDP_GUILD_ID", required=False)) else None),
            swl_guild_id=(GuildId(val) if (val := get_env_int("SWL_GUILD_ID", required=False)) else None),
            udp_port=get_env_int("UDP_PORT", required=False),
            # Game Admin cog settings
            mc_guild_id=(GuildId(val) if (val := get_env_int("MC_GUILD_ID", required=False)) else None),
            game_admin_log_channel_id=(
                ChannelId(val) if (val := get_env_int("GAME_ADMIN_LOG_CHANNEL_ID", required=False)) else None
            ),
            servers_path=get_path("SERVERS_PATH"),
            twelvedata_api_key=os.getenv("TWELVEDATA_API_KEY"),
        )
