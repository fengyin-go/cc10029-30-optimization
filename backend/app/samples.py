"""选择器校验样本：按归属站点维护页面样本，供选择器校验读取。

样本是不可信的外部页面快照：只做内存存放与只读匹配，不执行其中任何脚本。
内置样本覆盖三类典型场景：
- 正常分页页面（列表/详情/字段/分页全部可命中）；
- 无分页页面（分页链接缺失，用来区分“空分页”和“匹配失败”）；
- 结构错位页面（类名与规则不一致，用来制造匹配失败、字段缺失）。
"""
from __future__ import annotations

from typing import Any

BUILTIN_SAMPLES: list[dict[str, Any]] = [
    {
        "sample_id": "SAMP-0001",
        "sample_name": "新闻列表页样本（含分页）",
        "site_name": "抽取规则样例1",
        "sample_version": "v3",
        "pagination": {"type": "link", "selector": "a.page-next", "param": "page"},
        "html": """
<html><body>
  <ul class="news-list">
    <li class="item">
      <a class="detail-link" href="/news/1">
        <span class="title">首批标题一</span>
        <time class="pub-time">2026-11-01</time>
      </a>
    </li>
    <li class="item">
      <a class="detail-link" href="/news/2">
        <span class="title">首批标题二</span>
        <time class="pub-time">2026-11-02</time>
      </a>
    </li>
    <li class="item">
      <a class="detail-link" href="/news/3">
        <span class="title">首批标题三</span>
        <time class="pub-time">2026-11-03</time>
      </a>
    </li>
  </ul>
  <nav class="pager"><a class="page-next" href="/list?page=2">下一页</a></nav>
</body></html>
""",
    },
    {
        "sample_id": "SAMP-0002",
        "sample_name": "单页文章样本（无分页控件）",
        "site_name": "抽取规则样例2",
        "sample_version": "v2",
        "pagination": {"type": "none", "selector": "", "param": ""},
        "html": """
<html><body>
  <ul class="news-list">
    <li class="item">
      <a class="detail-link" href="/post/1">
        <span class="title">单页样本标题一</span>
        <time class="pub-time">2026-11-10</time>
      </a>
    </li>
    <li class="item">
      <a class="detail-link" href="/post/2">
        <span class="title">单页样本标题二</span>
        <time class="pub-time">2026-11-11</time>
      </a>
    </li>
  </ul>
</body></html>
""",
    },
    {
        "sample_id": "SAMP-0003",
        "sample_name": "结构改版样本（类名错位）",
        "site_name": "抽取规则样例3",
        "sample_version": "v1",
        "pagination": {"type": "link", "selector": "a.next-page", "param": "pn"},
        "html": """
<html><body>
  <div class="article-cards">
    <div class="card">
      <a class="card-link" href="/a/1">
        <h3 class="headline">改版后标题一</h3>
        <span class="date">2026-11-12</span>
      </a>
    </div>
    <div class="card">
      <a class="card-link" href="/a/2">
        <h3 class="headline">改版后标题二</h3>
        <span class="date">2026-11-13</span>
      </a>
    </div>
  </div>
  <nav class="pager"><a class="next-page" href="/cards?pn=2">下一页</a></nav>
</body></html>
""",
    },
    {
        "sample_id": "SAMP-0004",
        "sample_name": "通用演示站点样本",
        "site_name": "演示站点",
        "sample_version": "v1",
        "pagination": {"type": "link", "selector": "a.page-next", "param": "page"},
        "html": """
<html><body>
  <ul class="news-list">
    <li class="item"><a class="detail-link" href="/d/1"><span class="title">演示标题一</span><time class="pub-time">2026-11-15</time></a></li>
    <li class="item"><a class="detail-link" href="/d/2"><span class="title">演示标题二</span><time class="pub-time">2026-11-16</time></a></li>
  </ul>
  <a class="page-next" href="/demo?page=2">下一页</a>
</body></html>
""",
    },
]
