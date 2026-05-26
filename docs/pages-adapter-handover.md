# Pages 适配层交接文档

## 背景

将 `astrbot_plugin_proactive_chat` 的 WebUI 管理端适配到 AstrBot Dashboard 的 "pages" 机制中，使其能在 AstrBot 主界面的插件页面 iframe 内正常运行。

---

## 架构概览

```
独立模式 (端口 4100)          Pages 模式 (AstrBot Dashboard iframe)
┌─────────────────┐           ┌──────────────────────────────────┐
│  浏览器直接访问  │           │  AstrBot Dashboard               │
│  localhost:4100  │           │  ┌────────────────────────────┐  │
│                  │           │  │ iframe (sandbox)           │  │
│  fetch → 4100   │           │  │  bridge-sdk.js → postMsg   │  │
│  WebSocket → ws │           │  │  window.AstrBotPluginPage  │  │
└─────────────────┘           │  └────────────────────────────┘  │
        │                     └──────────────────────────────────┘
        ▼                                    │
┌─────────────────┐                          ▼
│ web_admin_server │           ┌──────────────────────────────────┐
│ (FastAPI/Uvicorn)│           │ pages_adapter.py                 │
│ 认证中间件       │           │ (register_web_api → AstrBot路由) │
└─────────────────┘           └──────────────────────────────────┘
```

### 两种模式共存

| 维度 | 独立模式 | Pages 模式 |
|------|----------|------------|
| 入口 | `localhost:4100` | AstrBot Dashboard → 插件页面 |
| 通信 | fetch + WebSocket | `window.AstrBotPluginPage` bridge |
| 认证 | 自有密码 + JWT token | AstrBot Dashboard 统一认证 |
| 实时更新 | WebSocket 推送 | 轮询 (bridge 不支持 WS) |
| 前端文件 | 同一个 `index.html` | 同一个 `index.html` |

---

## 为什么 index.html 是 1.4MB 的单文件

### iframe sandbox 的限制

AstrBot 的 pages iframe 使用 `sandbox="allow-scripts allow-forms allow-downloads"`，**没有 `allow-same-origin`**。这意味着：

1. **iframe 的 origin 是 `null`（不透明源）**
2. 所有 `fetch()` / `XHR` 请求都会被 CORS 阻止（origin: null 不被任何服务器接受）
3. 外部 `<link>` / `<script src="...">` 引用**同域资源可以加载**（浏览器对 script/link 标签的 CORS 策略比 fetch 宽松），但 `@font-face` 的 `url()` 引用会被阻止
4. `localStorage` 不可用（无 origin → 无存储分区）

### 因此必须内联的资源

- **字体文件** (5 个 Outfit 字重 ≈ 235KB)：`@font-face` 的 `url()` 在 opaque origin 下加载失败，必须 base64 内联
- **CSS**：依赖字体的 `@font-face` 声明，一起内联
- **Vendor JS** (React, ReactDOM, MUI, Emotion, Marked, DOMPurify ≈ 780KB)：虽然 `<script src>` 理论上可以加载同域文件，但 AstrBot 的 pages 静态文件服务对多文件请求不稳定（曾导致页面卡在"正在初始化"），单文件更可靠
- **应用 JS** (≈ 180KB)：同上

### 唯一的外部引用

```html
<script src="/api/plugin/page/bridge-sdk.js" onerror=""></script>
```

这是 AstrBot 提供的 bridge SDK，必须从服务器加载（它建立与父窗口的 postMessage 通道）。独立模式下 404 不影响功能。

---

## 为什么包体是 2MB

```
astrbot_plugin_proactive_chat.zip (2.0 MB) 构成：
├── pages/dashboard/index.html      1,410 KB  ← 单文件 bundled 前端
├── pages/dashboard/vendor/         ≈ 760 KB  ← 源文件（打包脚本需要）
│   ├── mermaid.min.js              2,512 KB  ← 最大单项，但未内联到 index.html
│   ├── material-ui.min.js            553 KB
│   ├── react-dom.min.js              129 KB
│   └── ...
├── pages/dashboard/fonts/          ≈ 234 KB  ← 源 TTF（打包脚本需要）
├── core/*.py                       ≈ 200 KB  ← 后端逻辑
├── docs/                           ≈  50 KB
└── 其他 (README, assets, utils)    ≈ 100 KB
```

**主要体积来源：**
1. `mermaid.min.js` (2.5MB) — 未内联到 index.html（太大），但保留在包里供独立模式 lazy-load
2. `index.html` (1.4MB) — 内联了除 mermaid 外的所有前端资源
3. vendor 源文件 + fonts — 打包脚本的输入，保留以便后续修改后重新 bundle

