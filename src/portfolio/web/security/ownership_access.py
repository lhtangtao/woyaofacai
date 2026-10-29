"""Page-specific device restriction using the existing request and admin role."""
from flask import g,request
from portfolio.web.security.auth_context import CAP_VIEW_ASSETS


def can_view_ownership():
    ctx=getattr(g,'auth_context',None)
    if not ctx or not ctx.has_capability(CAP_VIEW_ASSETS):return False
    if ctx.is_admin():return True
    ua=request.user_agent.string.lower()
    mobile=request.args.get('ui')=='mobile' or request.headers.get('Sec-CH-UA-Mobile')=='?1' or any(s in ua for s in ('android','iphone','ipad','ipod','mobile','windows phone'))
    # iPad desktop-style Safari still identifies its touch platform in the UA.
    mobile=mobile or ('macintosh' in ua and 'mobile/' in ua)
    return not mobile


def denied():
    from flask import jsonify
    return jsonify(status='error',message='股票归属与成本分析仅向桌面用户及移动端管理员开放。'),403
