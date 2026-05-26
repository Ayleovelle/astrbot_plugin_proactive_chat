/**
 * 文件职责：安全存储垫片。
 * AstrBot Dashboard iframe 的 sandbox 不含 allow-same-origin，
 * 导致 localStorage 访问抛出 SecurityError。
 * 本脚本在所有业务代码之前加载，检测 localStorage 可用性，
 * 若不可用则用内存 Map 替代，保证后续代码无需逐处 try/catch。
 */
(function () {
    try {
        var testKey = '__storage_test__';
        localStorage.setItem(testKey, '1');
        localStorage.removeItem(testKey);
    } catch (e) {
        // localStorage 不可用，提供内存降级实现。
        var memStore = {};
        var MemoryStorage = {
            getItem: function (key) {
                return Object.prototype.hasOwnProperty.call(memStore, key) ? memStore[key] : null;
            },
            setItem: function (key, value) {
                memStore[key] = String(value);
            },
            removeItem: function (key) {
                delete memStore[key];
            },
            clear: function () {
                memStore = {};
            },
            get length() {
                return Object.keys(memStore).length;
            },
            key: function (index) {
                return Object.keys(memStore)[index] || null;
            }
        };

        try {
            Object.defineProperty(window, 'localStorage', {
                value: MemoryStorage,
                writable: false,
                configurable: true,
            });
        } catch (defineErr) {
            // 某些环境下 defineProperty 也可能失败，最后兜底。
            window.localStorage = MemoryStorage;
        }
    }
})();
