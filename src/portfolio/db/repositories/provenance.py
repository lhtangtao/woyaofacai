import json
import re
from decimal import Decimal

SENSITIVE = re.compile(r"token|secret|password|authorization|cookie|app.?key|api.?key|account.?number|account.?key|account.?hash|email|phone|身份证|手机号|邮箱", re.I)


def redact(value):
    if isinstance(value, dict):
        return {key: "••••（已遮盖）" if SENSITIVE.search(key) else redact(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str):
        return re.sub(r"(?i)(token|secret|password|api_key|app_key)=([^\s&]+)", r"\1=••••", value)
    return value


def get_provenance(connection, source_id: int, *, sensitive=False):
    if type(source_id) is not int:
        raise ValueError("来源 ID 必须为整数。")
    row = connection.execute("""SELECT r.id,r.source_locator,r.source_sheet,r.source_row,
        r.parse_status,r.parse_message,r.payload_json,b.id AS batch_id,b.source_kind,
        b.source_path,b.status AS batch_status,b.imported_at_utc,b.parser_version
        FROM raw_records r JOIN import_batches b ON b.id=r.import_batch_id WHERE r.id=?""", (source_id,)).fetchone()
    if row is None:
        raise ValueError("找不到这条原始来源记录，可能已更换数据库。")
    result = dict(row)
    payload = result.pop("payload_json")
    if len(payload) > 1_000_000:
        result["payload"] = "原始内容超过 1 MB，M1 不展示全文。源记录保留在数据库中。"
    else:
        try:
            payload = json.loads(payload, parse_float=Decimal)
        except (ValueError, TypeError):
            payload = "原始内容不是有效 JSON，保持源库不变，请检查原始记录。"
        result["payload"] = payload if sensitive else redact(payload)
    return result if sensitive else redact(result)
