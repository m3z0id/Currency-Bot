# In modules/config.py
import logging
import os
from dataclasses import dataclass
from typing import Self

from .types import ChannelId, GuildId, RoleId, UserId

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class BotConfig:
    """A frozen dataclass to hold all bot configuration values."""

    token: str
    guild_id: GuildId
    join_leave_log_channel_id: ChannelId
    mod_channel_id: ChannelId
    level_up_channel_id: ChannelId | None
    disboard_bot_id: UserId
    bumper_role_id: RoleId
    backup_bumper_role_id: RoleId | None
    muted_role_id: RoleId | None
    udp_port: int | None
    # Add these new fields
    roles_to_prune: list[RoleId]
    inactivity_days: int
    custom_role_prefix: str
    custom_role_prune_days: int

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

        token = os.getenv("TOKEN")
        if not token:
            msg = "Required environment variable 'TOKEN' is not set."
            raise KeyError(msg)

        roles_str = os.getenv("ROLES_TO_PRUNE", "")

        return cls(
            token=token,
            guild_id=GuildId(get_env_int("GUILD_ID")),
            join_leave_log_channel_id=ChannelId(get_env_int("JOIN_LEAVE_LOG_CHANNEL_ID")),
            mod_channel_id=ChannelId(get_env_int("MOD_CHANNEL_ID")),
            level_up_channel_id=ChannelId(val) if (val := get_env_int("LEVEL_UP_CHANNEL_ID", required=False)) else None,
            disboard_bot_id=UserId(get_env_int("DISBOARD_BOT_ID")),
            bumper_role_id=RoleId(get_env_int("BUMPER_ROLE_ID")),
            backup_bumper_role_id=RoleId(val) if (val := get_env_int("BACKUP_BUMPER_ROLE_ID", required=False)) else None,
            muted_role_id=RoleId(val) if (val := get_env_int("MUTED_ROLE_ID", required=False)) else None,
            udp_port=get_env_int("UDP_PORT", required=False),
            roles_to_prune=[RoleId(int(r.strip())) for r in roles_str.split(",") if r.strip()],
            inactivity_days=get_env_int("INACTIVITY_DAYS", required=False) or 14,
            custom_role_prefix=os.getenv("CUSTOM_ROLE_PREFIX", "Custom: "),
            custom_role_prune_days=get_env_int("CUSTOM_ROLE_PRUNE_DAYS", required=False) or 30,
        )
