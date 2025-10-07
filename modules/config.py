# In modules/config.py
import logging
import os
from dataclasses import dataclass
from typing import NewType

log = logging.getLogger(__name__)

# Using NewType for stricter type checking of Discord IDs
DiscordId = NewType("DiscordId", int)


@dataclass(frozen=True)
class BotConfig:
    """A frozen dataclass to hold all bot configuration values."""

    token: str
    guild_id: DiscordId
    join_leave_log_channel_id: DiscordId
    mod_channel_id: DiscordId
    level_up_channel_id: DiscordId | None
    disboard_bot_id: DiscordId
    bumper_role_id: DiscordId
    backup_bumper_role_id: DiscordId | None
    muted_role_id: DiscordId | None
    udp_port: int | None
    # Add these new fields
    roles_to_prune: list[DiscordId]
    inactivity_days: int
    custom_role_prefix: str
    custom_role_prune_days: int

    @classmethod
    def from_environment(cls) -> "BotConfig":
        """Load all configuration from environment variables."""

        def get_env_id(name: str, required: bool = True) -> DiscordId | None:
            """Safely get and convert an environment variable to a DiscordId."""
            value = os.getenv(name)
            if value:
                try:
                    return DiscordId(int(value))
                except ValueError:
                    log.exception("'%s' is not a valid integer. Check your .env file.", name)
                    if required:
                        raise  # Re-raise to halt startup if required
                    return None
            if required:
                msg = f"Required environment variable '{name}' is not set."
                raise KeyError(msg)
            return None

        token = os.getenv("TOKEN")
        if not token:
            msg = "Required environment variable 'TOKEN' is not set."
            raise KeyError(msg)

        udp_port_str = os.getenv("UDP_PORT")
        udp_port = int(udp_port_str) if udp_port_str else None

        roles_str = os.getenv("ROLES_TO_PRUNE", "")
        roles_to_prune = [DiscordId(int(r.strip())) for r in roles_str.split(",") if r.strip()]

        inactivity_str = os.getenv("INACTIVITY_DAYS", "14")
        inactivity_days = int(inactivity_str)

        custom_role_prefix = os.getenv("CUSTOM_ROLE_PREFIX", "Custom: ")
        custom_role_prune_days = int(os.getenv("CUSTOM_ROLE_PRUNE_DAYS", "30"))

        return cls(
            token=token,
            guild_id=get_env_id("GUILD_ID"),
            join_leave_log_channel_id=get_env_id("JOIN_LEAVE_LOG_CHANNEL_ID"),
            mod_channel_id=get_env_id("MOD_CHANNEL_ID"),
            level_up_channel_id=get_env_id("LEVEL_UP_CHANNEL_ID", required=False),
            disboard_bot_id=get_env_id("DISBOARD_BOT_ID"),
            bumper_role_id=get_env_id("BUMPER_ROLE_ID"),
            backup_bumper_role_id=get_env_id("BACKUP_BUMPER_ROLE_ID", required=False),
            muted_role_id=get_env_id("MUTED_ROLE_ID", required=False),
            udp_port=udp_port,
            roles_to_prune=roles_to_prune,
            inactivity_days=inactivity_days,
            custom_role_prefix=custom_role_prefix,
            custom_role_prune_days=custom_role_prune_days,
        )
