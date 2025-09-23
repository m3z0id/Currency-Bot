# KiwiBot

This bot adds an economy system, automated role management, and logging for staff.

---

## For Server Members

### Economy Commands 💰

- **`/bal [member]`**: Check your current wallet balance or view the balance of another member.
- **`/daily`**: Claim your randomized currency reward! You can also click the "Remind me" button to get a DM when your next claim is ready.
- **`/donate <member> <amount>`**: Give your money to another user.
- **`/sell [limb]`** and **`/harvest [organ]`**: shh just earn your money.

### Automatic Rewards ✨

- **Bumping**: If the server supports bumping, you'll automatically receive a random currency reward every time you successfully bump.

---

## For Staff & Developers

### Additional Features

- **Automated Logging**:
  - **Mod Log**: Automatically logs moderation actions (bans, unbans, kicks, timeouts) to a designated channel.
  - **Join/Leave Log**: Announce when members join or leave the server in a specific channel.
- **Automatic Role Pruning**:
  - **Inactive Members**: Removes specified roles from users who have been inactive for a configurable number of days.
  - **Old Custom Roles**: Deletes roles with a `Custom:` prefix that are older than 30 days to keep your role list clean.

### Setup & Installation

#### 1\. Configuration

The bot is configured using a `.env` file in the root directory. Create this file and add the following variables:

- `TOKEN`: Your unique Discord bot token.
- `GUILD_ID`: The ID of the server where the bot will operate.
- `ROLES_TO_PRUNE`: A comma-separated list of role IDs to be removed from inactive users.
- `INACTIVITY_DAYS`: The number of days a user must be inactive to have their roles pruned.
- `DISBOARD_BOT_ID`: The channel where bumps occur and the ID of the bump bot for the reward system.
- `JOIN_LEAVE_LOG_CHANNEL_ID`: The channel where member join/leave messages will be sent.
- `MOD_CHANNEL_ID`: The channel for logging moderation actions.

#### 2\. Running the Bot

This project uses `uv` for dependency management and execution.

1.  Get `uv` by following the installation instructions at [https://docs.astral.sh/uv](https://docs.astral.sh/uv).
2.  Run the bot from your terminal. `uv` will automatically install the required dependencies from `pyproject.toml` and run the script.
    ```shell
    uv run main.py
    ```

### Contributing

See `CONTRIBUTING.md` before contributing code.
