# 如何与 jev-uf 交互

本文面向**调用方（Agent / 编排脚本）**，说明 jev-uf（jev-ultrafast）暴露了哪些接口、每一层该由谁决定、以及哪些事情不能交给模型。

## 1. 四层链路

```
编排脚本（你写的）
   ↓  import
jev_ultrafast.Agent / .Browser      感知（snapshot.js）+ 执行（node 句柄）+ 决策（Jev）
   ↓  ensure_daemon()
browser-harness daemon              BU_CDP_URL=http://127.0.0.1:9222
   ↓  WebSocket
Chrome 的 CDP :9222
```

只有最上面两层是你写的。daemon 负责多进程共享一个浏览器连接，CDP 负责真正操作 Chrome。

## 2. 两种控制粒度

| 场景 | 用哪个 | 是否调用模型 |
|---|---|---|
| 打开页面、抓 DOM、抓链接、验证某个元素在不在 | `Browser` | **否**，零成本 |
| 多步交互：下单、填表单、走流程 | `Agent` | **是**，每轮一次 Jev 请求 |

判断标准只有一条：**下一步该点什么，是否取决于上一步点完页面变成什么样**。是 → `Agent`；否 → `Browser`。

### 2.1 Browser：当遥控器用

```python
from jev_ultrafast import Browser

browser = Browser(url, background=False, reuse=True, settle_seconds=2)
page = browser.observe(screenshot=False)   # 元素表
browser.act(page["actions"][6], page)      # 执行
browser.evaluate("JSON.stringify([...document.querySelectorAll('a')].map(a => a.href))")
```

参数：

- `background` — 默认 `True`（后台标签页，Chrome 当前可见页不变）。**要让人看见就传 `False`**。
- `reuse` — 按 `netloc + path` 复用已打开的标签页。保登录态、避免重复导航（Cloudflare 类站点会限流）。
- `settle_seconds` — navigate 后额外等待。SPA（交易面板等）必须给，否则首次观察会 `StalePage`。

`observe()` 返回的元素字段是 `id / kind / label / node / rect / role / value`——**没有 `index` 和 `name`**，按后两者取字段会全部取空。`node` 是页面内元素句柄，`act()` 靠它执行，不拼选择器、不用坐标。

`Browser.close()` 只在 `owned=True`（自己开的标签页）时才真的关；`reuse=True` 时 `owned=False`，可以放心调。

### 2.2 Agent：唯一接口是自然语言 goal

```python
agent = Agent(
    url, goal,
    background=False, settle_seconds=5, reuse=True,
    screenshots=True, record_dir=Path("artifacts/limit_buy/frames"),
)
for state in agent.run():
    print(state["elapsed_ms"], state["status"])
```

**不要写指令序列**。goal 是一段把边界写死的白话：

> Place a limit buy (long) order for 0.001 BTC at price 70000 using post-only. Click the 'Limit' tab **if not already selected**. Fill the 'Price' textbox with 70000. Tick the 'TP/SL' checkbox **if it is not already checked** — **never untick it**.

三条硬经验：

1. **具体数值写死**（`70000`、`0.001`），别写"一个合理的价格"。
2. **条件动作写清**（"if not already selected"）。
3. **反向边界必须写**（"never untick"）。不写这句，模型会反复点同一个勾选框直到超步数——实测踩过。

每轮内部：

```
observe() → 元素表
   ↓  一次 Jev 请求，投机并行问 4 个头
operation（CLICK / TYPE_TEXT / SELECT / SCROLL_UP / SCROLL_DOWN / WAIT / DONE / BLOCKED）
+ click_target / type_text_target / select_target
   ↓  只有被 operation 选中的那个 target 能执行
act() → node 句柄 → 页面
   ↓  仅 TYPE_TEXT 时调小文本模型生成字符串
history 回写，下一轮
```

operation 与三个候选 target 在**一次网络往返里同时出**，只用匹配的那一个——这是它快的原因。

## 3. 编排者必须是确定性代码

jev-uf 没有 needHuman 交接、`BLOCKED` 语义含糊。所以以下事情**全在调用方**，不能交给模型：

- 需求点之间的顺序与依赖
- 前置条件检查（登录态、账户模式、页面是否就绪）
- 每个点跑完的**通过/不通过判定**
- 失败重试次数、何时交回人
- 需求点之间的状态归位
- 汇总报告

## 4. 判定：不要信模型说 DONE

Jev 自家评测 4 个工作流均值 **67.8%**，它赢的是成本和速度，不是准确率。模型说 `done` 不等于任务完成。

**判定必须是确定性断言**，且优先用抗抖动的量：

| 断言 | 可靠性 | 说明 |
|---|---|---|
| 计数变化（`Open orders (N)` 前后对比） | 强 | 首选。下单类验证用它 |
| URL 变化 | 强 | 导航类验证用它 |
| `evaluate()` 跑 JS 返回布尔 | 强 | 精确到 DOM 值 |
| 页面文本包含某串 | **弱** | 面板异步渲染，文本会晚到 → **只作 info，不作 pass/fail** |

实测教训：把"价格文本可见"当判据出现过假阴性，最后改成"Open orders 计数必须增长"。

## 5. 能力边界（别硬试）

DOM reader 不支持：shadow DOM、iframe、canvas、文件上传、嵌套滚动、任意键盘控件。图表（TradingView 是 canvas）尤其典型。需求点落在这些区域的，直接标记"不适用"，别花额度试——试了也是 `BLOCKED`。

## 6. 多需求点验证

一个需求点 = 一次窄 goal 的独立 Agent run + 一次确定性断言。**不要把多个需求点串进一条长 goal**——失败时定位不到是哪个点挂的，且只能整条重跑。

流程：

```
0  拆解：N 个需求点，每个写清 前置状态 / goal / 断言 / 归位
1  环境基线（确定性）：CDP 通、标签页在、登录态、账户模式、基准计数
2  逐点串行（复用同一标签页）：
     归位 → Agent run（窄 goal，步数有上限）→ 独立断言 → 归档
     失败 → 重跑 1 次 → 仍失败 → 标 blocked 交人，继续下一个（不阻塞）
3  汇总：需求点 / 结果 / 证据 / 步数 / 重跑次数 / 截图
```

两条硬约束：

- **需求点之间必须归位**：上一个点留下的半填表单、未关弹窗会污染下一个点。复用标签页省的是登录态和限流，代价就是状态污染。
- **不要并行抢同一个标签页**：多 Agent 同时导航会互相覆盖。要并行就走多 `BU_NAME` daemon 或多 `--user-data-dir` 实例；对验证场景，串行更稳。

可执行的编排实现见 [`examples/verify.py`](../examples/verify.py)。

## 7. 常见故障

| 现象 | 原因 | 处理 |
|---|---|---|
| 改了 `BU_CDP_URL` 不生效 | daemon 存活时 `ensure_daemon()` 直接复用旧连接 | `browser-harness --reload` |
| 首次观察就 `StalePage` | SPA 未渲染完 | `settle_seconds=5` |
| 标签页看不见 | `background` 默认 `True` | 传 `background=False` |
| 下载类点击不落文件 | Chrome 要求真实用户手势 | 用 `evaluate()` 抓 `a.href`，交给 `curl` |
| 模型反复点同一个勾选框 | goal 没写反向边界 | goal 里补 "if not already checked; never untick" |
| 反复导航被拦 | Cloudflare 限流 | `reuse=True` 复用已开标签页 |
