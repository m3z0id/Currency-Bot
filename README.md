# KiwiBot

KiwiBot is a comprehensive Discord bot an economy, games, moderation tools, and automated management features.

---

## Features for Server Members

### Economy & General Commands 💰

- **`/bal [member]`**: Check your wallet balance and bump count, or check the stats of another member.
- **`/daily`**: Claim your daily currency reward. You have a chance to hit a massive jackpot! Use the buttons to share your winnings or set a reminder preference (once, always, or never).
- **`/donate <member> <amount>`**: Share the wealth by giving money to another user.
- **`/leaderboard <stat>`**: See who's on top! Displays the server leaderboard for the richest users or the most dedicated bumpers.
- **`/sell [limb]`** & **`/harvest [organ]`**: Engage in... _creative_ capitalism. A risky way for earning extra cash.
- **`/blackjack <bet>`**: Feeling lucky? Start a game of Blackjack!

### Automatic Rewards ✨

- **Bumping**: If your server uses Disboard, you'll automatically earn a random currency reward and climb the bump leaderboard every time you successfully `/bump` the server.

---

## Features for Staff & Developers

### Moderation Suite 🛡️

All moderation actions are slash commands and are automatically logged to your designated mod-log channel.

- **`/moderate ban <member>`**: Bans a user, with options to delete their recent message history.
- **`/moderate kick <member>`**: Kicks a user from the server.
- **`/moderate timeout <member> <duration>`**: Times out a user for a specified duration (e.g., `10m`, `2h`, `7d`).
- **`/moderate untimeout <member>`**: Removes an active timeout from a user.
- **`/moderate mute <member>`**: Mutes a user by assigning the configured `MUTED_ROLE_ID`.
- **`/moderate unmute <member>`**: Removes the muted role from a user.

### Automation & Management ⚙️

- **Automated Logging**:
  - **Mod Log**: A detailed feed of all moderation actions (bans, kicks, mutes, timeouts), including the responsible moderator and provided reason.
  - **Join/Leave Log**: A clean announcement log for when members join, rejoin, or leave the server.
- **Server Stats Channels**: Automatically updates the names of designated voice channels to display live server statistics like the total member count.
- **Automatic Role Pruning**:
  - **Inactive Members**: Keeps your member list tidy by removing specified roles from users who have been inactive for a configurable period. It also cleans up common cosmetic roles (e.g., `Colour:`, `Ping:`, `Gradient:`).
  - **Old Custom Roles**: Automatically deletes roles prefixed with `Custom:` after 30 days to prevent role clutter.
- **Smart Bump Reminders**: The bot pings the `BUMPER_ROLE_ID` two hours after a successful bump. If no one bumps after 10 more minutes, it pings the `BACKUP_BUMPER_ROLE_ID`.

### Setup & Installation

#### 1. Configuration

The bot is configured using a `.env` file in the project's root directory. Create this file and add the following variables:

```dotenv
# --- Core Bot Settings ---
TOKEN=YOUR_DISCORD_BOT_TOKEN_HERE
GUILD_ID=YOUR_SERVER_ID_HERE

# --- Economy & Bumping ---
DISBOARD_BOT_ID=ID_OF_THE_BUMP_BOT
BUMPER_ROLE_ID=ID_OF_PRIMARY_BUMPER_ROLE
BACKUP_BUMPER_ROLE_ID=ID_OF_BACKUP_BUMPER_ROLE # Optional

# --- Logging Channels ---
JOIN_LEAVE_LOG_CHANNEL_ID=CHANNEL_ID_FOR_JOIN_LEAVE_LOGS
MOD_CHANNEL_ID=CHANNEL_ID_FOR_MODERATION_LOGS

# --- Role Management ---
ROLES_TO_PRUNE=ID_ONE,ID_TWO,ID_THREE # Comma-separated list of role IDs
INACTIVITY_DAYS=14 # Days until a user is considered inactive (default is 14)
MUTED_ROLE_ID=ID_OF_YOUR_MUTED_ROLE # Required for /mute and /unmute
```