如果要减小包体：
- 移除 `pages/dashboard/vendor/`、`pages/dashboard/fonts/`、`pages/dashboard/js/`、`pages/dashboard/css/` 目录（只保留 `index.html` + `logo.png`），可以减到 ≈ 600KB
- 但这样就无法在本地重新 bundle，需要权衡

---

## 原版 WebUI 国内节点难以打开的原因及解决办法

### 原因

原版 WebUI 的 `index.html` 通过 CDN 引用外部依赖：

```html
<!-- 原版写法（示意） -->
<script src="https://unpkg.com/react@18/umd/react.production.min.js"></script>
<script src="https://unpkg.com/react-dom@18/umd/react-dom.production.min.js"></script>
<script src="https://unpkg.com/@mui/material@5/umd/material-ui.production.min.js"></script>
<link href="https://fonts.googleapis.com/css2?family=Outfit:wght@400;700&display=swap">
```

这些 CDN 域名在国内网络环境下：
- `unpkg.com` — 部分地区/运营商 DNS 污染或连接超时
- `fonts.googleapis.com` / `fonts.gstatic.com` — 被墙或极慢
- `cdn.jsdelivr.net` — 时好时坏，高峰期超时

任何一个资源加载失败，整个页面就白屏或功能异常。

### 解决办法对比

| 方案 | 优点 | 缺点 |
|------|------|------|
| **本地 vendor（当前方案）** | 零外部依赖，离线可用，国内外一致 | 包体增大 |
| 换用国内 CDN 镜像 | 包体小，改动最少 | 仍依赖外部网络，镜像可能下线 |
| 用户自行配置代理 | 不改代码 | 门槛高，不现实 |
| Service Worker 缓存 | 首次加载后离线可用 | iframe sandbox 下 SW 不可用 |

### 如果不用本地 vendor：换用国内 CDN 镜像

最轻量的修法是把 CDN 源替换为国内可用的镜像，不需要本地存 vendor 文件：

```html
<!-- React -->
<script src="https://registry.npmmirror.com/react/18.2.0/files/umd/react.production.min.js"></script>
<script src="https://registry.npmmirror.com/react-dom/18.2.0/files/umd/react-dom.production.min.js"></script>

<!-- MUI (Material UI) -->
<script src="https://registry.npmmirror.com/@mui/material/5.15.0/files/umd/material-ui.production.min.js"></script>

<!-- Emotion (MUI 依赖) -->
<script src="https://registry.npmmirror.com/@emotion/react/11.11.0/files/umd/emotion-react.umd.min.js"></script>
<script src="https://registry.npmmirror.com/@emotion/styled/11.11.0/files/umd/emotion-styled.umd.min.js"></script>

<!-- Marked (Markdown 渲染) -->
<script src="https://registry.npmmirror.com/marked/9.1.0/files/marked.min.js"></script>

<!-- DOMPurify -->
<script src="https://registry.npmmirror.com/dompurify/3.0.6/files/dist/purify.min.js"></script>
```

字体方面，Google Fonts 可以换用国内镜像：

```html
<!-- 原版 -->
<link href="https://fonts.googleapis.com/css2?family=Outfit:wght@400;500;600;700;800&display=swap">

<!-- 国内镜像替代（任选其一） -->
<link href="https://fonts.loli.net/css2?family=Outfit:wght@400;500;600;700;800&display=swap">
<link href="https://fonts.font.im/css2?family=Outfit:wght@400;500;600;700;800&display=swap">
```

**可用的国内 CDN 源：**

| CDN | 域名 | 稳定性 | 备注 |
|-----|------|--------|------|
| npmmirror (淘宝) | `registry.npmmirror.com` | 高 | 阿里云支撑，npm 包直接引用 |
| BootCDN | `cdn.bootcdn.net` | 中高 | 国内老牌，偶尔更新滞后 |
| 字节 CDN | `lf3-cdn-tos.bytecdntp.com` | 高 | 字节跳动维护 |
| 七牛 staticfile | `cdn.staticfile.net` | 中 | 社区维护 |

**推荐做法：npmmirror 为主 + fallback**

```html
<script src="https://registry.npmmirror.com/react/18.2.0/files/umd/react.production.min.js"></script>
<script>window.React || document.write('<script src="https://cdn.bootcdn.net/ajax/libs/react/18.2.0/umd/react.production.min.js"><\/script>')</script>
```

这样主 CDN 挂了还有备用。包体保持最小，只需要改 HTML 里的 URL。

### 实际建议

如果目标只是"让国内用户能正常打开独立 WebUI"，**换 CDN 源是最简单的方案**：
1. 把 `index.html` 里的 unpkg/googleapis 链接替换为 npmmirror
2. 字体换用 `fonts.loli.net` 或直接内联（字体文件总共 234KB，base64 后 ≈ 312KB）
3. 不需要 bundle 成单文件，不需要本地 vendor 目录
4. 包体可以控制在几十 KB

