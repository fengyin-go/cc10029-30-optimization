"""选择器校验引擎：读取样本 HTML，按规则字段逐项校验并分类失败原因。

只依赖标准库（html.parser），样本 HTML 只被解析为节点树，不会被执行。

三类问题严格区分，禁止合并成同一种“选择器错误”：
- EMPTY_PAGINATION 空分页：分页参数为空，页面本身也没有分页控件（非阻断提示）；
- MATCH_FAIL       匹配失败：规则给了选择器，但在样本里命中 0 个节点（阻断）；
- FIELD_MISSING    字段缺失：选择器能命中节点，但节点里缺少要抽取的子字段（阻断）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any

# 错误分类常量：服务层、接口层与前端共用同一套取值
EMPTY_PAGINATION = "EMPTY_PAGINATION"
MATCH_FAIL = "MATCH_FAIL"
FIELD_MISSING = "FIELD_MISSING"
PASS = "PASS"

CATEGORY_LABELS = {
    EMPTY_PAGINATION: "空分页",
    MATCH_FAIL: "匹配失败",
    FIELD_MISSING: "字段缺失",
    PASS: "通过",
}

# 空分页只是提示，不阻断规则进入采集；匹配失败与字段缺失是阻断项
BLOCKING_CATEGORIES = {MATCH_FAIL, FIELD_MISSING}
NON_BLOCKING_CATEGORIES = {EMPTY_PAGINATION}

# 参与选择器校验的规则字段（等待条件、字符编码只做快照登记，不参与匹配）
SELECTOR_FIELDS = ["列表选择器", "详情选择器", "字段选择器", "分页参数", "等待条件"]
# 字段选择器里允许声明的抽取子字段（命中列表项后检查这些子字段是否存在）
FIELD_SELECTOR_PARTS = {
    "title": "标题",
    "url": "链接",
    "time": "时间",
}

VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}


@dataclass
class HtmlNode:
    tag: str
    attrs: dict[str, str]
    children: list["HtmlNode"] = field(default_factory=list)
    parent: "HtmlNode | None" = None
    text: str = ""

    @property
    def classes(self) -> list[str]:
        return self.attrs.get("class", "").split()


class _TreeBuilder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = HtmlNode("#document", {})
        self._stack = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = HtmlNode(tag=tag.lower(), attrs={k.lower(): (v or "") for k, v in attrs})
        node.parent = self._stack[-1]
        self._stack[-1].children.append(node)
        if tag.lower() not in VOID_TAGS:
            self._stack.append(node)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = HtmlNode(tag=tag.lower(), attrs={k.lower(): (v or "") for k, v in attrs})
        node.parent = self._stack[-1]
        self._stack[-1].children.append(node)

    def handle_endtag(self, tag: str) -> None:
        for depth in range(len(self._stack) - 1, 0, -1):
            if self._stack[depth].tag == tag.lower():
                del self._stack[depth:]
                break

    def handle_data(self, data: str) -> None:
        text = data.strip()
        if text:
            self._stack[-1].text = (self._stack[-1].text + " " + text).strip()


def parse_html(html: str) -> HtmlNode:
    builder = _TreeBuilder()
    builder.feed(html or "")
    builder.close()
    return builder.root


def _walk(node: HtmlNode) -> list[HtmlNode]:
    result: list[HtmlNode] = []
    for child in node.children:
        result.append(child)
        result.extend(_walk(child))
    return result


# --------------------------------------------------------------------------- #
# CSS 选择器（支持本平台规则实际会用到的子集：tag / .class / #id / [attr] /
# [attr=value] 以及它们的后代组合，逗号分组）
# ---------------------------------------------------------------------------
@dataclass
class _SimpleSelector:
    tag: str | None = None
    id: str | None = None
    classes: list[str] = field(default_factory=list)
    attr_present: list[str] = field(default_factory=list)
    attr_equals: dict[str, str] = field(default_factory=dict)


def _parse_simple(token: str) -> _SimpleSelector:
    sel = _SimpleSelector()
    buf = ""
    i = 0
    while i < len(token):
        ch = token[i]
        if ch == ".":
            if buf and sel.tag is None and sel.id is None and not sel.classes:
                sel.tag = buf
            buf = ""
            i += 1
            name = ""
            while i < len(token) and token[i] not in ".[#":
                name += token[i]
                i += 1
            sel.classes.append(name)
            continue
        if ch == "#":
            if buf and sel.tag is None:
                sel.tag = buf
            buf = ""
            i += 1
            name = ""
            while i < len(token) and token[i] not in ".[#":
                name += token[i]
                i += 1
            sel.id = name
            continue
        if ch == "[":
            if buf and sel.tag is None:
                sel.tag = buf
            buf = ""
            end = token.index("]", i)
            inner = token[i + 1:end]
            if "=" in inner:
                key, value = inner.split("=", 1)
                sel.attr_equals[key.strip().lower()] = value.strip().strip("\"'")
            else:
                sel.attr_present.append(inner.strip().lower())
            i = end + 1
            continue
        buf += ch
        i += 1
    if buf and sel.tag is None:
        sel.tag = buf.lower()
    return sel


def _match_simple(node: HtmlNode, sel: _SimpleSelector) -> bool:
    if sel.tag and node.tag != sel.tag:
        return False
    if sel.id and node.attrs.get("id") != sel.id:
        return False
    if any(name not in node.classes for name in sel.classes):
        return False
    if any(attr not in node.attrs for attr in sel.attr_present):
        return False
    if any(node.attrs.get(key) != value for key, value in sel.attr_equals.items()):
        return False
    return True


def _match_descendants(nodes: list[HtmlNode], tokens: list[str]) -> list[HtmlNode]:
    current = nodes
    for token in tokens:
        simple = _parse_simple(token)
        nxt: list[HtmlNode] = []
        for node in current:
            for descendant in _walk(node):
                if _match_simple(descendant, simple):
                    nxt.append(descendant)
        current = nxt
        if not current:
            break
    return current


def css_select(root: HtmlNode, selector: str) -> list[HtmlNode]:
    matched: list[HtmlNode] = []
    for group in (selector or "").split(","):
        tokens = group.split()
        if not tokens:
            continue
        first = _parse_simple(tokens[0])
        seeds = [node for node in _walk(root) if _match_simple(node, first)]
        if len(tokens) > 1:
            seeds = _match_descendants(seeds, tokens[1:])
        matched.extend(seeds)
    return matched


# --------------------------------------------------------------------------- #
# XPath 子集：//tag、//tag[@class='x']、//tag[@id='x']、//tag/a 与 text()
# ---------------------------------------------------------------------------
def _xpath_tokens(selector: str) -> list[str]:
    path = selector.strip()
    if path.startswith("//"):
        path = path[2:]
    elif path.startswith("/"):
        path = path[1:]
    return [step for step in path.split("/") if step and step != "text()"]


def _xpath_step_match(node: HtmlNode, step: str) -> bool:
    if "[" not in step:
        return node.tag == step.lower()
    tag, predicate = step.split("[", 1)
    predicate = predicate.rstrip("]")
    if node.tag != tag.lower():
        return False
    if predicate.startswith("@"):
        body = predicate[1:]
        if "=" in body:
            key, value = body.split("=", 1)
            return node.attrs.get(key.strip().lower()) == value.strip().strip("\"'")
        return body.strip().lower() in node.attrs
    return False


def xpath_select(root: HtmlNode, selector: str) -> list[HtmlNode]:
    tokens = _xpath_tokens(selector)
    if not tokens:
        return []
    current = [node for node in _walk(root) if _xpath_step_match(node, tokens[0])]
    for step in tokens[1:]:
        nxt: list[HtmlNode] = []
        for node in current:
            nxt.extend(child for child in node.children if _xpath_step_match(child, step))
        current = nxt
    return current


def select_nodes(root: HtmlNode, selector: str) -> list[HtmlNode]:
    text = (selector or "").strip()
    if not text:
        return []
    if text.startswith(("/", "(")):
        return xpath_select(root, text)
    return css_select(root, text)


def _is_xpath(value: str) -> bool:
    return value.strip().startswith(("/", "("))


def _field_sub_selectors(raw: str) -> dict[str, str]:
    """解析字段选择器：支持 `title=.title;url=a@href;time=.pub-time` 形式。"""
    parts: dict[str, str] = {}
    for chunk in (raw or "").split(";"):
        chunk = chunk.strip()
        if not chunk or "=" not in chunk:
            continue
        key, value = chunk.split("=", 1)
        key = key.strip().lower()
        if key in FIELD_SELECTOR_PARTS:
            parts[key] = value.strip()
    return parts


def _attr_or_text(node: HtmlNode, expression: str) -> str:
    if "@" in expression and not _is_xpath(expression):
        selector, attr = expression.rsplit("@", 1)
        targets = select_nodes(node, selector) if selector.strip() else [node]
        return targets[0].attrs.get(attr.strip().lower(), "") if targets else ""
    targets = select_nodes(node, expression)
    if not targets:
        return ""
    return targets[0].text or " ".join(targets[0].attrs.values())


def validate_rule(rule_snapshot: dict[str, Any], sample: dict[str, Any]) -> list[dict[str, Any]]:
    """对一条规则快照执行样本校验，返回按规则字段定位的校验项列表。

    每个校验项带 category（PASS/EMPTY_PAGINATION/MATCH_FAIL/FIELD_MISSING）、
    blocking（是否阻断）与命中数量，报告层据此判断规则能否进入采集。
    """
    root = parse_html(str(sample.get("html", "")))
    items: list[dict[str, Any]] = []

    def add(field_name: str, category: str, *, matched: int, detail: str, expression: str) -> None:
        items.append({
            "field": field_name,
            "expression": expression,
            "category": category,
            "category_label": CATEGORY_LABELS[category],
            "blocking": category in BLOCKING_CATEGORIES,
            "matched": matched,
            "detail": detail,
        })

    # 列表选择器：命中 0 即匹配失败
    list_expr = str(rule_snapshot.get("列表选择器") or "").strip()
    if not list_expr:
        add("列表选择器", MATCH_FAIL, matched=0, detail="规则未提供列表选择器，无法定位样本列表", expression=list_expr)
    else:
        hits = select_nodes(root, list_expr)
        if hits:
            add("列表选择器", PASS, matched=len(hits), detail=f"样本中命中 {len(hits)} 个列表节点", expression=list_expr)
        else:
            add("列表选择器", MATCH_FAIL, matched=0, detail="列表选择器在样本中未命中任何节点", expression=list_expr)

    # 详情选择器
    detail_expr = str(rule_snapshot.get("详情选择器") or "").strip()
    if not detail_expr:
        add("详情选择器", MATCH_FAIL, matched=0, detail="规则未提供详情选择器，无法定位详情入口", expression=detail_expr)
    else:
        hits = select_nodes(root, detail_expr)
        if hits:
            add("详情选择器", PASS, matched=len(hits), detail=f"样本中命中 {len(hits)} 个详情入口", expression=detail_expr)
        else:
            add("详情选择器", MATCH_FAIL, matched=0, detail="详情选择器在样本中未命中任何节点", expression=detail_expr)

    # 字段选择器：先要求列表存在，再在列表项内检查子字段，缺的是字段缺失而非匹配失败
    field_expr = str(rule_snapshot.get("字段选择器") or "").strip()
    list_nodes = select_nodes(root, list_expr) if list_expr else []
    sub_selectors = _field_sub_selectors(field_expr)
    if not field_expr:
        add("字段选择器", MATCH_FAIL, matched=0, detail="规则未提供字段选择器，无法抽取结构化字段", expression=field_expr)
    elif not list_nodes:
        add("字段选择器", MATCH_FAIL, matched=0, detail="列表选择器未命中，字段选择器失去挂载容器", expression=field_expr)
    elif not sub_selectors:
        add("字段选择器", MATCH_FAIL, matched=0,
            detail="字段选择器未声明 title/url/time 子字段，格式应为 title=.title;url=a@href;time=.pub-time",
            expression=field_expr)
    else:
        scope = list_nodes[0]
        missing_parts: list[str] = []
        resolved: dict[str, str] = {}
        for key, expression in sub_selectors.items():
            value = _attr_or_text(scope, expression).strip()
            if value:
                resolved[key] = value
            else:
                missing_parts.append(FIELD_SELECTOR_PARTS[key])
        if missing_parts:
            add("字段选择器", FIELD_MISSING, matched=len(resolved),
                detail=f"列表项内缺少子字段：{'、'.join(missing_parts)}", expression=field_expr)
        else:
            add("字段选择器", PASS, matched=len(resolved),
                detail=f"列表项内 {len(resolved)} 个子字段全部抽取成功", expression=field_expr)

    # 分页参数：空值且样本无分页控件 -> 空分页（非阻断）；给了选择器却没命中 -> 匹配失败（阻断）
    page_expr = str(rule_snapshot.get("分页参数") or "").strip()
    sample_page = sample.get("pagination") or {}
    sample_has_pager = bool(sample_page.get("selector"))
    if not page_expr:
        if sample_has_pager:
            add("分页参数", MATCH_FAIL, matched=0,
                detail=f"规则分页参数为空，但样本存在分页控件（{sample_page.get('selector')}），采集将漏翻页",
                expression=page_expr)
        else:
            add("分页参数", EMPTY_PAGINATION, matched=0,
                detail="规则分页参数为空，样本为单页结构且无分页控件，按空分页提示处理，不阻断采集",
                expression=page_expr)
    else:
        # 查询参数形式（如 page=2 / param:page）按样本分页元数据校验；其余按选择器匹配
        looks_like_param = ("=" in page_expr and not page_expr.startswith((".", "#", "/", "("))) or page_expr.startswith("param:")
        if looks_like_param:
            param_name = page_expr.split("=", 1)[0].replace("param:", "").strip()
            if sample_page.get("param") and param_name == sample_page["param"]:
                add("分页参数", PASS, matched=1, detail=f"分页参数 {param_name} 与样本翻页参数一致", expression=page_expr)
            else:
                add("分页参数", MATCH_FAIL, matched=0,
                    detail=f"分页参数 {param_name} 与样本翻页参数 {sample_page.get('param') or '（样本无分页）'} 不一致",
                    expression=page_expr)
        else:
            hits = select_nodes(root, page_expr)
            if hits:
                add("分页参数", PASS, matched=len(hits), detail=f"样本中命中 {len(hits)} 个分页控件", expression=page_expr)
            else:
                add("分页参数", MATCH_FAIL, matched=0, detail="分页选择器在样本中未命中分页控件", expression=page_expr)

    # 等待条件：空值只提示，不阻断；有值则检查样本是否出现对应选择器
    wait_expr = str(rule_snapshot.get("等待条件") or "").strip()
    if not wait_expr:
        add("等待条件", PASS, matched=0, detail="未配置等待条件，按页面加载完成即采集处理", expression=wait_expr)
    else:
        hits = select_nodes(root, wait_expr)
        if hits:
            add("等待条件", PASS, matched=len(hits), detail="等待条件节点在样本中存在", expression=wait_expr)
        else:
            add("等待条件", MATCH_FAIL, matched=0, detail="等待条件节点在样本中不存在，采集可能提前触发", expression=wait_expr)

    return items
