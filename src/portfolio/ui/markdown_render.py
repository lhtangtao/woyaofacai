"""桌面端 Qt (QTextBrowser) 专用 Markdown -> 美观 HTML 渲染器。

设计目标：
1. 彻底规避 Qt 6 QTextDocument.setMarkdown() (MD4C) 遇到表格内 `<br>` 等裸 HTML 标签时
   吞掉后续整张表格与整篇文章内容的严重 Bug；
2. 零外部依赖，严格遵循 Qt Rich Text (HTML4 / CSS2.1) 支持子集，呈现券商研报级清晰排版；
3. 支持标题层级、斑马纹全宽表格（含单元格内 `<br>` 换行与加粗）、多级嵌套列表、引用块、
   代码块、行内代码、链接、粗斜体、删除线以及常见 LaTeX 公式的友好 Unicode 渲染。
"""
from __future__ import annotations

import html
import re
from typing import List, Tuple

# 匹配 Markdown 表格分隔行（例如 | :--- | :---: | ---: |）
_TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(?:\|\s*:?-{2,}:?\s*)+\|?\s*$")
# 匹配标题行（# ~ ######）
_HEADING_RE = re.compile(r"^\s*(#{1,6})\s+(.*)$")
# 匹配水平分割线
_HR_RE = re.compile(r"^\s*(?:[-*_]\s*){3,}$")
# 匹配列表项（返回前导空格、标记、正文）
_UL_RE = re.compile(r"^(\s*)[-*+•]\s+(.*)$")
_OL_RE = re.compile(r"^(\s*)(\d+)[.)]\s+(.*)$")
# 匹配引用块
_QUOTE_RE = re.compile(r"^\s*>\s?(.*)$")
# 匹配围栏代码块开关
_FENCE_RE = re.compile(r"^\s*```([A-Za-z0-9_+-]*)\s*$")
# 匹配 <br> 变体
_BR_TAG_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)

# 常见 LaTeX 符号映射到 Unicode，避免在 Qt 富文本中暴露原始反斜杠代码
_LATEX_SYMBOL_MAP = (
    (r"\approx", "≈"),
    (r"\times", "×"),
    (r"\cdot", "·"),
    (r"\div", "÷"),
    (r"\pm", "±"),
    (r"\leq", "≤"),
    (r"\le", "≤"),
    (r"\geq", "≥"),
    (r"\ge", "≥"),
    (r"\neq", "≠"),
    (r"\ne", "≠"),
    (r"\rightarrow", "→"),
    (r"\to", "→"),
    (r"\leftarrow", "←"),
    (r"\Rightarrow", "⇒"),
    (r"\infty", "∞"),
    (r"\sum", "∑"),
    (r"\Delta", "Δ"),
    (r"\alpha", "α"),
    (r"\beta", "β"),
    (r"\sigma", "σ"),
    (r"\mu", "μ"),
    (r"\%", "%"),
)


def _prettify_latex(expr: str) -> str:
    """将常见简单 LaTeX 数学表达式转为清爽可读的 Unicode 文本。"""
    s = expr.strip()
    # 展开 \text{...} / \mathrm{...} / \mathbf{...}
    for _ in range(3):
        next_s = re.sub(r"\\(?:text|mathrm|mathbf|textbf|operatorname)\{([^{}]*)\}", r"\1", s)
        if next_s == s:
            break
        s = next_s
    # 展开简单 \frac{a}{b} -> (a) / (b)
    s = re.sub(r"\\frac\{([^{}]+)\}\{([^{}]+)\}", r"(\1) / (\2)", s)
    for latex_cmd, uni in _LATEX_SYMBOL_MAP:
        s = s.replace(latex_cmd, uni)
    # 清理残留的多余大括号与间距命令
    s = re.sub(r"\\[,;:! ]", " ", s)
    s = re.sub(r"\s{2,}", " ", s)
    return s.strip()


