import logging

from logging.handlers import RotatingFileHandler
from pathlib import Path

from decouple import config

LOG_DIR_NAME = 'logs/'

def _init_logger(log_name: str = None, log_level = None, log_file_name: str = None):
    logger = logging.getLogger(log_name or __name__)
    logger.setLevel(log_level or logging.INFO)

    bot_log_file = config('HOOK_LOG_FILE', default=log_file_name or './logs/the_hook.log', cast=str)
    if not bot_log_file.startswith(LOG_DIR_NAME):
        bot_log_file = LOG_DIR_NAME + bot_log_file

    Path(LOG_DIR_NAME).mkdir(exist_ok=True)
    Path(bot_log_file).touch(exist_ok=True)

    log_handler = RotatingFileHandler(filename=bot_log_file, encoding='utf-8', mode='a')
    log_handler.setFormatter(logging.Formatter('%(asctime)s:%(levelname)s:%(name)s: %(message)s'))
    logger.addHandler(log_handler)

    return logger
