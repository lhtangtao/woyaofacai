"""只读取Money3本机凭证的字符串常量，不执行配置代码。"""

from portfolio.paths import CREDENTIALS_PATH as CREDENTIALS_FILE
from portfolio.services.live_quotes import credentials


def load_longbridge_credentials():
    key, secret, token = credentials(CREDENTIALS_FILE)
    return token, key, secret