def _replace_inline_math(text: str, placeholders: List[str]) -> str:
    """仅替换真正的 LaTeX 公式（包含反斜杠命令），绝不误伤 `$30.00` 与 `$95.54` 之间的普通正文。"""

    def _sub(match: re.Match[str]) -> str:
        inner = match.group(1)
        # 严格校验：必须包含反斜杠 LaTeX 命令（如 \text、\times、\frac 等），防止误伤美元金额
        if "\\" not in inner:
            return match.group(0)
        pretty = html.escape(_prettify_latex(inner))
        idx = len(placeholders)
        placeholders.append(
            f'<span style="font-family: Georgia, \'Times New Roman\', serif; '
            f'background-color: #F8FAFC; color: #0F172A; padding: 1px 5px;">{pretty}</span>'
        )
        return f"\x00PH{idx}\x00"

    return re.sub(r"\$([^$\n]{2,200})\$", _sub, text)


def _render_inline(raw: str) -> str:
    """将单行（或单元格内）Markdown 行内语法安全渲染为 HTML。"""
    if not raw:
        return ""

    placeholders: List[str] = []

    def _stash(html_snippet: str) -> str:
        idx = len(placeholders)
        placeholders.append(html_snippet)
        return f"\x00PH{idx}\x00"

    # 1. 先保护行内代码 `code`，防止内部字符被二次解析
    def _code_sub(m: re.Match[str]) -> str:
        code_txt = html.escape(m.group(1))
        return _stash(
            '<code style="font-family: Menlo, Consolas, monospace; font-size: 12px; '
            'background-color: #F1F5F9; color: #0F172A; padding: 1px 4px;">'
            f"{code_txt}</code>"
        )

    text = re.sub(r"`([^`\n]+)`", _code_sub, raw)

    # 2. 保护并美化含反斜杠命令的行内 LaTeX 公式
    text = _replace_inline_math(text, placeholders)

    # 3. 保护 <br> / <br/> 标签为真实换行占位符（彻底根治 Qt MD4C 吞字问题）
    text = _BR_TAG_RE.sub(lambda _: _stash("<br/>"), text)

    # 4. 保护 Markdown 链接 [label](url)（仅放行安全协议）
    def _link_sub(m: re.Match[str]) -> str:
        label = html.escape(m.group(1))
        url = m.group(2).strip()
        if re.match(r"^(?:https?://|mailto:)", url, re.IGNORECASE):
            safe_url = html.escape(url, quote=True)
            return _stash(
                f'<a href="{safe_url}" style="color: #2563EB; text-decoration: none;">{label}</a>'
            )
        return html.escape(m.group(0))

    text = re.sub(r"\[([^\]\n]+)\]\(([^)\s]+)\)", _link_sub, text)

    # 5. 对剩余所有正文进行严格 HTML 转义，防止任意裸 `<...>` 破坏文档结构
    text = html.escape(text)

    # 6. 粗斜体 ***text***
    text = re.sub(
        r"\*\*\*(.+?)\*\*\*",
        r'<strong style="color: #0F172A; font-weight: 700;"><em>\1</em></strong>',
        text,
    )
    # 7. 粗体 **text** 或 __text__
    text = re.sub(
        r"\*\*(.+?)\*\*",
        r'<strong style="color: #0F172A; font-weight: 700;">\1</strong>',
        text,
    )
    text = re.sub(
        r"(?<!\w)__(.+?)__(?!\w)",
        r'<strong style="color: #0F172A; font-weight: 700;">\1</strong>',
        text,
    )
    # 8. 删除线 ~~text~~
    text = re.sub(
        r"~~(.+?)~~",
        r'<span style="text-decoration: line-through; color: #94A3B8;">\1</span>',
        text,
    )
    # 9. 斜体 *text*（避免匹配单个星号或前后带空格的乘号）
    text = re.sub(
        r"(?<!\*)\*(?!\s)(.+?)(?<!\s)\*(?!\*)",
        r'<em style="color: #334155;">\1</em>',
        text,
    )

    # 10. 还原占位符
    for idx, snippet in enumerate(placeholders):
        text = text.replace(f"\x00PH{idx}\x00", snippet)

    return text


