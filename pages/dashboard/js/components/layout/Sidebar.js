(() => {
  /**
   * 文件职责：侧边导航组件，负责视图切换、目录快捷操作与仓库信息入口展示。
   */

  const {
    Box,
    Typography
  } = MaterialUI;
  function SidebarActionButton({
    icon,
    label,
    onClick,
    iconStyle
  }) {
    return /*#__PURE__*/React.createElement("button", {
      className: "btn sidebar-action-btn",
      onClick: onClick
    }, /*#__PURE__*/React.createElement("span", {
      style: iconStyle || {
        fontSize: '16px',
        marginLeft: '-6px'
      }
    }, icon), label);
  }
  function Sidebar({
    currentView,
    onChange
  }) {
    const {
      state
    } = useAppContext();
    // 版本号来自全局状态的 status 快照；未加载前先展示占位文本。
    const version = state.status?.version || '...';
    const unreadNotificationCount = Math.max(0, Number(state.notificationsMeta?.unread_count ?? 0));
    const [logoSrc, setLogoSrc] = React.useState('logo.png');
    React.useEffect(() => {
      // iframe sandbox 模式下无法直接 fetch 图片（CORS origin null），
      // 使用 HttpUtil（内含 bridge 等待逻辑）获取 base64 logo。
      var inIframe = false;
      try { inIframe = window.self !== window.top; } catch (e) { inIframe = true; }

      if (inIframe) {
        // Bridge 模式下 iframe sandbox 阻止直接加载图片资源，
        // 通过 API 获取 base64 data URL 绕过限制。
        (async () => {
          try {
            const res = await window.HttpUtil.get('/api/logo-data');
            if (res && res.data_url) {
              setLogoSrc(res.data_url);
            }
          } catch (e) {
            // API 不可用时保持默认相对路径，让浏览器自行尝试。
          }
        })();
        return;
      }

      // 独立模式：依次尝试多个 logo 地址以提高兼容性。
      const candidates = ['/logo.png', '../logo.png', 'logo.png'];
      let cancelled = false;
      async function resolveLogo() {
        for (const candidate of candidates) {
          try {
            const response = await fetch(candidate, {
              method: 'HEAD'
            });
            if (response.ok) {
              if (!cancelled) setLogoSrc(candidate);
              return;
            }
          } catch (e) {
            // 某个候选地址失败并不代表整体失败，继续尝试下一个即可。
          }
        }
      }
      resolveLogo();
      return () => {
        cancelled = true;
      };
    }, []);

    // 当前管理端开放的主导航项集中定义在这里，便于后续扩展新视图。
    const menus = [{
      key: 'status',
      label: '运行状态',
      icon: '📊'
    }, {
      key: 'tasks',
      label: '任务管理',
      icon: '📋'
    }, {
      key: 'notifications',
      label: '通知中心',
      icon: '🔔'
    }, {
      key: 'docs',
      label: '文档浏览',
      icon: '📚'
    }, {
      key: 'config',
      label: '配置管理',
      icon: '⚙️'
    }];
    const openDirectory = async path => {
      try {
        // 通过后端调用本机资源管理器打开目录，前端无需感知具体平台命令。
        const response = await window.HttpUtil.post('/api/open-directory', {
          path
        });
        if (!response || response.ok === false) {
          const errorTitle = response?.error || '打开目录失败';
          const errorMsg = response?.message ? `\n详情: ${response.message}` : '';
          throw new Error(`${errorTitle}${errorMsg}`);
        }
      } catch (e) {
        const message = e?.message || '打开目录失败';
        // sandbox 环境下 alert 被禁用，降级为 console 输出。
        try {
          window.alert(message);
        } catch (_) {
          console.warn('[主动消息]', message);
        }
      }
    };
    return /*#__PURE__*/React.createElement("div", {
      className: "sidebar"
    }, /*#__PURE__*/React.createElement("div", {
      className: "sidebar-header"
    }, /*#__PURE__*/React.createElement("img", {
      src: logoSrc,
      alt: "Logo",
      className: "sidebar-logo-img",
      onError: () => {
        // 图片加载失败时再尝试回退到上一级相对路径，兼容静态资源引用差异。
        if (logoSrc !== '../logo.png') setLogoSrc('../logo.png');
      }
    }), /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement(Typography, {
      variant: "h6",
      sx: {
        fontWeight: 800,
        lineHeight: 1.2,
        letterSpacing: '-0.5px'
      }
    }, "\u4E3B\u52A8\u6D88\u606F"), /*#__PURE__*/React.createElement(Typography, {
      variant: "caption",
      sx: {
        opacity: 0.6,
        display: 'block',
        mt: 0.5
      }
    }, "Admin Console"))), /*#__PURE__*/React.createElement(Box, {
      sx: {
        flex: 1,
        mt: 4
      }
    }, menus.map(item => {
      const showUnreadBadge = item.key === 'notifications' && unreadNotificationCount > 0;
      const unreadBadgeText = unreadNotificationCount > 99 ? '99+' : String(unreadNotificationCount);
      return /*#__PURE__*/React.createElement("div", {
        key: item.key,
        className: `nav-item ${currentView === item.key ? 'active' : ''}`
        // 导航只上抛目标视图 key，具体状态更新由父组件统一处理。
        ,
        onClick: () => onChange(item.key)
      }, /*#__PURE__*/React.createElement("span", {
        style: {
          fontSize: '18px'
        }
      }, item.icon), /*#__PURE__*/React.createElement(Typography, {
        variant: "body2",
        sx: {
          fontWeight: 500
        }
      }, item.label), showUnreadBadge ? /*#__PURE__*/React.createElement("span", {
        className: "sidebar-notification-badge",
        "aria-label": `未读通知 ${unreadNotificationCount} 条`
      }, unreadBadgeText) : null);
    })), /*#__PURE__*/React.createElement(Box, {
      sx: {
        px: 2,
        pt: 0,
        pb: 0
      }
    }, /*#__PURE__*/React.createElement("div", {
      className: "sidebar-actions-panel"
    }, /*#__PURE__*/React.createElement(SidebarActionButton, {
      icon: "\uD83D\uDCC2",
      label: "\u6253\u5F00\u63D2\u4EF6\u6587\u4EF6\u76EE\u5F55",
      onClick: () => openDirectory('plugin')
    }), /*#__PURE__*/React.createElement(SidebarActionButton, {
      icon: "\uD83D\uDDC3\uFE0F",
      label: "\u6253\u5F00\u63D2\u4EF6\u6570\u636E\u76EE\u5F55",
      onClick: () => openDirectory('data'),
      iconStyle: {
        fontSize: '16px',
        marginLeft: '-4px'
      }
    })), /*#__PURE__*/React.createElement("a", {
      href: "https://github.com/DBJD-CR/astrbot_plugin_proactive_chat",
      target: "_blank",
      rel: "noopener noreferrer",
      className: "github-btn sidebar-github-card",
      style: {
        textDecoration: 'none'
      }
    }, /*#__PURE__*/React.createElement("div", {
      className: "sidebar-github-inner"
    }, /*#__PURE__*/React.createElement("svg", {
      height: "28",
      width: "28",
      viewBox: "0 0 16 16"
      // GitHub 图标颜色随当前主题切换，保证深浅色模式下都具备良好对比度。
      ,
      fill: state.theme === 'dark' ? '#E6E1E5' : '#000000',
      style: {
        flexShrink: 0
      }
    }, /*#__PURE__*/React.createElement("path", {
      d: "M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z"
    })), /*#__PURE__*/React.createElement("div", {
      className: "sidebar-github-texts"
    }, /*#__PURE__*/React.createElement(Typography, {
      variant: "caption",
      className: "sidebar-github-author"
    }, "@DBJD-CR"), /*#__PURE__*/React.createElement(Typography, {
      variant: "caption",
      className: "sidebar-github-meta"
    }, "\uD83D\uDD27 (\u4E3B\u52A8\u6D88\u606F) ", version)))), /*#__PURE__*/React.createElement(Typography, {
      variant: "caption",
      sx: {
        display: 'block',
        textAlign: 'center',
        mt: 1,
        opacity: 0.7,
        fontSize: '14px',
        fontWeight: 500
      }
    }, "\u70B9\u4E2A Star \u5427~ \u2B50")));
  }

  // 暴露到全局，供入口应用直接使用侧边栏组件。
  window.Sidebar = Sidebar;
})();