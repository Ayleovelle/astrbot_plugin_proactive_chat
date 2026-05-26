(() => {
  /**
   * 文件职责：顶部栏组件，负责标题展示、时钟显示、连接状态指示与主题切换入口。
   */

  const {
    Box,
    Typography,
    IconButton
  } = MaterialUI;
  const {
    useState,
    useEffect
  } = React;
  function RealTimeClock({
    timeZone
  }) {
    const [timeStr, setTimeStr] = useState('');
    useEffect(() => {
      const updateTime = () => {
        // 头部时钟始终使用统一的格式化工具，确保与状态页、任务页时间显示风格一致。
        setTimeStr(formatDateTime(new Date(), timeZone || 'Asia/Shanghai', {
          includeYear: true,
          includeSeconds: true
        }));
      };
      updateTime();
      // 每秒刷新一次时钟文本；组件卸载时清理定时器，避免后台泄漏。
      const timer = setInterval(updateTime, 1000);
      return () => clearInterval(timer);
    }, [timeZone]);

    // 初始尚未生成时间字符串时先不渲染，避免短暂出现占位空壳。
    if (!timeStr) return null;
    return /*#__PURE__*/React.createElement("div", {
      className: "header-clock-chip"
    }, /*#__PURE__*/React.createElement("span", {
      className: "header-clock-label"
    }, "\u5F53\u524D\u65F6\u95F4 \uD83D\uDD52"), /*#__PURE__*/React.createElement("span", {
      className: "header-clock-value"
    }, timeStr));
  }
  function Header({
    currentView
  }) {
    const {
      state,
      dispatch
    } = useAppContext();
    const {
      config,
      status
    } = state;
    // 若配置中未单独指定展示时区，则默认按插件主要使用场景的东八区展示。
    const displayTimezone = config?.displayTimezone || 'Asia/Shanghai';
    const toggleTheme = () => {
      dispatch({
        type: 'TOGGLE_THEME'
      });
    };

    // 视图 key 到标题文案的映射集中维护，避免 JSX 中散落条件判断。
    const viewTitles = {
      status: '运行状态',
      tasks: '任务管理',
      notifications: '通知中心',
      docs: '文档浏览',
      config: '配置管理'
    };

    // Header 不直接感知底层 socket 实例，只消费后端状态中的连接计数结果。
    const wsCount = Number(status?.ws_connections ?? 0);
    const wsConnected = wsCount > 0;
    // Bridge 模式下无 WebSocket，连接状态由 API 可达性决定。
    const isBridge = !!(window.AstrBotPluginPage && typeof window.AstrBotPluginPage.apiGet === "function");
    const showConnected = isBridge ? !!status : wsConnected;
    const connectionLabel = isBridge ? status ? 'Bridge 已连接' : '未连接' : wsConnected ? '已连接' : '未连接';
    return /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("div", {
      className: "top-bar"
    }, /*#__PURE__*/React.createElement(Typography, {
      variant: "h5",
      sx: {
        fontWeight: 800,
        color: 'text.primary',
        letterSpacing: '-0.5px'
      }
    }, viewTitles[currentView] || viewTitles.status), /*#__PURE__*/React.createElement(Box, {
      sx: {
        display: 'flex',
        alignItems: 'center',
        gap: 2,
        flexWrap: 'wrap',
        justifyContent: 'flex-end'
      }
    }, /*#__PURE__*/React.createElement(RealTimeClock, {
      timeZone: displayTimezone
    }), /*#__PURE__*/React.createElement("div", {
      className: `connection-chip ${showConnected ? 'is-connected' : 'is-disconnected'}`
    }, /*#__PURE__*/React.createElement("div", {
      className: "connection-chip-dot"
    }), /*#__PURE__*/React.createElement(Typography, {
      variant: "body2",
      className: "connection-chip-text"
    }, connectionLabel)), /*#__PURE__*/React.createElement(IconButton, {
      onClick: toggleTheme,
      sx: {
        width: 44,
        height: 44,
        background: 'var(--md-sys-color-surface)',
        border: '1px solid var(--glass-border)',
        boxShadow: '0 2px 8px rgba(0,0,0,0.05)',
        '&:hover': {
          background: 'var(--md-sys-color-surface-variant)'
        }
      }
    }, /*#__PURE__*/React.createElement("span", {
      style: {
        fontSize: '18px'
      }
    }, state.theme === 'dark' ? '🌞' : '🌙')))));
  }

  // 暴露到全局，供入口应用直接渲染 Header。
  window.Header = Header;
})();