def _split_table_row(line: str) -> List[str]:
    """切分 Markdown 表格行单元格，支持转义管道符 `\\|`。"""
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|") and not s.endswith(r"\|"):
        s = s[:-1]
    cells: List[str] = []
    buf: List[str] = []
    i = 0
    while i < len(s):
        if s[i] == "\\" and i + 1 < len(s) and s[i + 1] == "|":
            buf.append("|")
            i += 2
            continue
        if s[i] == "|":
            cells.append("".join(buf).strip())
            buf = []
        else:
            buf.append(s[i])
        i += 1
    cells.append("".join(buf).strip())
    return cells


def _parse_table_alignments(sep_line: str) -> List[str]:
    """解析表格分隔行对齐方式。"""
    aligns: List[str] = []
    for cell in _split_table_row(sep_line):
        c = cell.strip()
        if c.startswith(":") and c.endswith(":"):
            aligns.append("center")
        elif c.endswith(":"):
            aligns.append("right")
        else:
            aligns.append("left")
    return aligns


def _render_table(header_line: str, sep_line: str, body_lines: List[str]) -> str:
    """渲染全宽、斑马纹、带清晰表头与柔和边框的研报级 HTML 表格。"""
    headers = _split_table_row(header_line)
    aligns = _parse_table_alignments(sep_line)
    col_count = max(len(headers), len(aligns))

    def _align(col_idx: int) -> str:
        return aligns[col_idx] if col_idx < len(aligns) else "left"

    out: List[str] = [
        '<table width="100%" cellspacing="0" cellpadding="7" '
        'style="border-collapse: collapse; margin-top: 8px; margin-bottom: 12px; '
        'border: 1px solid #CBD5E1; font-size: 12.5px;">'
    ]
    # 表头
    out.append("<thead><tr>")
    for idx in range(col_count):
        cell_text = headers[idx] if idx < len(headers) else ""
        out.append(
            f'<th align="{_align(idx)}" valign="top" '
            'style="background-color: #F1F5F9; color: #0F172A; font-weight: 700; '
            'border: 1px solid #CBD5E1; padding: 7px 9px;">'
            f"{_render_inline(cell_text)}</th>"
        )
    out.append("</tr></thead><tbody>")

    # 数据行（斑马纹）
    for r_idx, raw_row in enumerate(body_lines):
        cells = _split_table_row(raw_row)
        bg = "#FFFFFF" if r_idx % 2 == 0 else "#F8FAFC"
        out.append("<tr>")
        for idx in range(col_count):
            cell_text = cells[idx] if idx < len(cells) else ""
            out.append(
                f'<td align="{_align(idx)}" valign="top" '
                f'style="background-color: {bg}; color: #1E293B; '
                'border: 1px solid #E2E8F0; padding: 7px 9px; line-height: 1.55;">'
                f"{_render_inline(cell_text)}</td>"
            )
        out.append("</tr>")

    out.append("</tbody></table>")
    return "".join(out)


