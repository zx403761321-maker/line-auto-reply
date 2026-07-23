我们正在对 LINE 自动化获客系统做稳定性改造。

项目目录: /root/line-auto-reply/
架构文档: /root/STABILITY_REVIEW.md

---

## 已完成
- P0-1: 锁泄漏修复 (try/finally)
- P0-2: API Key 环境变量化
- P0-3: /health 全设备检查
- P0-4: Docker 日志限制
- P0-5: 全局异常兜底
- P1-6: loop.py 整合进 Docker
- P1-7: 结构化日志 (logger.py)

---

## 当前阶段 — 10-20台（现在做）

1. P1-8: ADB 重连加指数退避 (main.py ensure_connected)
2. P1-9: DeepSeek API 连接池 + 重试 (main.py)
3. P2-2: SQLite 持久化设备状态 + 任务记录 (新建 db.py)

---

## 第三阶段 — 20-50台

4. 固定坐标逐步替换为 uiautomator2 元素定位
5. 设备分组，每 Worker 管 10-15 台
6. Prometheus /metrics 监控端点
7. 钉钉告警通知

---

## 第四阶段 — 50-200台

8. Scheduler + Worker 模式，Redis 任务队列
9. PostgreSQL 替换 SQLite
10. 多服务器部署，灰度发布

---

## 要求
- 改动最小化，不要大重构
- 每次改完验证语法: python3 -c "import py_compile; py_compile.compile('...', doraise=True)"
- 改完不要自动重启 Docker，等我确认
- 不要动生产容器，除非用户明确要求

## 环境说明
- 生产容器: openclaw-adb-bridge (端口 8899)
- 测试容器: openclaw-adb-bridge-test (端口 8898)
- crontab 已配置: `crontab -l` 查看
- line-crm 已合并到本仓库 line-crm/ 目录
- 宿主机原 line-crm 路径: /root/line-crm/ (cron 仍用这个路径)
- 代码在 GitHub: zx403761321-maker/line-auto-reply
- 不要动生产容器，除非用户明确要求
