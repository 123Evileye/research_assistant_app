"""
科研助手 Flask 前端服务（BFF 架构）
===================================
核心学习点：
1. 页面级登录拦截：未登录访问 / 或 /admin 自动跳转 /login。
2. Session + JWT 配合：登录成功后把 FastAPI 返回的 JWT Token 存入服务端加密 Cookie(session)，
   前端 JS 不直接接触明文 Token，由 Flask 代理转发时在 Header 自动注入 Authorization: Bearer <token>。
3. 接口权限透传：普通研究员点击管理员接口时，FastAPI 返回 403 Forbidden，Flask 原样透传给前端展示。
"""

import os
from functools import wraps
from pathlib import Path

import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, redirect, render_template, request, session, url_for

ROOT_ENV = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(ROOT_ENV)

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev-flask-session-secret")

BACKEND_API_URL = os.getenv("BACKEND_API_URL", "http://127.0.0.1:8000").rstrip("/")


def _auth_headers() -> dict:
    token = session.get("access_token")
    if token:
        return {"Authorization": f"Bearer {token}"}
    return {}


def login_required(is_api: bool = False):
    """页面/代理接口登录校验装饰器"""
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if "access_token" not in session:
                if is_api:
                    return jsonify({"detail": "未登录（Flask Session 无有效令牌）"}), 401
                return redirect(url_for("login_page"))
            return fn(*args, **kwargs)
        return wrapper
    return decorator


# ==================== 页面路由 ====================

@app.route("/login", methods=["GET", "POST"])
def login_page():
    error = None
    success = request.args.get("msg")
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        try:
            resp = requests.post(
                f"{BACKEND_API_URL}/api/auth/login",
                json={"username": username, "password": password},
                timeout=10,
            )
            if resp.status_code == 200:
                data = resp.json()
                session["access_token"] = data["access_token"]
                session["user"] = data["user"]
                return redirect(url_for("index_page"))
            error = resp.json().get("detail", f"登录失败（HTTP {resp.status_code}）")
        except requests.RequestException as e:
            error = f"无法连接后端服务 ({BACKEND_API_URL})：{e}"
    return render_template("login.html", error=error, success=success, backend_url=BACKEND_API_URL)


@app.route("/register", methods=["POST"])
def register_action():
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")
    try:
        resp = requests.post(
            f"{BACKEND_API_URL}/api/auth/register",
            json={"username": username, "password": password},
            timeout=10,
        )
        if resp.status_code == 200:
            return redirect(url_for("login_page", msg="注册成功！请使用新账号登录（角色：researcher）"))
        error = resp.json().get("detail", f"注册失败（HTTP {resp.status_code}）")
    except requests.RequestException as e:
        error = f"无法连接后端服务：{e}"
    return render_template("login.html", error=error, success=None, backend_url=BACKEND_API_URL)


@app.route("/logout")
def logout_action():
    session.clear()
    return redirect(url_for("login_page", msg="已安全退出登录"))


@app.route("/")
@login_required(is_api=False)
def index_page():
    return render_template(
        "index.html",
        user=session.get("user"),
        backend_url=BACKEND_API_URL,
    )


@app.route("/admin")
@login_required(is_api=False)
def admin_page():
    """管理员面板：故意直接请求后端 /api/admin/users，让普通用户看到真实的后端 403 拦截"""
    users = []
    error = None
    status_code = 200
    try:
        resp = requests.get(
            f"{BACKEND_API_URL}/api/admin/users",
            headers=_auth_headers(),
            timeout=10,
        )
        status_code = resp.status_code
        if resp.status_code == 200:
            users = resp.json().get("users", [])
        else:
            error = f"HTTP {resp.status_code}：{resp.json().get('detail', resp.text)}"
    except requests.RequestException as e:
        status_code = 502
        error = f"后端连接失败：{e}"

    return render_template(
        "admin.html",
        user=session.get("user"),
        users=users,
        error=error,
        status_code=status_code,
    ), (200 if status_code == 200 else status_code)


# ==================== BFF 联调代理路由（携带 Bearer Token 转发给 FastAPI） ====================

@app.route("/proxy/health")
def proxy_health():
    try:
        resp = requests.get(f"{BACKEND_API_URL}/api/health", timeout=5)
        return jsonify(resp.json()), resp.status_code
    except requests.RequestException as e:
        return jsonify({"status": "error", "detail": str(e)}), 502


@app.route("/proxy/files")
@login_required(is_api=True)
def proxy_files():
    try:
        resp = requests.get(f"{BACKEND_API_URL}/api/files", headers=_auth_headers(), timeout=10)
        return jsonify(resp.json()), resp.status_code
    except requests.RequestException as e:
        return jsonify({"detail": str(e)}), 502


@app.route("/proxy/upload", methods=["POST"])
@login_required(is_api=True)
def proxy_upload():
    f = request.files.get("file")
    if not f:
        return jsonify({"detail": "未选择文件"}), 400
    try:
        resp = requests.post(
            f"{BACKEND_API_URL}/api/upload",
            headers=_auth_headers(),
            files={"file": (f.filename, f.stream, f.mimetype or "application/pdf")},
            timeout=120,
        )
        return jsonify(resp.json()), resp.status_code
    except requests.RequestException as e:
        return jsonify({"detail": str(e)}), 502


@app.route("/proxy/files/<path:file_id>", methods=["DELETE"])
@login_required(is_api=True)
def proxy_delete_file(file_id: str):
    try:
        resp = requests.delete(
            f"{BACKEND_API_URL}/api/files/{file_id}",
            headers=_auth_headers(),
            timeout=15,
        )
        return jsonify(resp.json()), resp.status_code
    except requests.RequestException as e:
        return jsonify({"detail": str(e)}), 502


@app.route("/proxy/chat", methods=["POST"])
@login_required(is_api=True)
def proxy_chat():
    payload = request.get_json(silent=True) or {}
    try:
        resp = requests.post(
            f"{BACKEND_API_URL}/api/chat",
            headers=_auth_headers(),
            json=payload,
            timeout=120,
        )
        return jsonify(resp.json()), resp.status_code
    except requests.RequestException as e:
        return jsonify({"detail": str(e)}), 502


@app.route("/proxy/test-unauth")
def proxy_test_unauth():
    """演练专用：故意不带 Token 请求 /api/files，演示后端 401 Unauthorized 拦截"""
    try:
        resp = requests.get(f"{BACKEND_API_URL}/api/files", timeout=5)
        return jsonify(resp.json()), resp.status_code
    except requests.RequestException as e:
        return jsonify({"detail": str(e)}), 502


@app.route("/proxy/test-admin")
@login_required(is_api=True)
def proxy_test_admin():
    """演练专用：带着当前登录用户的 Token 请求管理员接口，普通用户会收到 403 Forbidden"""
    try:
        resp = requests.get(f"{BACKEND_API_URL}/api/admin/users", headers=_auth_headers(), timeout=5)
        return jsonify(resp.json()), resp.status_code
    except requests.RequestException as e:
        return jsonify({"detail": str(e)}), 502


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
