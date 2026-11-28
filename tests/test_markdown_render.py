"""桌面端 Markdown -> Qt HTML 渲染器回归测试。"""
from __future__ import annotations

from PySide6.QtGui import QTextDocument

from portfolio.ui.markdown_render import markdown_to_qt_html


def test_table_with_br_tags_preserves_all_cells_and_following_sections():
    """复现并验证：Markdown 表格内含 `<br>` 时，不得吞掉后续单元格和下文列表。"""
    md = """### 三、 未来一个月走势的情景推演

| 情景假设 | 触发条件（核心变量组合） | 预计走势特征 |
| :--- | :--- | :--- |
| **乐观情景<br>（震荡上行 / 突破）** | • USDC 流通量加速净流入<br>• 加密市场走强 | **放量上攻，跑赢纳指** |
| **悲观情景<br>（承压回撤）** | • 降息预期提前<br>• 链上净赎回 | **估值与盈利预期双杀** |

---

### 四、 未来一个月必须警惕的三大下行风险

1. **竞争壁垒松动风险**：传统银行入局稳定币。
2. **渠道议价权恶化风险**：净利润率承压。
3. **高估值下的波动率杀伤**：单日波幅大。
"""
    rendered = markdown_to_qt_html(md)
    doc = QTextDocument()
    doc.setHtml(rendered)
    plain = doc.toPlainText()

    assert "乐观情景" in plain
    assert "震荡上行 / 突破" in plain
    assert "USDC 流通量加速净流入" in plain
    assert "放量上攻，跑赢纳指" in plain
    assert "悲观情景" in plain
    assert "估值与盈利预期双杀" in plain
    assert "竞争壁垒松动风险" in plain
    assert "渠道议价权恶化风险" in plain
    assert "高估值下的波动率杀伤" in plain
    assert "<br/>" in rendered


def test_latex_prettified_without_breaking_dollar_prices():
    """含反斜杠命令的 LaTeX 公式应美化，但普通美元金额不应被误伤。"""
    md = (
        r"公式：$\text{核心营收} \approx \text{USDC 日均流通量} \times \text{收益率}$。"
        "\n\n"
        "成交价 $30.00 与 $95.54，区间涨幅 +12.5%。"
    )
    rendered = markdown_to_qt_html(md)
    doc = QTextDocument()
    doc.setHtml(rendered)
    plain = doc.toPlainText()

    assert "核心营收 ≈ USDC 日均流通量 × 收益率" in plain
    assert "$30.00 与 $95.54" in plain


def test_nested_lists_blockquote_code_and_html_escape():
    """验证嵌套列表、引用块、代码块以及裸尖括号安全转义。"""
    md = """1. **一级条目**
   - 二级要点：仓位 <10% 且回撤 >5%
2. **二级条目**

> 核心提示：不要在财报前重仓 3x 杠杆。

```python
x = 1 < 2
```
"""
    rendered = markdown_to_qt_html(md)
    doc = QTextDocument()
    doc.setHtml(rendered)
    plain = doc.toPlainText()

    assert "一级条目" in plain
    assert "仓位 <10% 且回撤 >5%" in plain
    assert "核心提示：不要在财报前重仓 3x 杠杆。" in plain
    assert "x = 1 < 2" in plain
