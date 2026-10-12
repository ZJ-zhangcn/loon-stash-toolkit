# Loon + Stash Toolkit

收录 Kelee 的 Loon 插件转换得到的 Stash 覆写，以及双向转换工具。

## Stash 覆写

覆写位于 `stash/`，订阅地址格式：

```text
https://raw.githubusercontent.com/ZJ-zhangcn/loon-stash-toolkit/main/stash/<文件名>
```

- 24 个覆写来源于 `kelee.one`，源文件与输出映射见 `sources/loon-plugins.json`。
- `Bilibili_remove_ads.stoverride` 基于 [kokoryh/Sparkle 的 Loon 插件](https://github.com/kokoryh/Sparkle/blob/master/release/loon/plugin/bilibili.lpx)，按需固定参数：动态最常访问设为 `show`、过滤置顶评论广告、开启空降助手、关闭日志，`bsbsb.top` 走 `DIRECT`。我的页面移除会员购，底栏过滤发布（加号）入口。评论区 protobuf 请求脚本已启用；当前上游版本没有独立的请求优化开关。

## 双向转换

转换器 `tools/convert.py` 仅依赖 Python 3 标准库。

```bash
python3 tools/convert.py loon-to-stash ~/Downloads/example.lpx -o build/example.stoverride
python3 tools/convert.py stash-to-loon stash/Block_HTTPDNS.stoverride -o build/Block_HTTPDNS.lpx
python3 tools/convert.py stash-to-loon --input-dir stash --output-dir build/loon
```

支持 Loon `#!` 元数据与 Stash 元数据（包括 `tag` ↔ `category`）、规则、各类 Rewrite、脚本及 MITM。Stash 的 `mock` 没有无损的 Loon 对应语法，转换时会跳过并警告；其他未知语法也会告警。转换后建议用对应客户端校验配置。

## 验证

```bash
python3 -m unittest discover -s tests -v
```
