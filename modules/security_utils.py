import discord


# Define a custom exception for our security checks
class SecurityCheckError(Exception):
    """Base exception for a failed security validation."""


# --- Validator Functions (Raise Exceptions) ---


def validate_role_safety(role: discord.Role, *, require_no_permissions: bool = False) -> None:
    """Check if a role is safe to be configured.

    Raises SecurityCheckError if it's dangerous.
    """
    # 1. Check for roles that must be purely cosmetic (e.g., opt-out, vanity)
    if require_no_permissions and role.permissions.value != 0:
        msg = f"Role {role.mention} must have **no permissions** to be used for this feature."
        raise SecurityCheckError(msg)

    # 2. Check for dangerous permissions on ANY role
    if role.permissions.administrator:
        msg = f"Role {role.mention} has **Administrator** permissions and cannot be used."
        raise SecurityCheckError(msg)

    # You can expand this check if needed
    dangerous_perms = {
        "manage_guild": role.permissions.manage_guild,
        "manage_roles": role.permissions.manage_roles,
        "kick_members": role.permissions.kick_members,
        "ban_members": role.permissions.ban_members,
    }

    if any(dangerous_perms.values()):
        msg = f"Role {role.mention} has dangerous permissions (e.g., Manage Roles, Kick/Ban) and cannot be used."
        raise SecurityCheckError(
            msg,
        )


def validate_bot_hierarchy(interaction: discord.Interaction | discord.Message, role: discord.Role) -> None:
    """Check if the bot's role is high enough to manage the target role.

    Raises SecurityCheckError if it's not.
    """
    # Get guild.me from either an interaction or a message
    if interaction.guild.me.top_role <= role:
        msg = (
            f"I cannot manage the {role.mention} role. It is higher than (or equal to) my own top role. "
            "Please move my bot role higher in the server's role list."
        )
        raise SecurityCheckError(
            msg,
        )


def validate_moderation_action(interaction: discord.Interaction, target_member: discord.Member) -> None:
    """Perform all pre-action checks for a moderation command.

    Raises SecurityCheckError on any failure.
    """
    actor = interaction.user
    guild = interaction.guild
    bot_user = interaction.client.user

    if target_member.id == actor.id:
        msg = "You cannot perform this action on yourself."
        raise SecurityCheckError(msg)

    if target_member.id == bot_user.id:
        msg = "You cannot perform this action on me."
        raise SecurityCheckError(msg)

    if target_member.id == guild.owner_id:
        msg = "You cannot perform moderation actions on the server owner."
        raise SecurityCheckError(msg)

    if target_member.top_role >= actor.top_role and guild.owner_id != actor.id:
        msg = "You cannot moderate a member with an equal or higher role."
        raise SecurityCheckError(msg)

    if target_member.top_role >= guild.me.top_role:
        msg = f"I cannot moderate {target_member.mention}. Their role is higher than (or equal to) my own."
        raise SecurityCheckError(msg)


# --- Boolean-Check Functions (Return Tuples) ---


def is_role_safe(role: discord.Role, *, require_no_permissions: bool = False) -> tuple[bool, str | None]:
    """Boolean version of the safety check for non-command logic.

    Checks for dangerous permissions AND optionally requires zero permissions.
    """
    # 1. Check for roles that must be purely cosmetic
    if require_no_permissions and role.permissions.value != 0:
        return False, "Role has permissions and will be ignored for security."

    # 2. Check for dangerous permissions on ANY role
    if role.permissions.administrator:
        return False, "Role has Administrator permissions and cannot be used."

    dangerous_perms = {
        "manage_guild": role.permissions.manage_guild,
        "manage_messages": role.permissions.manage_messages,
        "manage_roles": role.permissions.manage_roles,
        "kick_members": role.permissions.kick_members,
        "ban_members": role.permissions.ban_members,
        "move_members": role.permissions.move_members,
    }
    if any(dangerous_perms.values()):
        return (
            False,
            "Role has dangerous permissions (e.g., Manage Roles, Kick/Ban) and cannot be used.",
        )

    return True, None


def is_bot_hierarchy_sufficient(guild: discord.Guild, role: discord.Role) -> tuple[bool, str | None]:
    """Boolean version of the hierarchy check for non-command logic.

    Matches the check in reaction_roles.py.
    """
    if guild.me.top_role <= role:
        return (
            False,
            "I cannot manage this role as it is higher than or equal to my own top role.",
        )
    return True, None
