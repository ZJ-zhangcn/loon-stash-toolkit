# Loon + Stash Toolkit

个人 Loon 插件、Stash 覆写与双向转换工具仓库。

## Loon 插件

订阅地址统一为：

```text
https://raw.githubusercontent.com/ZJ-zhangcn/loon-stash-toolkit/main/plugins/<文件名>
```

| 插件 | 文件 | 作用 |
| --- | --- | --- |
| 12306 广告域名屏蔽 | `12306-ad-domain.lpx` | 屏蔽 `ad.12306.cn` |
| 发现精彩广告拦截 | `cgb-life-startup-ad-test.lpx` | 拦截开屏素材与首页浮窗广告 |
| 招商银行开屏广告 | `cmb-startup-ad.lpx` | 移除开屏广告配置并屏蔽开屏素材 |
| 盒马开屏广告 | `freshippo-splash.lpx` | 仅处理盒马开屏响应，避免误伤首页 |
| YouTube 去广告 | `YouTube_remove_ads.lpx` | 按 protobuf 结构清理推荐流广告，并拦截 pagead、activeview、aclk |

## Stash 覆写

覆写文件位于 `stash/`，订阅地址统一为：

```text
https://raw.githubusercontent.com/ZJ-zhangcn/loon-stash-toolkit/main/stash/<文件名>
```

以下文件由本仓库的 Loon 插件转换生成：

- `12306-ad-domain.stoverride`
- `cmb-startup-ad.stoverride`
- `freshippo-splash.stoverride`
- `cgb-life-startup-ad-test.stoverride`

`bilibili-ios-ads.stoverride` 根据 iOS 哔哩哔哩抓包单独整理：

- `app.bilibili.com/x/resource/show/tab/v2`：隐藏底部栏的发布按钮与会员购入口
- `app.bilibili.com/x/v2/feed/index`：删除 `cm_v2`、`ad_info.is_ad` 和 `nature_ad=1` 卡片
- `app.bilibili.com/x/v2/splash/list`：清空开屏广告列表
- `app.bilibili.com/x/v2/splash/show`：清空实际展示项
- `app.bilibili.com/x/v2/splash/brand/list`：清空启动品牌素材

`YouTube_remove_ads.stoverride` 根据关闭去广告覆写后的 iOS 抓包重建：

- `youtubei.googleapis.com/youtubei/v1/browse|next|search`：解析 `richItemContents` 的 protobuf 字段类型，删除广告项并缓存已识别的字段号，覆盖首次加载和后续延迟插入的推荐流广告
- `youtubei.googleapis.com/youtubei/v1/player|reel/reel_watch_sequence|guide|account/get_setting|get_watch|log_event|config`：清理播放器广告字段、短视频广告和 UMP 配置
- `www.youtube.com/pagead`、`pcs/activeview`：拦截广告展示与可见性上报
- `www.google.com/aclk`、`www.googleadservices.com/pagead/aclk`、`www.google.com/ads/on-device/conversions`：拦截广告点击与设备端转化上报

该方案处理平台推荐流和请求链路广告，不能去除视频创作者口播或视频内赞助片段。

其余覆写已从 `clash-rules/rules/stash` 迁移，完整源映射见 `sources/loon-plugins.json`。

## 双向转换

转换器位于 `tools/convert.py`，只依赖 Python 3 标准库。

Loon 转 Stash：

```bash
python3 tools/convert.py loon-to-stash plugins/cmb-startup-ad.lpx -o stash/cmb-startup-ad.stoverride
```

Stash 转 Loon：

```bash
python3 tools/convert.py stash-to-loon stash/freshippo-splash.stoverride -o plugins/freshippo-splash.lpx
```

批量转换：

```bash
python3 tools/convert.py loon-to-stash --input-dir plugins --output-dir build/stash
python3 tools/convert.py stash-to-loon --input-dir stash --output-dir build/loon
```

当前支持并转换以下内容：

- Loon `#!` 元数据与 Stash `name/desc/author/homepage/icon/date/version`
- `[Rule]` 与 `rules`
- `[URL Rewrite]`、`[Header Rewrite]`、`[Body Rewrite]`
- `[Script]`、HTTP script 与 `script-providers`
- `[MITM]` 与 `http.mitm`

Stash 的 `mock` 没有无损的 Loon 对应语法，转换为 Loon 时会跳过并输出警告；其他未知语法同样会告警。转换后建议先用对应客户端做一次配置校验。

## 验证

```bash
python3 -m unittest discover -s tests -v
```
