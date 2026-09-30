(() => {
  const {
    Alert,
    Box,
    Button,
    Chip,
    CircularProgress,
    Dialog,
    DialogActions,
    DialogContent,
    DialogTitle,
    MenuItem,
    TextField,
    Typography
  } = MaterialUI;
  const categories = {
    decision: '决策',
    generation: '生成',
    send: '发送',
    state: '状态',
    runtime: '运行'
  };
  const reasons = {
    quiet_hours: '免打扰时段',
    session_disabled: '会话已禁用',
    session_config_missing: '未配置会话',
    unanswered_limit: '达到未回复上限',
    new_user_message: '生成期间收到用户新消息',
    plugin_stopping: '插件正在停止',
    context_unavailable: '上下文不可用',
    no_response: '未返回生成结果',
    empty_result: '生成结果为空',
    decorating_hook: '被装饰钩子拦截'
  };
  const outcomes = {
    explicit_success: '接口明确返回成功，终端送达未确认',
    explicit_failure: '接口明确返回失败',
    returned_without_receipt: '调用返回，未提供送达回执',
    unknown_no_receipt: '未知：事件未返回结果',
    unknown_after_exception: '异常后状态未知，可能已投递',
    flow_returned_true: '发送流程返回 True，查看接口记录确认',
    failed_or_blocked: '发送流程失败或被拦截'
  };
  const freshFilters = () => ({
    min_level: 'INFO',
    category: '',
    session_id: '',
    trace_id: '',
    since: '',
    until: ''
  });
  const color = level => ({
    DEBUG: 'default',
    INFO: 'info',
    WARNING: 'warning',
    ERROR: 'error',
    CRITICAL: 'error'
  })[level] || 'default';
  const stamp = ts => new Date(ts * 1000).toLocaleString('zh-CN', {
    hour12: false
  });
  function detailLine(item) {
    const d = item.details,
      parts = [];
    const known = value => value === null || value === undefined ? '未知' : value;
    if (d.condition) {
      const names = {
        session_config_present: '会话配置存在',
        session_enabled: '会话启用',
        quiet_hours_active: '处于免打扰时段'
      };
      parts.push(`${names[d.condition] || d.condition}：${d.value ? '是' : '否'}`, d.allowed ? '检查通过' : '不满足触发条件');
    }
    if (d.limit !== undefined) parts.push(`未回复 ${known(d.unanswered_count)} 次 / 上限 ${d.limit === null ? '未知' : d.limit > 0 ? d.limit + ' 次' : '不限'}`);
    if (d.previous_count !== undefined) parts.push(`未回复计数 ${known(d.previous_count)} → ${known(d.unanswered_count)}`);
    if (d.source_mode) parts.push(`上下文 ${d.source_mode}`, `历史 ${d.history_count} 条`, `平台 ${d.platform_records} 条`, `注入 ${d.injected_count} 条`);
    if (d.chosen_interval_seconds !== undefined) parts.push(`候选 ${d.min_interval_seconds}–${d.max_interval_seconds} 秒`, `随机选择 ${d.chosen_interval_seconds} 秒`);
    if (d.text_length !== undefined) parts.push(`文本长度 ${d.text_length}`, `组件 ${d.component_count ?? '未知'}`);
    if (d.route) parts.push(`路径 ${d.route}`);
    if (d.duration_ms !== undefined) parts.push(`耗时 ${known(d.duration_ms)} ms`);
    return parts.join(' · ');
  }
  function paramsFor(filters, cursor, limit = 50) {
    const params = new URLSearchParams({
      limit: String(limit)
    });
    Object.entries(filters).forEach(([key, value]) => {
      if (!value) return;
      params.set(key, key === 'since' || key === 'until' ? String(new Date(value).getTime() / 1000) : value);
    });
    if (cursor) params.set('before_id', String(cursor));
    return params;
  }
  async function requestLogs(filters, cursor, limit = 50, signal) {
    const response = await fetch('/api/logs?' + paramsFor(filters, cursor, limit), {
      headers: window.AuthUtil.withAuthHeaders({}),
      signal,
      cache: 'no-store'
    });
    const result = await response.json();
    if (!response.ok) throw new Error(response.status === 401 ? '登录已过期，请刷新页面重新登录' : result.error || '日志读取失败');
    return result;
  }
  function LogsView() {
    const [draft, setDraft] = React.useState(freshFilters);
    const [filters, setFilters] = React.useState(freshFilters);
    const [cursors, setCursors] = React.useState([null]);
    const [payload, setPayload] = React.useState(null);
    const [loading, setLoading] = React.useState(false);
    const [error, setError] = React.useState('');
    const [tick, setTick] = React.useState(0);
    const [live, setLive] = React.useState(false);
    const [selected, setSelected] = React.useState(null);
    const [exporting, setExporting] = React.useState(false);
    const [notice, setNotice] = React.useState('');
    const page = cursors.length;
    const cursor = cursors[page - 1];
    const mounted = React.useRef(true);
    React.useEffect(() => {
      mounted.current = true;
      return () => {
        mounted.current = false;
      };
    }, []);
    React.useEffect(() => {
      const controller = new AbortController();
      setLoading(true);
      setError('');
      requestLogs(filters, cursor, 50, controller.signal).then(result => {
        if (!controller.signal.aborted) setPayload(result);
      }).catch(e => {
        if (!controller.signal.aborted && e.name !== 'AbortError') {
          setError(e.message);
          setPayload(null);
        }
      }).finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
      return () => controller.abort();
    }, [filters, cursor, tick]);
    React.useEffect(() => {
      if (!live || page !== 1) return;
      const timer = setInterval(() => {
        if (document.visibilityState === 'visible') setTick(v => v + 1);
      }, 5000);
      return () => clearInterval(timer);
    }, [live, page]);
    const apply = next => {
      setFilters({
        ...next
      });
      setCursors([null]);
      setNotice('');
    };
    const update = key => e => setDraft(d => ({
      ...d,
      [key]: e.target.value
    }));
    const trace = item => {
      const next = {
        ...freshFilters(),
        min_level: 'DEBUG',
        trace_id: item.trace_id
      };
      setDraft(next);
      apply(next);
      setSelected(null);
      setLive(false);
    };
    const exportLogs = async () => {
      setExporting(true);
      setNotice('');
      try {
        let before = null,
          rows = [],
          meta = null,
          truncated = false;
        do {
          const result = await requestLogs(filters, before, 100);
          rows = rows.concat(result.items || []);
          meta = result.meta;
          before = result.next_cursor;
          if (rows.length >= 1000) {
            truncated = Boolean(before);
            break;
          }
        } while (before && mounted.current);
        if (!mounted.current) return;
        const report = {
          format: 'proactive-log-center-v1',
          exported_at: new Date().toISOString(),
          filters,
          records_order: 'newest_first',
          truncated,
          max_records: 1000,
          meta,
          warning: '包含会话标识；分享前请再次检查。无正文、提示词、动态日志参数和原始异常消息。未知发送结果不代表送达。',
          records: rows
        };
        const url = URL.createObjectURL(new Blob([JSON.stringify(report, null, 2)], {
          type: 'application/json'
        }));
        const a = document.createElement('a');
        a.href = url;
        a.download = 'proactive-diagnostics-' + Date.now() + '.json';
        a.click();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
        setNotice(`已导出 ${rows.length} 条${truncated ? '（达到 1000 条上限，请缩小筛选范围）' : ''}。文件含会话标识，分享前请检查。`);
      } catch (e) {
        if (mounted.current) setError(e.message);
      } finally {
        if (mounted.current) setExporting(false);
      }
    };
    const items = payload?.items || [],
      meta = payload?.meta;
    return /*#__PURE__*/React.createElement("section", {
      className: "logs-view",
      "aria-label": "\u65E5\u5FD7\u4E2D\u5FC3"
    }, /*#__PURE__*/React.createElement("div", {
      className: "logs-heading"
    }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement(Typography, {
      variant: "h4",
      sx: {
        fontWeight: 800
      }
    }, "\u65E5\u5FD7\u4E2D\u5FC3"), /*#__PURE__*/React.createElement(Typography, {
      sx: {
        mt: 1,
        opacity: .7
      }
    }, "\u6BCF\u6B21\u51B3\u7B56\uFF0C\u90FD\u6709\u8FF9\u53EF\u5FAA")), /*#__PURE__*/React.createElement(Chip, {
      label: "\u672C\u5730\u5B58\u50A8 \xB7 \u6D4B\u8BD5\u7248",
      variant: "outlined"
    })), /*#__PURE__*/React.createElement("div", {
      className: "logs-status-strip"
    }, /*#__PURE__*/React.createElement("span", null, /*#__PURE__*/React.createElement("strong", null, meta?.debug_enabled ? 'DEBUG+' : 'INFO+'), " \u91C7\u96C6\u7B49\u7EA7"), /*#__PURE__*/React.createElement("span", null, /*#__PURE__*/React.createElement("strong", null, meta?.retention_days ?? '—', " \u5929"), " \u4FDD\u7559\u4E0A\u9650"), /*#__PURE__*/React.createElement("span", null, /*#__PURE__*/React.createElement("strong", null, meta?.max_entries?.toLocaleString() ?? '—', " \u6761"), " \u5BB9\u91CF\u4E0A\u9650"), /*#__PURE__*/React.createElement("span", null, /*#__PURE__*/React.createElement("strong", null, meta?.dropped ?? 0), " \u672C\u6B21\u542F\u52A8\u4E22\u5F03")), /*#__PURE__*/React.createElement(Alert, {
      severity: "info",
      sx: {
        mb: 2
      }
    }, "\u4EC5\u8BB0\u5F55\u672C\u63D2\u4EF6\u3002\u6B63\u6587\u3001\u63D0\u793A\u8BCD\u3001\u52A8\u6001\u53C2\u6570\u4E0E\u5F02\u5E38\u6D88\u606F\u4E0D\u843D\u76D8\uFF1B\u4FDD\u7559\u9519\u8BEF\u7C7B\u578B\u548C\u8C03\u7528\u6808\u4F4D\u7F6E\u3002\u53D1\u9001\u63A5\u53E3\u8FD4\u56DE\u6210\u529F\u4E0D\u7B49\u4E8E\u7EC8\u7AEF\u9001\u8FBE\u6216\u5DF2\u8BFB\u3002"), meta?.enabled === false && /*#__PURE__*/React.createElement(Alert, {
      severity: "warning",
      sx: {
        mb: 2
      }
    }, "\u65E5\u5FD7\u91C7\u96C6\u5DF2\u5173\u95ED\u3002\u8BF7\u5728\u914D\u7F6E\u7BA1\u7406\u7684\u300C\u672C\u5730\u65E5\u5FD7\u4E2D\u5FC3\u300D\u5F00\u542F\u5E76\u91CD\u8F7D\u63D2\u4EF6\u3002"), meta?.storage_error && /*#__PURE__*/React.createElement(Alert, {
      severity: "error",
      sx: {
        mb: 2
      }
    }, "\u65E5\u5FD7\u5B58\u50A8\u5F02\u5E38\uFF1A", meta.storage_error, "\u3002\u8BF7\u68C0\u67E5\u78C1\u76D8\u7A7A\u95F4\u548C\u76EE\u5F55\u6743\u9650\uFF1B\u4E3B\u52A8\u6D88\u606F\u7EE7\u7EED\u8FD0\u884C\uFF0C\u671F\u95F4\u65E5\u5FD7\u53EF\u80FD\u4E22\u5931\u3002"), !!meta?.dropped && /*#__PURE__*/React.createElement(Alert, {
      severity: "warning",
      sx: {
        mb: 2
      }
    }, "\u65E5\u5FD7\u961F\u5217\u6216\u5B58\u50A8\u538B\u529B\u5BFC\u81F4\u8BB0\u5F55\u4E22\u5931\uFF1B\u672C\u9875\u4E0D\u80FD\u4EE3\u8868\u5B8C\u6574\u5386\u53F2\u3002"), /*#__PURE__*/React.createElement("form", {
      className: "logs-filters",
      onSubmit: e => {
        e.preventDefault();
        apply(draft);
      }
    }, /*#__PURE__*/React.createElement(TextField, {
      select: true,
      size: "small",
      label: "\u6700\u4F4E\u7B49\u7EA7",
      value: draft.min_level,
      onChange: update('min_level')
    }, ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'].map(level => /*#__PURE__*/React.createElement(MenuItem, {
      key: level,
      value: level
    }, level, " \u53CA\u4EE5\u4E0A"))), /*#__PURE__*/React.createElement(TextField, {
      select: true,
      size: "small",
      label: "\u4E8B\u4EF6\u7C7B\u578B",
      value: draft.category,
      onChange: update('category')
    }, /*#__PURE__*/React.createElement(MenuItem, {
      value: ""
    }, "\u5168\u90E8\u4E8B\u4EF6"), Object.entries(categories).map(([key, label]) => /*#__PURE__*/React.createElement(MenuItem, {
      key: key,
      value: key
    }, label))), /*#__PURE__*/React.createElement(TextField, {
      size: "small",
      label: "\u4F1A\u8BDD ID\uFF08\u5B8C\u6574\u5339\u914D\uFF09",
      value: draft.session_id,
      onChange: update('session_id'),
      inputProps: {
        maxLength: 256
      }
    }), /*#__PURE__*/React.createElement(TextField, {
      size: "small",
      label: "\u5173\u8054 ID\uFF08\u5B8C\u6574\u5339\u914D\uFF09",
      value: draft.trace_id,
      onChange: update('trace_id'),
      inputProps: {
        maxLength: 64
      }
    }), /*#__PURE__*/React.createElement(TextField, {
      size: "small",
      type: "datetime-local",
      label: "\u5F00\u59CB\u65F6\u95F4\uFF08\u6D4F\u89C8\u5668\u65F6\u533A\uFF09",
      InputLabelProps: {
        shrink: true
      },
      value: draft.since,
      onChange: update('since')
    }), /*#__PURE__*/React.createElement(TextField, {
      size: "small",
      type: "datetime-local",
      label: "\u7ED3\u675F\u65F6\u95F4\uFF08\u6D4F\u89C8\u5668\u65F6\u533A\uFF09",
      InputLabelProps: {
        shrink: true
      },
      value: draft.until,
      onChange: update('until')
    }), /*#__PURE__*/React.createElement(Button, {
      type: "submit",
      variant: "contained",
      disabled: loading
    }, "\u5E94\u7528\u7B5B\u9009"), /*#__PURE__*/React.createElement(Button, {
      onClick: () => {
        const next = freshFilters();
        setDraft(next);
        apply(next);
      }
    }, "\u91CD\u7F6E\u7B5B\u9009")), /*#__PURE__*/React.createElement("div", {
      className: "logs-toolbar"
    }, /*#__PURE__*/React.createElement(Button, {
      variant: "outlined",
      onClick: () => {
        setCursors([null]);
        setTick(v => v + 1);
      },
      disabled: loading
    }, "\u5237\u65B0\u6700\u65B0"), /*#__PURE__*/React.createElement(Button, {
      variant: live ? 'contained' : 'outlined',
      onClick: () => setLive(v => !v),
      "aria-pressed": live
    }, live ? '暂停自动刷新' : '自动刷新（5 秒）'), /*#__PURE__*/React.createElement(Button, {
      onClick: exportLogs,
      disabled: loading || exporting || !items.length
    }, exporting ? '正在导出…' : '导出筛选诊断 JSON'), /*#__PURE__*/React.createElement(Typography, {
      variant: "caption",
      sx: {
        ml: 'auto',
        opacity: .7
      }
    }, "\u7B2C ", page, " \u9875 \xB7 \u6700\u65B0\u5728\u524D", live && page > 1 ? ' · 翻页时暂停刷新' : '')), error && /*#__PURE__*/React.createElement(Alert, {
      severity: "error",
      sx: {
        mb: 2
      }
    }, error), notice && /*#__PURE__*/React.createElement(Alert, {
      severity: "success",
      sx: {
        mb: 2
      }
    }, notice), loading && /*#__PURE__*/React.createElement(Box, {
      role: "status",
      sx: {
        display: 'flex',
        gap: 1,
        alignItems: 'center',
        p: 2
      }
    }, /*#__PURE__*/React.createElement(CircularProgress, {
      size: 16
    }), " \u6B63\u5728\u8BFB\u53D6\u65E5\u5FD7\u2026"), !loading && !error && items.length === 0 && /*#__PURE__*/React.createElement("div", {
      className: "logs-empty"
    }, /*#__PURE__*/React.createElement(Typography, {
      variant: "h6"
    }, "\u8FD8\u6CA1\u6709\u5339\u914D\u7684\u65E5\u5FD7"), /*#__PURE__*/React.createElement(Typography, {
      sx: {
        opacity: .7,
        mt: 1
      }
    }, "\u65B0\u5B89\u88C5\u4E0D\u4F1A\u8865\u5F55\u65E7\u5386\u53F2\u3002\u8BD5\u8BD5\u91CD\u7F6E\u7B5B\u9009\uFF0C\u6216\u7B49\u5F85\u4E0B\u4E00\u6B21\u4E3B\u52A8\u4EFB\u52A1\u3002"), filters.min_level === 'DEBUG' && !meta?.debug_enabled && /*#__PURE__*/React.createElement(Typography, {
      sx: {
        mt: 1
      }
    }, "DEBUG \u5C1A\u672A\u91C7\u96C6\uFF0C\u8BF7\u5728\u914D\u7F6E\u4E2D\u663E\u5F0F\u5F00\u542F\u5E76\u91CD\u8F7D\u63D2\u4EF6\u3002")), /*#__PURE__*/React.createElement("div", {
      className: "logs-list",
      "aria-busy": loading
    }, items.map(item => /*#__PURE__*/React.createElement("button", {
      className: "log-row",
      key: item.id,
      onClick: () => setSelected(item),
      "aria-label": `查看日志 ${item.id} ${item.level} ${item.summary}`
    }, /*#__PURE__*/React.createElement("div", {
      className: "log-row-top"
    }, /*#__PURE__*/React.createElement(Chip, {
      size: "small",
      label: item.level,
      color: color(item.level),
      variant: item.level === 'CRITICAL' ? 'filled' : 'outlined'
    }), /*#__PURE__*/React.createElement("span", {
      className: "log-category"
    }, categories[item.category] || item.category), /*#__PURE__*/React.createElement("time", null, stamp(item.ts)), /*#__PURE__*/React.createElement("span", {
      className: "log-id"
    }, "#", item.id)), /*#__PURE__*/React.createElement("div", {
      className: "log-summary"
    }, item.summary), (item.details.reason || item.details.outcome) && /*#__PURE__*/React.createElement("div", {
      className: "log-reason"
    }, reasons[item.details.reason] || outcomes[item.details.outcome] || item.details.reason || item.details.outcome), detailLine(item) && /*#__PURE__*/React.createElement("div", {
      className: "log-reason"
    }, detailLine(item)), /*#__PURE__*/React.createElement("div", {
      className: "log-row-bottom"
    }, /*#__PURE__*/React.createElement("span", null, item.session_id || '插件运行'), item.trace_id && /*#__PURE__*/React.createElement("span", null, "\u94FE\u8DEF ", item.trace_id.slice(0, 12)), item.details.source && /*#__PURE__*/React.createElement("span", null, item.details.source, ":", item.details.line))))), /*#__PURE__*/React.createElement("div", {
      className: "logs-pagination"
    }, /*#__PURE__*/React.createElement(Button, {
      disabled: page === 1 || loading,
      onClick: () => setCursors(c => c.slice(0, -1))
    }, "\u4E0A\u4E00\u9875"), /*#__PURE__*/React.createElement("span", null, "\u7B2C ", page, " \u9875"), /*#__PURE__*/React.createElement(Button, {
      disabled: !payload?.next_cursor || loading,
      onClick: () => setCursors(c => [...c, payload.next_cursor])
    }, "\u4E0B\u4E00\u9875")), /*#__PURE__*/React.createElement(Dialog, {
      open: Boolean(selected),
      onClose: () => setSelected(null),
      maxWidth: "md",
      fullWidth: true,
      "aria-labelledby": "log-detail-title"
    }, /*#__PURE__*/React.createElement(DialogTitle, {
      id: "log-detail-title"
    }, "\u65E5\u5FD7\u8BE6\u60C5 ", selected && `#${selected.id}`), selected && /*#__PURE__*/React.createElement(DialogContent, {
      dividers: true
    }, /*#__PURE__*/React.createElement("div", {
      className: "log-row-top"
    }, /*#__PURE__*/React.createElement(Chip, {
      size: "small",
      label: selected.level,
      color: color(selected.level)
    }), /*#__PURE__*/React.createElement("span", null, categories[selected.category], " \xB7 ", selected.event)), /*#__PURE__*/React.createElement(Typography, {
      sx: {
        mt: 2,
        mb: 2,
        fontWeight: 700
      }
    }, selected.summary), /*#__PURE__*/React.createElement(Typography, {
      variant: "body2",
      sx: {
        overflowWrap: 'anywhere'
      }
    }, "\u65F6\u95F4\uFF1A", stamp(selected.ts), /*#__PURE__*/React.createElement("br", null), "\u4F1A\u8BDD\uFF1A", selected.session_id || '插件运行', /*#__PURE__*/React.createElement("br", null), "\u5173\u8054 ID\uFF1A", selected.trace_id || '无（不属于主动任务）'), /*#__PURE__*/React.createElement(Typography, {
      variant: "subtitle2",
      sx: {
        mt: 3
      }
    }, "\u7ED3\u6784\u5316\u8BE6\u60C5\uFF08\u672A\u77E5\u5B57\u6BB5\u4E0D\u586B\u5145\uFF09"), /*#__PURE__*/React.createElement("pre", {
      className: "log-detail-json"
    }, JSON.stringify(selected.details, null, 2)), selected.details.exception && /*#__PURE__*/React.createElement(Alert, {
      severity: "warning"
    }, "\u5F02\u5E38\u6D88\u606F\u3001\u6E90\u4EE3\u7801\u884C\u53CA\u5C40\u90E8\u53D8\u91CF\u5DF2\u9690\u85CF\u3002frames \u4FDD\u7559\u771F\u5B9E\u8C03\u7528\u6808\u4F4D\u7F6E\uFF1B\u8D85\u8FC7\u5B89\u5168\u5927\u5C0F\u7684\u6808\u4F1A\u6807\u6CE8\u622A\u65AD\u3002")), /*#__PURE__*/React.createElement(DialogActions, null, selected?.trace_id && /*#__PURE__*/React.createElement(Button, {
      onClick: () => trace(selected)
    }, "\u67E5\u770B\u672C\u6B21\u4EFB\u52A1\u5B8C\u6574\u94FE\u8DEF"), /*#__PURE__*/React.createElement(Button, {
      onClick: () => setSelected(null)
    }, "\u5173\u95ED"))));
  }
  window.LogsView = LogsView;
})();