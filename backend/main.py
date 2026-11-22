"""
科研助手 Flask 后端 API 主入口（端口 8000）
==========================================
权限分层设计（供前后端联调演练）：
1. 公开接口（无需登录）：
   - GET  /api/health
   - POST /api/auth/login
   - POST /api/auth/register
2. 登录权限接口（@jwt_required，未登录返回 401）：
   - GET  /api/auth/me
   - GET  /api/files
   - POST /api/chat
3. 管理员接口权限（@admin_required，普通用户返回 403）：
   - POST   /api/upload
   - DELETE /api/files/<file_id>
   - GET    /api/admin/users
"""

from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, g, jsonify, request, send_from_directory
from flask_cors import CORS

ROOT_ENV = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(ROOT_ENV)

import auth  # noqa: E402
import rag_engine  # noqa: E402

auth.init_user_db()

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})


@app.errorhandler(rag_engine.RagError)
def handle_rag_error(err: rag_engine.RagError):
    return jsonify({"detail": err.detail}), err.status_code


# 提供 PDF 原文访问，供前端“打开原文第 N 页”使用
@app.get("/files/<path:filename>")
def serve_pdf(filename: str):
    return send_from_directory(rag_engine.PDF_DIR, filename)


# ---------- 1. 公开接口 ----------
@app.get("/api/health")
def health_check():
    return jsonify({"status": "ok", "message": "Research Assistant Flask API (RBAC + RAG) is running"})


@app.post("/api/auth/login")
def login():
    body = request.get_json(silent=True) or {}
    username = str(body.get("username", "")).strip()
    password = str(body.get("password", ""))
    user = auth.authenticate_user(username, password)
    if not user:
        return jsonify({"detail": "用户名或密码错误"}), 401
    token = auth.create_access_token(username=user["username"], role=user["role"])
    return jsonify({
        "access_token": token,
        "token_type": "bearer",
        "user": {"username": user["username"], "role": user["role"]},
    })


@app.post("/api/auth/register")
def register():
    body = request.get_json(silent=True) or {}
    username = str(body.get("username", "")).strip()
    password = str(body.get("password", ""))
    if len(username) < 3 or len(password) < 6:
        return jsonify({"detail": "用户名至少 3 位，密码至少 6 位"}), 400
    user = auth.register_user(username, password, role="researcher")
    if not user:
        return jsonify({"detail": "用户名已存在"}), 400
    return jsonify({"message": "注册成功（默认角色：researcher）", "user": user})


# ---------- 2. 登录权限接口（401 保护） ----------
@app.get("/api/auth/me")
@auth.jwt_required
def get_me():
    return jsonify({"user": g.current_user})


@app.get("/api/files")
@auth.jwt_required
def get_files():
    return jsonify({"files": rag_engine.list_pdf_files(), "requested_by": g.current_user["username"]})


@app.post("/api/chat")
@auth.jwt_required
def chat():
    body = request.get_json(silent=True) or {}
    question = str(body.get("question", "")).strip()
    file_ids = body.get("file_ids") or []
    if not question:
        return jsonify({"detail": "问题不能为空"}), 400
    base_url = request.host_url.rstrip("/")
    result = rag_engine.rag_chat(
        question=question,
        file_ids=file_ids,
        base_url_for_files=base_url,
    )
    result["user"] = g.current_user["username"]
    return jsonify(result)


# ---------- 3. 管理员接口权限（403 保护） ----------
@app.post("/api/upload")
@auth.admin_required
def upload_pdf():
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"detail": "未选择文件"}), 400
    res = rag_engine.index_pdf(f.filename, f.read())
    res["uploaded_by"] = g.current_user["username"]
    return jsonify(res)


@app.delete("/api/files/<path:file_id>")
@auth.admin_required
def delete_file(file_id: str):
    deleted = rag_engine.delete_pdf(file_id)
    if not deleted:
        return jsonify({"detail": "文件不存在"}), 404
    return jsonify({"deleted": file_id, "operator": g.current_user["username"]})


@app.get("/api/admin/users")
@auth.admin_required
def admin_users():
    return jsonify({"users": auth.list_all_users(), "operator": g.current_user["username"]})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True)
