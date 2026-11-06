"""Read-only trade center routes; authorization remains in the existing scope service."""
from dataclasses import replace
from datetime import date,datetime
from decimal import Decimal
from flask import Response,g,jsonify,request
from portfolio.services.trade_center import TradeCenterService
from portfolio.web.security.auth_context import CAP_VIEW_ASSETS,require_capability


def exact(value):
    if isinstance(value,Decimal):return str(value)
    if isinstance(value,(date,datetime)):return value.isoformat()
    if isinstance(value,dict):return {k:exact(v) for k,v in value.items()}
    if isinstance(value,(list,tuple)):return [exact(v) for v in value]
    return value


def register(api):
    from portfolio.web.api_v1 import get_user_query_scope,get_trades_service
    def inputs():
        params=request.args.to_dict()
        end=date.fromisoformat(params.get('end_date') or date.today().isoformat())
        start=date.fromisoformat(params.get('start_date') or '2025-01-01')
        if start>end:raise ValueError('开始日期不可晚于结束日期。')
        if params.get('person_id') and not params['person_id'].isdigit():raise ValueError('人员编号无效。')
        scope=replace(get_user_query_scope(as_of=end),start_date=start,search=params.get('search','').strip(),category=params.get('category') or None,include_disabled=True)
        return TradeCenterService(get_trades_service().path),scope,params
    @api.get('/trade-center')
    @require_capability(CAP_VIEW_ASSETS)
    def center_list():
        try:
            service,scope,params=inputs();ctx=getattr(g,'auth_context',None)
            allowed=None if ctx and (ctx.is_admin() or ctx.is_lan_viewer()) else tuple(ctx.allowed_person_ids or (-99999,)) if ctx else (-99999,)
            return jsonify(exact(service.page(scope,params,catalog_scope=replace(scope,person_ids=allowed))))
        except ValueError as exc:return jsonify(status='error',message=str(exc)),400
    @api.get('/trade-center/<int:record_id>')
    @require_capability(CAP_VIEW_ASSETS)
    def center_detail(record_id):
        try:
            service,scope,params=inputs();ctx=getattr(g,'auth_context',None)
            return jsonify(exact(service.detail(scope,params,record_id,restricted=not ctx or not(ctx.is_admin() or ctx.is_lan_viewer()))))
        except ValueError as exc:return jsonify(status='error',message=str(exc)),400
    @api.get('/trade-center/export')
    @require_capability(CAP_VIEW_ASSETS)
    def center_export():
        try:
            service,scope,params=inputs()
            # Validate before sending headers, then stream bounded batches.
            from portfolio.services.trade_center import validate
            validate(params)
            chunks=service.export(scope,params);first=next(chunks)
            def stream():
                try:
                    yield first
                    yield from chunks
                finally:chunks.close()
            return Response(stream(),content_type='text/csv; charset=utf-8',headers={'Content-Disposition':'attachment; filename=trade-center.csv','Cache-Control':'no-store'})
        except ValueError as exc:return jsonify(status='error',message=str(exc)),400
