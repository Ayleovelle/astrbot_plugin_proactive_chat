/**
 * 文件职责：WebSocket Hook，负责全局单例连接管理与实时消息分发。
 * Bridge 模式（AstrBot Dashboard iframe）下 WebSocket 不可用，自动降级为轮询。
 */

function __isBridgeMode() {
    // iframe 模式下 WebSocket 不可用（sandbox 限制），直接判定为 bridge 模式跳过。
    try { if (window.self !== window.top) return true; } catch (e) { return true; }
    var b = window.AstrBotPluginPage;
    return b && (typeof b.fetch === "function" || typeof b.apiGet === "function");
}

// 管理端全局只维护一个 WebSocket 连接，避免多个组件重复建立相同连接。
let __globalWs = null;
// 所有订阅实时数据的回调统一挂到一个监听集合中，由单连接分发。
let __wsListeners = new Set();
// 重连定时器与退避参数：插件重载期间避免高频重连打满日志。
let __reconnectTimer = null;
const WS_RECONNECT_BASE_DELAY_MS = 1000;
const WS_RECONNECT_MAX_DELAY_MS = 10000;
let __nextReconnectDelayMs = WS_RECONNECT_BASE_DELAY_MS;

// Bridge 模式下的轮询降级参数。
let __pollingTimer = null;
const WS_POLLING_INTERVAL_MS = 5000;

function clearReconnectTimer() {
    if (!__reconnectTimer) return;
    clearTimeout(__reconnectTimer);
    __reconnectTimer = null;
}

function resetReconnectBackoff() {
    __nextReconnectDelayMs = WS_RECONNECT_BASE_DELAY_MS;
    clearReconnectTimer();
}

function scheduleReconnect() {
    // 没有任何订阅者时不需要重连，避免后台空转。
    if (__wsListeners.size === 0) return;
    // 已有重连任务时直接复用，防止重复排队。
    if (__reconnectTimer) return;

    const delay = __nextReconnectDelayMs;
    __nextReconnectDelayMs = Math.min(__nextReconnectDelayMs * 2, WS_RECONNECT_MAX_DELAY_MS);

    __reconnectTimer = setTimeout(() => {
        __reconnectTimer = null;
        ensureGlobalWs();
    }, delay);
}

// Bridge 模式降级轮询：定时拉取 status 接口模拟实时更新。
function startPolling() {
    if (__pollingTimer) return;
    async function poll() {
        if (__wsListeners.size === 0) {
            stopPolling();
            return;
        }
        try {
            const data = await window.HttpUtil.get('/api/status');
            if (data && !data.error) {
                __wsListeners.forEach((listener) => {
                    try { listener(data); } catch (e) {}
                });
            }
        } catch (e) {}
        __pollingTimer = setTimeout(poll, WS_POLLING_INTERVAL_MS);
    }
    poll();
}

function stopPolling() {
    if (__pollingTimer) {
        clearTimeout(__pollingTimer);
        __pollingTimer = null;
    }
}

function ensureGlobalWs() {
    // Bridge 模式下 iframe sandbox 禁止 WebSocket，且 app.jsx 已有独立轮询，无需额外降级。
    if (__isBridgeMode()) {
        return;
    }

    // 若连接已存在且仍处于"连接中 / 已连接"状态，则直接复用。
    if (__globalWs && (__globalWs.readyState === WebSocket.OPEN || __globalWs.readyState === WebSocket.CONNECTING)) {
        return;
    }

    // 与 HTTP 保持相同的 token 来源，统一从 AuthUtil 中读取。
    const token = window.AuthUtil.getToken();
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    // 开启鉴权时通过 query 传 token；无鉴权或 no-auth 场景则不附加参数。
    const tokenQuery = token && token !== 'no-auth' ? `?token=${encodeURIComponent(token)}` : '';
    const ws = new WebSocket(`${protocol}//${window.location.host}/ws${tokenQuery}`);
    __globalWs = ws;

    ws.onopen = function () {
        // 连接恢复后重置退避，后续若再次断开可从短延迟开始。
        if (__globalWs === ws) {
            resetReconnectBackoff();
        }
    };

    ws.onmessage = function (event) {
        try {
            const msg = JSON.parse(event.data);
            // 仅处理后端定义的两类数据推送消息，其余消息类型忽略。
            if (msg.type === 'full_update' || msg.type === 'update') {
                const data = msg.data || {};
                __wsListeners.forEach((listener) => {
                    try { listener(data); } catch (e) {}
                });
            }
        } catch (e) {}
    };

    ws.onerror = function () {
        try { ws.close(); } catch (e) {}
    };

    ws.onclose = function () {
        if (__globalWs !== ws) return;
        __globalWs = null;
        // 连接关闭后自动尝试重连，覆盖插件重载等短暂不可用场景。
        scheduleReconnect();
    };
}

function useWebSocket(onData) {
    React.useEffect(() => {
        if (typeof onData === 'function') {
            __wsListeners.add(onData);
        }

        // 只要有任意一个订阅者存在，就确保全局连接（或轮询）已建立。
        ensureGlobalWs();

        return () => {
            if (typeof onData === 'function') {
                __wsListeners.delete(onData);
            }
            // 最后一个订阅者离开时主动关闭连接/轮询，减少空闲资源占用。
            if (__wsListeners.size === 0) {
                clearReconnectTimer();
                stopPolling();
                __nextReconnectDelayMs = WS_RECONNECT_BASE_DELAY_MS;
                if (__globalWs) {
                    try { __globalWs.close(); } catch (e) {}
                }
                __globalWs = null;
            }
        };
    }, [onData]);
}

// 暴露为全局 Hook，供入口应用与各页面脚本直接调用。
window.useWebSocket = useWebSocket;

