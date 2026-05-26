/**
 * 认证工具模块（pages bridge 模式下跳过认证）
 */
(function () {
    const TOKEN_KEY = 'proactive_token';

    function isBridgeMode() {
        // 与 Sylanne 一致：检查 bridge 对象是否已注入。
        var b = window.AstrBotPluginPage;
        if (b && (typeof b.apiGet === "function" || typeof b.apiPost === "function")) return true;
        // iframe 内也视为 bridge 模式（bridge 可能尚未注入但即将到来）。
        try { if (window.self !== window.top) return true; } catch (e) { return true; }
        return false;
    }

    window.AuthUtil = {
        getToken: function () {
            if (isBridgeMode()) return 'bridge-mode';
            try { return localStorage.getItem(TOKEN_KEY); } catch (e) { return null; }
        },
        setToken: function (token) {
            if (isBridgeMode()) return;
            try { localStorage.setItem(TOKEN_KEY, token); } catch (e) {}
        },
        clearToken: function () {
            if (isBridgeMode()) return;
            try { localStorage.removeItem(TOKEN_KEY); } catch (e) {}
        },
        withAuthHeaders: function (headers) {
            if (isBridgeMode()) return Object.assign({}, headers || {});
            const token = window.AuthUtil.getToken();
            const base = Object.assign({}, headers || {});
            if (token && token !== 'no-auth') {
                base.Authorization = 'Bearer ' + token;
            }
            return base;
        }
    };
})();
