import logging

from logging import RotatingFileHandler

from decouple import config

def _init_logger(log_name: str = None, log_level = None, log_file_name: str = None):
    logger = logging.getLogger(log_name or __name__)
    logger.setLevel(log_level or logging.INFO)
    bot_log_file = config('HOOK_LOG_FILE', default=log_file_name or 'the_hook.log', cast=str)
    log_handler = RotatingFileHandler(filename=bot_log_file, encoding='utf-8', mode='a')
    log_handler.setFormatter(logging.Formatter('%(asctime)s:%(levelname)s:%(name)s: %(message)s'))
    logger.addHandler(log_handler)
    return logger
