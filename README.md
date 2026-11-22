# 科研助手（纯 Flask 前后端分离 + JWT/RBAC 权限 + RAG）

面向 **2核 4G 云服务器** 设计的轻量前后端分离科研助手，适合用来完整演练 **Git 代码管理、登录权限（JWT）、接口权限（RBAC 401/403）、前后端联调、云端部署**。

## 目录结构

```text
research_assistant_app/
├── .gitignore              # 保护 .env、.venv、data/ 不泄露到 Git
├── .env.example            # 环境变量模板（复制为 .env 后填入真实 Key）
├── backend/                # Flask API 后端（默认端口 8000）
│   ├── pyproject.toml
│   ├── auth.py             # PBKDF2 密码哈希 + JWT + @jwt_required / @admin_required 装饰器
│   ├── rag_engine.py       # PyMuPDF 分页切块 + ChromaDB + bge-m3 + DeepSeek
│   └── main.py             # REST API 路由
└── flask_frontend/         # Flask 前端 BFF（默认端口 5000）
    ├── pyproject.toml
    ├── app.py              # 页面登录拦截 + Session 存 JWT + 代理转发
    └── templates/
        ├── login.html      # 登录/注册页
        ├── index.html      # 科研工作台 + 401/403 权限联调演练区
        └── admin.html      # 管理员面板
```

## 权限设计矩阵

| 接口 / 页面 | 所需权限 | 未登录返回 | 普通用户 (`researcher`) | 管理员 (`admin`) |
| :--- | :--- | :---: | :---: | :---: |
| `GET /api/health` | 公开 | `200` | `200` | `200` |
| `POST /api/auth/login` | 公开 | `200` / `401` | `200` | `200` |
| `GET /api/files` | 登录态 | **`401`** | `200` | `200` |
| `POST /api/chat` | 登录态 | **`401`** | `200` | `200` |
| `POST /api/upload` | 仅管理员 | **`401`** | **`403`** | `200` |
| `DELETE /api/files/{id}` | 仅管理员 | **`401`** | **`403`** | `200` |
| `GET /api/admin/users` | 仅管理员 | **`401`** | **`403`** | `200` |
| Flask `/` 与 `/admin` | 页面登录态 | 自动跳 `/login` | `/` 可进，`/admin` 显示 403 | 均可访问 |

预置测试账号（首次启动后端自动写入 SQLite）：
- 管理员：`admin` / `admin123`
- 普通研究员：`researcher` / `user123`

## 快速启动（本地或云端通用）

1. 复制环境变量并填入你的 DeepSeek / 硅基流动 API Key：
   ```bash
   cp .env.example .env
   ```
2. 启动 Flask 后端 API（8000 端口）：
   ```bash
   cd backend
   uv sync --default-index https://pypi.tuna.tsinghua.edu.cn/simple
   uv run python main.py
   ```
3. 启动 Flask 前端（5000 端口）：
   ```bash
   cd flask_frontend
   uv sync --default-index https://pypi.tuna.tsinghua.edu.cn/simple
   uv run python app.py
   ```

## 大联调演练清单

1. **未登录拦截**：直接访问 `http://<IP>:5000/` -> 自动重定向到 `/login`。
2. **管理员登录**：用 `admin / admin123` 登录 -> 上传一篇 PDF 论文 -> 提问验证 DeepSeek 回答与右侧原文页码溯源 -> 点击“管理员面板”看到用户表。
3. **401 演练**：点击左下角 `测 401(无Token)` -> 观察后端返回 `HTTP 401`。
4. **403 越权演练**：退出登录，改用 `researcher / user123` 登录：
   - 可以正常看论文列表和 RAG 提问（`200`）；
   - 尝试上传或删除论文、点 `测 403(管理接口)` 或进“管理员面板” -> 全部被后端拦下并返回 **`HTTP 403`**！
