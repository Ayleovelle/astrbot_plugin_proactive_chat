(function () {
    // 检测是否在 iframe 中运行（AstrBot Dashboard 内嵌）。
    var inIframe = false;
    try { inIframe = window.self !== window.top; } catch (e) { inIframe = true; }

    console.log('[主动消息 boot] v2 | inIframe=' + inIframe);

    if (inIframe) {
        // iframe 模式：跳过独立认证流程，直接放行。
        // Bridge 的 API 通信由 HttpUtil 在实际请求时处理（延迟检测）。
        console.log('[主动消息 boot] iframe 模式，跳过认证');
        window.__PROACTIVE_AUTH_PENDING = false;
        window.dispatchEvent(new Event('auth-ready'));
        var skeleton = document.getElementById('loading-skeleton');
        if (skeleton) {
            skeleton.classList.add('is-exiting');
            setTimeout(function() { if (skeleton.parentNode) skeleton.parentNode.removeChild(skeleton); }, 400);
        }
        return;
    }

    // 独立模式：执行完整的认证流程。
    window.__PROACTIVE_AUTH_PENDING = true;

    var bootLoginEventsBound = false;

    function proceed() {
        window.__PROACTIVE_AUTH_PENDING = false;
        window.dispatchEvent(new Event('auth-ready'));
        setTimeout(function() {
            var skeleton = document.getElementById('loading-skeleton');
            if (skeleton) {
                skeleton.classList.add('is-exiting');
                setTimeout(function() {
                    if (skeleton.parentNode) skeleton.parentNode.removeChild(skeleton);
                }, 300);
            }
        }, 500);
    }

    function showError(msg) {
        var text = document.getElementById('boot-text');
        if (text) { text.textContent = msg; text.style.color = '#B3261E'; }
    }

    function showInfo(msg) {
        var text = document.getElementById('boot-text');
        if (text) { text.textContent = msg; text.style.color = '#D05F00'; }
    }

    function setLoginBusy(busy) {
        var button = document.getElementById('boot-login-button');
        var input = document.getElementById('boot-password');
        if (button) {
            button.disabled = !!busy;
            button.style.opacity = busy ? '0.72' : '1';
            button.textContent = busy ? '登录中...' : '登录进入';
        }
        if (input) { input.disabled = !!busy; }
    }

    function showLoginForm() {
        // 展示登录表单，隐藏进度条。
        var loginBox = document.getElementById('boot-login');
        if (loginBox) { loginBox.style.display = 'block'; }
        var progress = document.querySelector('.boot-loader__progress');
        if (progress) { progress.style.display = 'none'; }

        if (bootLoginEventsBound) return;
        bootLoginEventsBound = true;

        var button = document.getElementById('boot-login-button');
        var input = document.getElementById('boot-password');

        function submitLogin() {
            var password = (input && input.value) ? input.value.trim() : '';
            if (!password) { showError('请输入密码'); return; }
            setLoginBusy(true);

            fetch('/api/login', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ password: password })
            })
            .then(function (res) { return res.json(); })
            .then(function (data) {
                if (data && data.token) {
                    // 登录成功，保存 token 并放行。
                    try { localStorage.setItem('proactive_token', data.token); } catch(e) {}
                    proceed();
                } else {
                    showError(data.error || '密码错误');
                    setLoginBusy(false);
                }
            })
            .catch(function (err) {
                showError('登录请求失败: ' + (err.message || err));
                setLoginBusy(false);
            });
        }

        if (button) { button.addEventListener('click', submitLogin); }
        if (input) {
            input.addEventListener('keydown', function (e) {
                if (e.key === 'Enter') { submitLogin(); }
            });
        }
    }

    function verifyExistingToken(token) {
        // 验证本地缓存的 token 是否仍然有效。
        fetch('/api/verify-token', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': 'Bearer ' + token
            }
        })
        .then(function (res) { return res.json(); })
        .then(function (data) {
            if (data && data.valid) {
                proceed();
            } else {
                // token 失效，清除并展示登录表单。
                try { localStorage.removeItem('proactive_token'); } catch(e) {}
                showInfo('会话已过期，请重新登录');
                showLoginForm();
            }
        })
        .catch(function () {
            // 网络异常时仍展示登录表单，让用户手动重试。
            try { localStorage.removeItem('proactive_token'); } catch(e) {}
            showInfo('无法验证登录状态');
            showLoginForm();
        });
    }

    // 独立模式启动：先检查后端是否需要认证。
    fetch('/api/auth-info')
        .then(function (res) { return res.json(); })
        .then(function (data) {
            if (data && data.auth_required === false) {
                // 后端未启用密码保护，直接放行。
                proceed();
                return;
            }
            // 需要认证：检查本地是否已有有效 token。
            var existingToken = null;
            try { existingToken = localStorage.getItem('proactive_token'); } catch(e) {}
            if (existingToken) {
                verifyExistingToken(existingToken);
            } else {
                showInfo('请登录以继续');
                showLoginForm();
            }
        })
        .catch(function (err) {
            showError('无法连接后端服务: ' + (err.message || err));
        });
})();
