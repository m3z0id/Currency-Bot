import logging

from dotenv import load_dotenv

from modules.config import BotConfig
from modules.KiwiBot import KiwiBot

# Loads environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("bot.log"),
        logging.StreamHandler(),
    ],
)

try:
    # Create the config from environment first
    config = BotConfig.from_environment()

    # Pass the config object into the bot's constructor
    bot: KiwiBot = KiwiBot(config=config)
    bot.run(config.token)

except (KeyError, ValueError):
    log = logging.getLogger(__name__)
    log.exception(
        "A critical configuration error occurred. Please check your environment variables.",
    )
