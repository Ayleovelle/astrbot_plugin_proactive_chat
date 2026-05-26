/**
 * HTTP 工具模块
 * 通信策略与 Sylanne 一致：
 * 1. 优先使用 AstrBotPluginPage bridge (apiGet/apiPost)
 * 2. bridge 不可用或失败时，fallback 到 fetch(apiPath(url))
 */
(function () {
    var PLUGIN_NAME = "astrbot_plugin_proactive_chat";
    var lastTransportMode = "http";

    // 根据当前页面 URL 推导 API 基础路径（与 Sylanne 相同逻辑）。
    function apiPath(path) {
        var pathname = window.location.pathname.replace(/\/+$/, '');
        // /api/plug/<plugin>/webui 或 /api/plug/<plugin>/dashboard
        var plugMatch = pathname.match(/^\/api\/plug\/([^/]+)\/(?:webui|dashboard)/);
        if (plugMatch) return '/api/plug/' + plugMatch[1] + path;
        // /api/plugin/page/<plugin>/dashboard
        if (pathname.startsWith('/api/plugin/page/')) return '/api/plug/' + PLUGIN_NAME + path;
        // /<plugin>/pages/...
        var pageMatch = pathname.match(/^(\/[^/]+)\/pages\//);
        if (pageMatch) return '/api/plug/' + pageMatch[1].replace(/^\/+/, '') + path;
        // /<plugin>/webui 或 /<plugin>/dashboard
        var routeMatch = pathname.match(/^(\/[^/]+)\/(?:webui|dashboard)/);
        var base = routeMatch ? '/api/plug/' + routeMatch[1].replace(/^\/+/, '') : '';
        return base + path;
    }

    function splitApiPath(path) {
        var parts = String(path || '').split('?');
        var endpoint = parts[0].replace(/^\/+/, '');
        var params = {};
        if (parts[1]) new URLSearchParams(parts[1]).forEach(function(v, k) { params[k] = v; });
        return { endpoint: endpoint, params: params };
    }

    // Bridge 等待逻辑（与 Sylanne 一致）。
    var pluginBridge = null;

    // iframe sandbox 模式下 bridge SDK 在页面脚本之后注入，需要更长等待时间。
    function isInIframe() {
        try { return window.self !== window.top; } catch (e) { return true; }
    }

    function sleep(ms) {
        return new Promise(function(r) { setTimeout(r, ms); });
    }

    async function waitForPluginBridge(timeoutMs) {
        // iframe 模式下 1.4MB 文件解析完毕后 bridge SDK 才注入，给足 10 秒。
        var defaultTimeout = isInIframe() ? 10000 : 2000;
        var deadline = Date.now() + (timeoutMs || defaultTimeout);
        while (Date.now() < deadline) {
            var b = window.AstrBotPluginPage;
            if (b && typeof b.apiGet === 'function' && typeof b.apiPost === 'function') {
                return b;
            }
            await sleep(60);
        }
        return null;
    }

    async function getPluginBridge() {
        var bridge = pluginBridge || await waitForPluginBridge();
        if (!bridge) return null;
        if (pluginBridge !== bridge) pluginBridge = bridge;
        if (typeof pluginBridge.ready === 'function') {
            try {
                await Promise.race([
                    pluginBridge.ready(),
                    new Promise(function(r) { setTimeout(function() { r(null); }, 1500); })
                ]);
            } catch (e) { /* ignore */ }
        }
        return pluginBridge;
    }

    // 统一请求函数：bridge 优先；iframe 模式下 fetch 不可用（CORS origin null），
    // 因此 iframe 内只走 bridge 并带重试，不 fallback 到 fetch。
    var BRIDGE_RETRY_COUNT = 3;
    var BRIDGE_RETRY_DELAY_MS = 1500;

    async function request(url, options) {
        var method = (options && options.method || 'GET').toUpperCase();
        var body = options && options.body ? JSON.parse(options.body) : {};
        var inIframe = isInIframe();

        // 尝试 bridge 通信（带重试）。
        var maxAttempts = inIframe ? BRIDGE_RETRY_COUNT : 1;
        for (var attempt = 0; attempt < maxAttempts; attempt++) {
            if (attempt > 0) {
                // 重试前等待一段时间，让 bridge SDK 有机会注入。
                await sleep(BRIDGE_RETRY_DELAY_MS);
            }
            // 重试时重新等待 bridge（可能第一次超时了但后续注入了）。
            pluginBridge = null;
            var bridge = await getPluginBridge();
            if (bridge) {
                var info = splitApiPath(url);
                try {
                    var data = (method === 'POST' || method === 'DELETE')
                        ? await bridge.apiPost(info.endpoint, body)
                        : await bridge.apiGet(info.endpoint, info.params);
                    lastTransportMode = 'bridge';
                    return data;
                } catch (e) {
                    console.warn('[主动消息] bridge 请求失败 (attempt ' + (attempt + 1) + '):', url, e);
                }
            } else if (inIframe) {
                console.warn('[主动消息] bridge 未就绪 (attempt ' + (attempt + 1) + '/' + maxAttempts + ')，等待重试...');
            }
        }

        // iframe 模式下 fetch 永远被 CORS 阻止，直接报错而非发起注定失败的请求。
        if (inIframe) {
            throw new Error('Bridge 不可用，iframe 模式下无法通信。请刷新页面重试。');
        }

        // 独立模式 Fallback：直接 fetch 同服务器路径。
        lastTransportMode = 'http';
        var headers = { 'Content-Type': 'application/json' };
        // 独立模式下附加 token。
        if (window.AuthUtil && typeof window.AuthUtil.withAuthHeaders === 'function') {
            headers = window.AuthUtil.withAuthHeaders(headers);
        }

        var fetchOpts = { method: method, headers: headers };
        if (method === 'POST' || method === 'DELETE') {
            fetchOpts.body = options && options.body ? options.body : '{}';
        }

        var fullUrl = apiPath(url);
        var response = await fetch(fullUrl, fetchOpts);
        var payload = null;
        try { payload = await response.json(); } catch (e) { payload = null; }

        if (!response.ok) {
            var message = payload && payload.error ? payload.error : '请求失败';
            throw new Error(message);
        }
        return payload;
    }

    window.HttpUtil = {
        get: function (url) { return request(url, { method: 'GET' }); },
        post: function (url, body) {
            return request(url, { method: 'POST', body: JSON.stringify(body || {}) });
        },
        del: function (url) { return request(url, { method: 'DELETE' }); },
        getTransportMode: function () { return lastTransportMode; }
    };
})();