def _render_list_block(lines: List[str], start: int) -> Tuple[str, int]:
    """解析并渲染支持多级缩进嵌套的有序/无序列表块。"""
    items: List[Tuple[int, str, str]] = []  # (indent, 'ul'|'ol', text)
    i = start
    n = len(lines)
    while i < n:
        line = lines[i]
        m_ul = _UL_RE.match(line)
        m_ol = _OL_RE.match(line)
        if m_ul:
            indent = len(m_ul.group(1).expandtabs(2))
            items.append((indent, "ul", m_ul.group(2).strip()))
            i += 1
            continue
        if m_ol:
            indent = len(m_ol.group(1).expandtabs(2))
            items.append((indent, "ol", m_ol.group(3).strip()))
            i += 1
            continue
        # 续行：仅缩进文本且不是新块结构时拼接到上一条列表项
        if line.strip() and (line.startswith("  ") or line.startswith("\t")) and items:
            if not _HEADING_RE.match(line) and not _HR_RE.match(line) and not _FENCE_RE.match(line):
                prev_indent, prev_tag, prev_txt = items[-1]
                items[-1] = (prev_indent, prev_tag, prev_txt + " " + line.strip())
                i += 1
                continue
        break

    out: List[str] = []
    stack: List[Tuple[int, str]] = []  # (indent, tag)

    for indent, tag, text in items:
        if not stack:
            out.append(f'<{tag} style="margin-top: 4px; margin-bottom: 8px; padding-left: 20px;">')
            stack.append((indent, tag))
        elif indent > stack[-1][0]:
            out.append(f'<{tag} style="margin-top: 3px; margin-bottom: 4px; padding-left: 18px;">')
            stack.append((indent, tag))
        else:
            while len(stack) > 1 and indent < stack[-1][0]:
                _, closing = stack.pop()
                out.append(f"</{closing}>")
            if stack and stack[-1][1] != tag:
                _, closing = stack.pop()
                out.append(f"</{closing}>")
                out.append(f'<{tag} style="margin-top: 4px; margin-bottom: 8px; padding-left: 20px;">')
                stack.append((indent, tag))

        out.append(
            '<li style="margin-top: 3px; margin-bottom: 4px; line-height: 1.65; color: #1E293B;">'
            f"{_render_inline(text)}</li>"
        )

    while stack:
        _, closing = stack.pop()
        out.append(f"</{closing}>")

    return "".join(out), i


