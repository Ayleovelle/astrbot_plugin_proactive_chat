/**
 * 文件职责：API 访问 Hook，封装后端接口调用并提供稳定引用的方法集合。
 */

function useApi() {
    // 将每个接口都包装成稳定引用的回调，避免依赖它们的 effect / memo 无谓重跑。
    const getStatus = React.useCallback(() => window.HttpUtil.get('/api/status'), []);
    const getConfig = React.useCallback(() => window.HttpUtil.get('/api/config'), []);
    const getConfigSchema = React.useCallback(() => window.HttpUtil.get('/api/config-schema'), []);
    const updateConfig = React.useCallback(
        // 全局配置保存接口，传入完整 payload 由后端进行白名单字段更新。
        (payload) => window.HttpUtil.post('/api/config', payload),
        []
    );

    const listSessions = React.useCallback(
        // 拉取所有已知会话及其覆写状态，用于会话差异配置页与主应用首载。
        () => window.HttpUtil.get('/api/session-config/sessions'),
        []
    );
    const getSessionConfig = React.useCallback(
        // Pages 模式下使用 query param 传递 session（register_web_api 不支持路径参数）。
        (session) => window.HttpUtil.get(`/api/session-config?session=${encodeURIComponent(session)}`),
        []
    );
    const updateSessionConfig = React.useCallback(
        // 同一个接口同时支持 override 与 effective 两种保存模式，由 payload.mode 区分。
        (session, payload) =>
            window.HttpUtil.post('/api/session-config', Object.assign({ session }, payload)),
        []
    );
    const resetSessionConfig = React.useCallback(
        // 删除会话覆写后，该会话会重新完全继承全局配置。
        (session) => window.HttpUtil.post('/api/session-config/reset', { session }),
        []
    );

    const listJobs = React.useCallback(() => window.HttpUtil.get('/api/jobs'), []);
    const listMarkdownFiles = React.useCallback(
        () => window.HttpUtil.get('/api/markdown-files'),
        []
    );
    const getMarkdownFile = React.useCallback(
        (path) => window.HttpUtil.get(`/api/markdown-file?path=${encodeURIComponent(path)}`),
        []
    );
    const getNotifications = React.useCallback(
        () => window.HttpUtil.get('/api/notifications'),
        []
    );
    const readNotification = React.useCallback(
        (id) => window.HttpUtil.post('/api/notifications/read', { id }),
        []
    );
    const readAllNotifications = React.useCallback(
        () => window.HttpUtil.post('/api/notifications/read-all', {}),
        []
    );
    const refreshNotifications = React.useCallback(
        () => window.HttpUtil.post('/api/notifications/refresh', {}),
        []
    );
    const triggerJob = React.useCallback(
        // "立即触发"通过 body 传 session，与 pages_adapter 路由匹配。
        (session) => window.HttpUtil.post('/api/jobs/trigger', { session }),
        []
    );
    const cancelJob = React.useCallback(
        (session) => window.HttpUtil.post('/api/jobs/cancel', { session }),
        []
    );
    const rescheduleJob = React.useCallback(
        (session) => window.HttpUtil.post('/api/jobs/reschedule', { session }),
        []
    );

    return React.useMemo(
        () => ({
            getStatus,
            getConfig,
            getConfigSchema,
            updateConfig,
            listSessions,
            getSessionConfig,
            updateSessionConfig,
            resetSessionConfig,
            listJobs,
            listMarkdownFiles,
            getMarkdownFile,
            getNotifications,
            readNotification,
            readAllNotifications,
            refreshNotifications,
            triggerJob,
            cancelJob,
            rescheduleJob,
        }),
        [
            getStatus,
            getConfig,
            getConfigSchema,
            updateConfig,
            listSessions,
            getSessionConfig,
            updateSessionConfig,
            resetSessionConfig,
            listJobs,
            listMarkdownFiles,
            getMarkdownFile,
            getNotifications,
            readNotification,
            readAllNotifications,
            refreshNotifications,
            triggerJob,
            cancelJob,
            rescheduleJob,
        ]
    );
}

// 暴露到全局，供未经过 ESModule 打包的其余 JSX 文件直接调用。
window.useApi = useApi;

