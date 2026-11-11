"""Administrator-only access to the separate personal CNY ledger."""
from datetime import date
from pathlib import Path
import sqlite3

from flask import Blueprint, jsonify, request

from portfolio.domestic.alipay import parse_document
from portfolio.domestic.query import COLUMNS, read_transaction, read_view
from portfolio.domestic.service import publish, recalculate
from portfolio.web.security.auth_context import CAP_MANAGE_USERS, require_capability

domestic_api = Blueprint("domestic_api", __name__, url_prefix="/api/v1/domestic")


@domestic_api.get("/<view>")
@require_capability(CAP_MANAGE_USERS)
def view_data(view):
    if view not in COLUMNS:
        return jsonify(status="error", message="未知国内资产页面"), 404
    try:
        data = read_view(
            view, as_of=request.args.get("as_of", ""), start=request.args.get("start", ""),
            end=request.args.get("end", ""), fund=request.args.get("fund", ""), kind=request.args.get("kind", ""),
            page=request.args.get("page", 1, type=int), ascending=request.args.get("sort") == "asc",
        )
    except (ValueError, sqlite3.Error):
        return jsonify(status="error", message="无法读取国内资产数据，请检查日期及数据库状态"), 400
    return jsonify(status="ok", **data)


@domestic_api.get("/transactions/<raw_id>")
@require_capability(CAP_MANAGE_USERS)
def source_record(raw_id):
    try:
        row = read_transaction(raw_id)
    except sqlite3.Error:
        row = None
    if row is None:
        return jsonify(status="error", message="未找到来源记录"), 404
    return jsonify(status="ok", row=row)


@domestic_api.post("/import/<action>")
@require_capability(CAP_MANAGE_USERS)
def import_pdf(action):
    pdf = request.files.get("pdf")
    source_type = request.form.get("source_type")
    if action not in ("preview", "commit") or not pdf or source_type not in ("opening_asset", "transaction"):
        return jsonify(status="error", message="请选择对应类型的 PDF"), 400
    limit = 20 * 1024 * 1024
    content = pdf.stream.read(limit + 1)
    if len(content) > limit:
        return jsonify(status="error", message="PDF 最大支持 20 MB"), 413
    try:
        snapshot = date.fromisoformat(request.form.get("snapshot") or "2021-11-11")
        doc = parse_document(content, Path(pdf.filename).name, source_type, snapshot)
        if action == "preview":
            return jsonify(status="ok", summary=doc.summary(), digest=doc.digest,
                           issues=[{k: v for k, v in item.items() if k != "raw_data"} for item in doc.issues[:20]])
        if request.form.get("digest") != doc.digest:
            return jsonify(status="error", message="文件与预检不一致，请重新预检"), 400
        return jsonify(status="ok", **publish([doc]))
    except (ValueError, RuntimeError) as exc:
        return jsonify(status="error", message=str(exc)), 400
    except Exception:
        return jsonify(status="error", message="PDF 处理未完成，请在数据导入与校验中查看批次状态"), 400


@domestic_api.post("/rebuild")
@require_capability(CAP_MANAGE_USERS)
def rebuild_positions():
    try:
        return jsonify(status="ok", calculation=recalculate())
    except (ValueError, OSError, sqlite3.Error):
        return jsonify(status="error", message="重算未完成，请检查原始库与导入批次"), 400


@domestic_api.get("/holdings")
@require_capability(CAP_MANAGE_USERS)
def domestic_holdings():
    from portfolio.domestic.holdings_query import holdings

    account, fund = request.args.get("account", ""), request.args.get("fund", "")
    if bool(account) != bool(fund):
        return jsonify(status="error", message="请同时提供账户和基金代码"), 400
    try:
        return jsonify(status="ok", **holdings(account, fund))
    except (ValueError, ArithmeticError, sqlite3.Error):
        return jsonify(status="error", message="无法读取国内资产持仓"), 400