def markdown_to_qt_html(markdown_text: str) -> str:
    """将 Markdown 文本安全渲染为适配 Qt QTextBrowser 的美观研报级 HTML。"""
    raw = str(markdown_text or "").replace("\r\n", "\n").replace("\r", "\n")
    if not raw.strip():
        return ""

    lines = raw.split("\n")
    n = len(lines)
    i = 0
    blocks: List[str] = []

    while i < n:
        line = lines[i]
        stripped = line.strip()

        # 空行跳过
        if not stripped:
            i += 1
            continue

        # 1. 围栏代码块 ```
        m_fence = _FENCE_RE.match(line)
        if m_fence:
            i += 1
            code_lines: List[str] = []
            while i < n and not _FENCE_RE.match(lines[i]):
                code_lines.append(lines[i])
                i += 1
            if i < n:
                i += 1  # 跳过结束 ```
            escaped_code = html.escape("\n".join(code_lines))
            blocks.append(
                '<pre style="background-color: #F8FAFC; border: 1px solid #E2E8F0; '
                'padding: 10px 12px; margin: 8px 0 10px 0; '
                'font-family: Menlo, Consolas, monospace; font-size: 12px; '
                f'color: #0F172A; white-space: pre-wrap;">{escaped_code}</pre>'
            )
            continue

        # 2. 块级数学公式 $$ ... $$
        if stripped.startswith("$$"):
            if stripped.endswith("$$") and len(stripped) > 4:
                expr = stripped[2:-2]
                i += 1
            else:
                math_lines = [stripped[2:]]
                i += 1
                while i < n and not lines[i].strip().endswith("$$"):
                    math_lines.append(lines[i])
                    i += 1
                if i < n:
                    math_lines.append(lines[i].strip()[:-2])
                    i += 1
                expr = " ".join(math_lines)
            pretty = html.escape(_prettify_latex(expr))
            blocks.append(
                '<div style="background-color: #F8FAFC; border: 1px solid #E2E8F0; '
                'padding: 8px 12px; margin: 8px 0 10px 0; text-align: center; '
                "font-family: Georgia, 'Times New Roman', serif; font-size: 13px; "
                f'color: #0F172A;">{pretty}</div>'
            )
            continue

        # 3. 水平分割线
        if _HR_RE.match(line):
            blocks.append('<hr style="border: none; border-top: 1px solid #E2E8F0; margin: 12px 0;" />')
            i += 1
            continue

        # 4. 标题 # ~ ######
        m_h = _HEADING_RE.match(line)
        if m_h:
            level = len(m_h.group(1))
            title_html = _render_inline(m_h.group(2).strip())
            if level == 1:
                style = (
                    "font-size: 17px; font-weight: 700; color: #0F172A; "
                    "margin-top: 14px; margin-bottom: 8px; padding-bottom: 4px; "
                    "border-bottom: 1px solid #E2E8F0;"
                )
            elif level == 2:
                style = (
                    "font-size: 15px; font-weight: 700; color: #0F172A; "
                    "margin-top: 14px; margin-bottom: 6px; padding-bottom: 3px; "
                    "border-bottom: 1px solid #E2E8F0;"
                )
            elif level == 3:
                style = (
                    "font-size: 14px; font-weight: 700; color: #1E3A8A; "
                    "margin-top: 12px; margin-bottom: 5px;"
                )
            else:
                style = (
                    "font-size: 13px; font-weight: 700; color: #334155; "
                    "margin-top: 10px; margin-bottom: 4px;"
                )
            blocks.append(f'<h{level} style="{style}">{title_html}</h{level}>')
            i += 1
            continue

        # 5. Markdown 表格（当前行含 `|` 且下一行是合法分隔行）
        if "|" in line and i + 1 < n and _TABLE_SEP_RE.match(lines[i + 1]):
            header_line = line
            sep_line = lines[i + 1]
            i += 2
            body_lines: List[str] = []
            while i < n and lines[i].strip() and "|" in lines[i]:
                if _HR_RE.match(lines[i]) or _HEADING_RE.match(lines[i]):
                    break
                body_lines.append(lines[i])
                i += 1
            blocks.append(_render_table(header_line, sep_line, body_lines))
            continue

        # 6. 引用块 >
        if _QUOTE_RE.match(line):
            q_lines: List[str] = []
            while i < n and _QUOTE_RE.match(lines[i]):
                q_lines.append(_QUOTE_RE.match(lines[i]).group(1))  # type: ignore[union-attr]
                i += 1
            q_html = "<br/>".join(_render_inline(ql) for ql in q_lines)
            blocks.append(
                '<table width="100%" cellspacing="0" cellpadding="0" '
                'style="margin-top: 6px; margin-bottom: 10px; border-collapse: collapse;">'
                '<tr><td width="4" style="background-color: #3B82F6;"></td>'
                '<td style="background-color: #F8FAFC; color: #334155; '
                'padding: 8px 12px; line-height: 1.6; font-size: 12.5px;">'
                f"{q_html}</td></tr></table>"
            )
            continue

        # 7. 有序 / 无序列表
        if _UL_RE.match(line) or _OL_RE.match(line):
            list_html, i = _render_list_block(lines, i)
            blocks.append(list_html)
            continue

        # 8. 普通段落（合并连续普通文本行，遇空行或块级元素停止）
        para_lines: List[str] = [stripped]
        i += 1
        while i < n:
            nxt = lines[i]
            nxt_s = nxt.strip()
            if (
                not nxt_s
                or _FENCE_RE.match(nxt)
                or _HEADING_RE.match(nxt)
                or _HR_RE.match(nxt)
                or _QUOTE_RE.match(nxt)
                or _UL_RE.match(nxt)
                or _OL_RE.match(nxt)
                or nxt_s.startswith("$$")
                or ("|" in nxt and i + 1 < n and _TABLE_SEP_RE.match(lines[i + 1]))
            ):
                break
            para_lines.append(nxt_s)
            i += 1
        para_html = "<br/>".join(_render_inline(pl) for pl in para_lines)
        blocks.append(
            '<p style="margin-top: 4px; margin-bottom: 8px; line-height: 1.68; color: #1E293B;">'
            f"{para_html}</p>"
        )

    body = "\n".join(blocks)
    return (
        '<html><head><meta charset="utf-8" />'
        "<style>"
        "body { font-family: -apple-system, 'PingFang SC', 'Helvetica Neue', sans-serif; "
        "font-size: 13px; color: #1E293B; line-height: 1.65; margin: 0; padding: 0; }"
        "a { color: #2563EB; text-decoration: none; }"
        "</style></head><body>"
        f"{body}</body></html>"
    )
