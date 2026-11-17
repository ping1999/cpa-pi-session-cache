# CPA Pi Session Cache

CPA C ABI 动态库插件。配合 [项目级 Pi 扩展](https://github.com/ping1999/pi-cpa-session-cache)，将 `X-Pi-Cache-Session` 稳定映射成 Antigravity `request.sessionId`。

不修改用户消息文本，不修改/重建 CPA 主程序，不接管 OAuth 或模型执行器。无专用 header 的请求不受干预。

## 从 GitHub 安装、更新

发布产物面向 **Linux amd64 / glibc**；不宣称支持 musl、macOS、Windows 或 arm64。C ABI v1、schema v10，已在官方 CPA **v8.0.76 / 7df5607** 验证。

在现有 `config.yaml` 的 `plugins` 下合并以下字段（不要覆盖其他插件或配置）：

```yaml
plugins:
  enabled: true
  store-sources:
    - https://raw.githubusercontent.com/ping1999/cpa-pi-session-cache/main/registry.json
  configs:
    pi-cpa-session-cache:
      enabled: true
      priority: 100
```

然后从 CPA 插件商店中选择新增来源的 **Pi Session Cache Namespace** 安装，下载 GitHub Release 的 ZIP 和 `checksums.txt`，由 CPA 验证并写入安装 manifest。之后在同一来源更新，无需自行编译。

管理 API 的等价安装操作（使用你实际的管理地址、管理密钥；不要把密钥写进仓库）：

```text
POST /v8/management/plugins/store/pi-cpa-session-cache/install
{"version":"0.1.0"}
```

不指定版本则安装最新 Release；可在商店查询时用 `?refresh=true` 刷新版本。私有 GitHub 仓库需要为 registry、GitHub API 和 Release 下载配置合适的 store auth；本 README 不建议把 token 存进 YAML。

**按此顺序启用：先安装 CPA 插件，再启用 Pi 项目扩展。**卸载反过来：先停用 Pi 扩展/header，再卸载 CPA 插件；否则退回原生上游 ID。不要在有进行中请求时热卸载插件，等待空闲后操作。

## 实现

1. `request.intercept_after`：只有目标 `antigravity` 且带专用 header 才处理；要求来源 `openai`。
2. 对 `pi-cpa-session-cache:v1:<Pi Session ID>` 做 SHA-256，取大端前 63 位（0 改为 1），生成负十进制 ID。
3. 在现有 `generationConfig` / `generation_config` 中暂存保留字段 `__pi_cpa_cache_session_v1`；CPA 原生 OpenAI→Antigravity 转换会保留它。
4. `request.normalize` 在原生转换之后消费保留字段，把 ID 写入 `request.sessionId`，删除保留字段。原生执行器优先保留已有 sessionId。

没有跨请求映射表、线程全局会话变量或生命周期依赖；不根据首条 user 文本分配 ID。哈希结果只有 63 位，存在很低但非零的碰撞概率，不是强安全隔离边界。

Header 限制为 1–128 字符 `[A-Za-z0-9._:-]`，重复/无效 header、错误 generationConfig、保留字段冲突会在转换前拒绝（HTTP 400）。不影响其他上游或无标记请求。

## 局限和故障语义

- 只支持 OpenAI Chat Completions → Antigravity；Responses、Anthropic 入口等不支持。Claude 和 Gemini 模型都走已验证的执行路径；图片/搜索/压缩/特殊路由未作为保证范围。
- **依赖 CPA 原生转换保留 generationConfig 的行为**，不是最终 HTTP transport hook。CPA 升级或其他插件改写/删除该字段，可能导致 namespace 不生效；其他插件也可能覆盖 sessionId。
- CPA 的 normalizer 错误是宿主控制的：不可承诺 OOM、插件熔断/卸载、其他 normalizer 故障时 fail-closed。请监控插件状态并在升级前跑集成测试；异常时先停用 Pi header。
- 正常路径的临时字段经测试不会发到上游，但不可承诺任意第三方插件组合或热卸载中的行为。
- 不保证 Google 实际缓存全部清空、命中率或计费结果。测试使用本地 mock，不调用 Google 生成接口。
- 不修改通用 `canonical_session_id` / 账号亲和路由；只修正选定请求的 Antigravity 上游 ID。

## 构建与测试

日常安装只需要 GitHub Release。开发者构建：

```bash
make test
python3 tests/integration.py  # Linux + Docker；官方镜像，本地 mock，无真实凭证
python3 scripts/package.py amd64
```

依赖 C11 编译器、make、Python 3；运行库仅 libc，无 OpenSSL/Go 依赖。cJSON 1.7.19 已 vendored，来源和线程局部错误状态补丁见 `vendor/README.md`。源码和 Makefile 支持本机编译；发布 CI 在 Ubuntu 22.04 构建 amd64 基线并运行测试，推送 `v*` tag 自动发布 CPA 标准命名 ZIP 和校验和。

单元测试包括 SHA-256 映射对照、消息/选项保持、非法输入、header 大小写/重复、并发 100 会话；集成测试验证 Claude/Gemini、流式/非流式、20 路并发、不带标记、改首条 user、关闭插件对照。
