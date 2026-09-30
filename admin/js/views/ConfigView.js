(() => {
  /**
   * 文件职责：配置页视图容器，负责承载配置页头部结构并挂载 ConfigRenderer。
   */

  const {
    Box,
    Typography
  } = MaterialUI;
  function ConfigView() {
    return (
      /*#__PURE__*/
      // 该视图本身只负责提供页面容器与标题，真正的配置编辑逻辑全部下沉到 ConfigRenderer。
      React.createElement(Box, {
        sx: {
          height: '100%'
        }
      }, /*#__PURE__*/React.createElement("div", {
        className: "card config-card"
      }, /*#__PURE__*/React.createElement("div", {
        className: "config-header"
      }, /*#__PURE__*/React.createElement("div", {
        style: {
          display: 'flex',
          alignItems: 'center',
          gap: '12px'
        }
      }, /*#__PURE__*/React.createElement("div", {
        style: {
          width: '4px',
          height: '24px',
          background: 'var(--md-sys-color-primary)',
          borderRadius: '2px'
        }
      }), /*#__PURE__*/React.createElement(Typography, {
        variant: "h6",
        sx: {
          fontWeight: 800,
          letterSpacing: '-0.5px'
        }
      }, "\u914D\u7F6E\u7BA1\u7406"))), /*#__PURE__*/React.createElement(ConfigRenderer, null)))
    );
  }

  // 暴露为全局视图组件，供应用入口按 currentView 渲染。
  window.ConfigView = ConfigView;
})();