Dashboard 集成（Pages 模式）才需要单文件 bundle，因为 iframe sandbox 的限制比普通网页严格得多。如果不需要 Dashboard 集成，独立 WebUI 换个 CDN 源就够了。

---

## 关键文件清单

| 文件 | 职责 |
|------|------|
| `pages/dashboard/index.html` | 单文件 bundled 前端（Pages + 独立模式共用） |
| `core/pages_adapter.py` | 将 web_admin_server 的 API 桥接到 AstrBot register_web_api |
| `core/web_admin_server.py` | 独立端口 (4100) 的完整 Web 服务 |
| `pages/dashboard/js/utils/http.js` | 统一通信层：bridge 优先，独立模式 fallback fetch |
| `pages/dashboard/js/boot.js` | 启动流程：iframe 跳过认证，独立模式走密码验证 |
| `pages/dashboard/js/utils/auth.js` | Token 管理（key: `proactive_token`） |
| `pages/dashboard/js/hooks/useWebSocket.js` | 实时更新：独立模式 WS，bridge 模式轮询 |
| `/tmp/bundle_dashboard.py` | 打包脚本：将所有资源合并为单 HTML |

---

## 我们用了什么办法解决问题

### 问题一：页面在 iframe 中白屏 / 卡在"正在初始化"

**根因**：原版 WebUI 使用多个独立 JS/CSS 文件，AstrBot 的 pages 静态文件服务对大量并发小文件请求处理不稳定，部分文件加载失败导致 React 无法初始化。

**解法**：编写 Python 打包脚本，将所有资源（字体 base64、CSS、vendor JS、应用 JS）合并为单个 `index.html`。AstrBot 只需 serve 一个文件，彻底消除多文件加载竞态。

### 问题二：iframe 内所有 API 请求被 CORS 阻止

**根因**：`sandbox` 没有 `allow-same-origin`，iframe origin 为 `null`，所有 `fetch()` 请求都被服务器拒绝（Origin: null 不匹配任何 CORS 策略）。

**解法**：使用 AstrBot 提供的 bridge SDK（`/api/plugin/page/bridge-sdk.js`）。Bridge 通过 `postMessage` 与父窗口通信，由父窗口代为发起 HTTP 请求，绕过 iframe 的 CORS 限制。前端统一通信层（`http.js`）在 iframe 模式下只走 bridge，不 fallback 到 fetch。

### 问题三：Bridge SDK 加载时机

**根因**：最初误以为 AstrBot 会自动注入 bridge SDK 到 iframe 中。实际上需要页面自己通过 `<script src>` 引用。

**解法**：在 bundled HTML 的应用脚本之前添加 `<script src="/api/plugin/page/bridge-sdk.js" onerror="">` 标签。`<script src>` 是同步加载的，确保 bridge 对象在应用代码执行前就已就绪。`onerror=""` 使独立模式下 404 不报错。

### 问题四：独立模式登录后仍报"登录已过期"

**根因**：`boot.js` 登录成功后将 token 存入 `localStorage['proactive_token']`，但 `auth.js` 的 `withAuthHeaders()` 从 `localStorage['proactive_admin_token']` 读取。Key 不一致导致后续请求不带 Authorization header。

**解法**：统一 token 存储 key 为 `proactive_token`。

### 问题五：国内用户打开 WebUI 白屏

**根因**：原版通过 unpkg / Google Fonts 等境外 CDN 加载依赖，国内网络环境下这些域名经常超时或被阻断。

**解法**：将所有 vendor 库下载到本地 `pages/dashboard/vendor/` 目录，字体文件 base64 内联到 CSS 中。零外部网络依赖，任何网络环境下都能正常加载。

---

## 已修复的问题

1. **Bridge SDK 未加载** — 原以为 AstrBot 自动注入，实际需要页面自己 `<script src="/api/plugin/page/bridge-sdk.js">`
2. **Bridge 时序问题** — iframe 模式下等待超时从 2s 提升到 10s，失败后重试 3 次而非 fallback 到 fetch
3. **Token key 不一致** — boot.js 存 `proactive_token`，auth.js 读 `proactive_admin_token`，已统一为 `proactive_token`
4. **Sidebar logo CORS** — 同步检查 bridge 对象改为 iframe 检测，通过 HttpUtil 的 bridge 等待逻辑获取 logo
5. **iframe 内 fetch fallback 无意义** — iframe origin 为 null，fetch 永远被 CORS 阻止，改为直接报错

---

## 重新打包流程

```bash
python /tmp/bundle_dashboard.py
# 输出: pages/dashboard/index.html (≈1410 KB)

# 如需完整 zip:
cd G:/UI_for_astrbot_plugin_proactive_chat
# 运行打包脚本（排除 .git、zip 自身）
```

打包脚本位于 `/tmp/bundle_dashboard.py`，建议移入仓库（如 `scripts/bundle.py`）以便持久保存。
