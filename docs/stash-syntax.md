# Stash 覆写语法参考（来自官方文档实测）

来源：https://stash.wiki —— 任何语法疑问先查这里，不要凭 Loon/Surge/Clash 经验推断。

## 覆写合并规则（configuration/override）

- 多个覆写**按列表从上到下依次覆盖**
- 简单类型（string/number/boolean）：同名键直接覆盖
- 字典类型：**递归键值合并**
- 数组类型：**覆写文件的数组插入到原数组前面**（追加，不是替换）
- `#!replace`：该键完全覆盖，不走合并
- `#!append`：显式追加（默认行为）
- 当前版本**不支持**修改数组中的特定元素

> 推论：覆写里的数组是"叠加"到已有配置，不是"替换"。想顶掉原有数组必须用 `#!replace`。

## http 段合法结构（http-engine/rewrite）

```yaml
http:
  url-rewrite:
    - ^http://g\.cn https://www.google.com transparent
    - ^https?://www.google.cn https://www.google.com 302
    - ^https?://ad\.example - reject
  header-rewrite:
    - ^http://g\.cn request-add DNT 1
    - ^http://g\.cn request-del DNT
    - ^http://g\.cn request-replace DNT 1
    - ^http://g\.cn request-replace-regex User-Agent Go-http-client curl
    - ^http://g\.cn response-add DNT 1
    - ^http://g\.cn response-del DNT
    - ^http://g\.cn response-replace DNT 1
    - ^http://g\.cn response-replace-regex User-Agent Go-http-client curl
  body-rewrite:
    - '^https://api\.example\.com/ response-jq del(.debug) | .name = "Stash"'
  mock:
    - match: ^https?://ad\.example
      status-code: 503
```

### url-rewrite 合法动作
`transparent` `302` `307` `reject` `reject-200` `reject-img` `reject-dict` `reject-array`

### header-rewrite 合法动作
`request-add` `request-del` `request-replace` `request-replace-regex`
`response-add` `response-del` `response-replace` `response-replace-regex`

> **没有** `response-header-add`，**没有** `header-add`，**不加** `http-response` 前缀。
> 格式就是 `<正则> <动作> <参数...>`。

### body-rewrite 合法动作
`request-replace-regex` `response-replace-regex`（第 4 个参数 disabled/enabled 可选）
`request-jq` `response-jq`
`request-json-replace` `response-json-replace`
`request-json-add` `response-json-add`
`request-json-del` `response-json-del`

> **没有** `response-header-add`。header 操作属于 header-rewrite 段，不属于 body-rewrite。

## script-providers（script/manage-script）

```yaml
script-providers:
  your-script:
    url: https://example.com/s.js
    interval: 86400
    headers:              # 可选，给下载请求加头
      Authorization: Bearer xxx
      Accept: application/javascript
```

- `payload`（iOS 3.2.5+）：内联脚本内容，**合法字段**
- 同时有 `url` 和 `payload` 时：启动写 payload 到本地缓存，之后用 url 覆盖
- `path`：指定本地缓存位置，限制在配置文件目录内
- 只有 `path` 没有 `url`：不自动更新

> 复杂脚本用 payload 会显著拖慢解析，官方不建议。

## http.script 条目字段（script/rewrite-requests）

```yaml
http:
  script:
    - name: your-script-name
      match: ^https://example\.com/
      type: response        # request | response
      require-body: true
      timeout: 10
      argument: '{"k":"v"}'  # 字符串
      engine: auto           # auto | webkit | jsc（iOS 3.6+）
      binary-mode: true      # body 以 Uint8Array 传入（iOS 2.0.2+）
      max-size: 10485760     # 超过则不触发
```

- `name` 必须能在 `script-providers` 里找到
- 一个 provider 可被多条 script 引用（共用同一份代码，用 `argument` 区分行为）
- 脚本必须调用 `$done(value)` 释放资源

## 覆写参数（configuration/override）

```yaml
arguments-version: 1
arguments:
  dns_server:
    type: input            # input | select | switch
    title: DNS 地址
    default: https://dns.alidns.com/dns-query
    required: true
```

引用写法：`$${args.参数名}`
- 值只有引用时保留类型：`'${args.ipv6_enabled}'` → 布尔
- 与文字混排时变字符串：`'节点 ${args.region}'`

> Stash 原生支持参数，**不需要**把参数硬编码进转换产物。

## 排查顺序（遇到"覆写加了就坏"时）

1. 先看有没有报错弹窗，报错会直接指出段名和语法
2. 没报错但功能失效 → 二分：只换 rules 测一次，只换 http 测一次
3. 用一份**已验证可用**的同功能覆写作基线，一次只改一处
4. 所有语法先回文档确认，不要从 Loon/Surge/Clash 推断
