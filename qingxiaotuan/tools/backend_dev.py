"""增强的后端开发工具集: 提供专业的后端开发能力。

功能:
- 代码生成与模板
- API 设计与文档
- 数据库迁移
- 测试生成
- 部署配置
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..core.kernel import Kernel, Plugin
from .base import Tool, ToolContext, string_prop


# 代码模板库
CODE_TEMPLATES = {
    "python_fastapi": {
        "name": "FastAPI 应用模板",
        "description": "生成 FastAPI REST API 应用代码",
        "template": '''"""
{project_name} - FastAPI 应用
"""
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional, List
import uvicorn

app = FastAPI(
    title="{project_name}",
    description="{description}",
    version="0.1.0"
)


class Item(BaseModel):
    """数据模型"""
    id: Optional[int] = None
    name: str
    description: Optional[str] = None
    price: float
    is_offer: Optional[bool] = None


# 内存数据库 (生产环境请使用真实数据库)
fake_items_db = [{"id": 1, "name": "Foo", "price": 50.2}]


@app.get("/")
async def root():
    return {"message": "Welcome to {project_name}"}


@app.get("/items/{{item_id}}")
async def read_item(item_id: int, q: Optional[str] = None):
    if q:
        return {"item_id": item_id, "q": q}
    if item_id not in [item["id"] for item in fake_items_db]:
        raise HTTPException(status_code=404, detail="Item not found")
    return {"item_id": item_id}


@app.get("/items/")
async def read_items(skip: int = 0, limit: int = 10):
    return fake_items_db[skip : skip + limit]


@app.post("/items/")
async def create_item(item: Item):
    item_dict = item.dict()
    if item.id is None:
        item_dict["id"] = len(fake_items_db) + 1
    else:
        item_dict["id"] = item.id
    fake_items_db.append(item_dict)
    return item_dict


@app.put("/items/{{item_id}}")
async def update_item(item_id: int, item: Item):
    if item_id not in [item["id"] for item in fake_items_db]:
        raise HTTPException(status_code=404, detail="Item not found")
    for i, existing in enumerate(fake_items_db):
        if existing["id"] == item_id:
            fake_items_db[i] = {**item.dict(), "id": item_id}
            return fake_items_db[i]
    return {"error": "Item not found"}


@app.delete("/items/{{item_id}}")
async def delete_item(item_id: int):
    if item_id not in [item["id"] for item in fake_items_db]:
        raise HTTPException(status_code=404, detail="Item not found")
    fake_items_db[:] = [item for item in fake_items_db if item["id"] != item_id]
    return {"message": "Item deleted"}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
''',
    },
    "python_flask": {
        "name": "Flask 应用模板",
        "description": "生成 Flask REST API 应用代码",
        "template": '''"""
{project_name} - Flask 应用
"""
from flask import Flask, jsonify, request
from flask_cors import CORS

app = Flask(__name__)
CORS(app)


@app.route("/")
def hello():
    return jsonify({{"message": "Welcome to {project_name}"}})


@app.route("/items", methods=["GET"])
def get_items():
    return jsonify({{"items": []}})


@app.route("/items/<int:item_id>", methods=["GET"])
def get_item(item_id):
    return jsonify({{"id": item_id, "name": "Item"}})


@app.route("/items", methods=["POST"])
def create_item():
    data = request.get_json()
    return jsonify(data), 201


@app.route("/items/<int:item_id>", methods=["PUT"])
def update_item(item_id):
    data = request.get_json()
    return jsonify({{"id": item_id, **data}})


@app.route("/items/<int:item_id>", methods=["DELETE"])
def delete_item(item_id):
    return jsonify({{"message": "Deleted"}}), 204


if __name__ == "__main__":
    app.run(debug=True, port=5000)
''',
    },
    "docker_compose": {
        "name": "Docker Compose 模板",
        "description": "生成 Docker Compose 配置文件",
        "template": '''version: '3.8'

services:
  app:
    build: .
    ports:
      - "8000:8000"
    environment:
      - DATABASE_URL=postgresql://user:password@db:5432/mydb
      - REDIS_URL=redis://redis:6379
    depends_on:
      - db
      - redis
    volumes:
      - .:/app
    command: uvicorn main:app --host 0.0.0.0 --port 8000 --reload

  db:
    image: postgres:15
    environment:
      - POSTGRES_USER=user
      - POSTGRES_PASSWORD=password
      - POSTGRES_DB=mydb
    ports:
      - "5432:5432"
    volumes:
      - postgres_data:/var/lib/postgresql/data

  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"

  nginx:
    image: nginx:alpine
    ports:
      - "80:80"
    volumes:
      - ./nginx.conf:/etc/nginx/nginx.conf
    depends_on:
      - app

volumes:
  postgres_data:
''',
    },
    "github_actions": {
        "name": "GitHub Actions CI/CD 模板",
        "description": "生成 GitHub Actions 工作流配置",
        "template": '''name: CI/CD Pipeline

on:
  push:
    branches: [ main, develop ]
  pull_request:
    branches: [ main ]

jobs:
  test:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        python-version: ["3.10", "3.11", "3.12"]
    
    steps:
    - uses: actions/checkout@v4
    
    - name: Set up Python ${{{{ matrix.python-version }}}}
      uses: actions/setup-python@v5
      with:
        python-version: ${{{{ matrix.python-version }}}}
    
    - name: Install dependencies
      run: |
        python -m pip install --upgrade pip
        pip install -r requirements.txt
        pip install pytest pytest-cov
    
    - name: Run tests
      run: |
        pytest --cov=app --cov-report=xml
    
    - name: Upload coverage to Codecov
      uses: codecov/codecov-action@v3
      with:
        file: ./coverage.xml

  lint:
    runs-on: ubuntu-latest
    steps:
    - uses: actions/checkout@v4
    
    - name: Set up Python
      uses: actions/setup-python@v5
      with:
        python-version: "3.11"
    
    - name: Run linter
      run: |
        pip install ruff
        ruff check .

  deploy:
    needs: [test, lint]
    runs-on: ubuntu-latest
    if: github.ref == 'refs/heads/main'
    
    steps:
    - uses: actions/checkout@v4
    
    - name: Deploy to production
      run: |
        echo "Deploying to production..."
        # Add your deployment commands here
''',
    },
}


def generate_code_from_template(
    ctx: ToolContext,
    template_name: str,
    project_name: str = "my_project",
    description: str = "",
    output_dir: str = ".",
) -> str:
    """从模板生成代码。"""
    if template_name not in CODE_TEMPLATES:
        available = ", ".join(CODE_TEMPLATES.keys())
        return f"模板 '{template_name}' 不存在。可用模板: {available}"
    
    template = CODE_TEMPLATES[template_name]
    code = template["template"].format(
        project_name=project_name,
        description=description or f"{project_name} application",
    )
    
    # 确定输出文件名
    file_extensions = {
        "python_fastapi": "main.py",
        "python_flask": "app.py",
        "docker_compose": "docker-compose.yml",
        "github_actions": ".github/workflows/ci.yml",
    }
    
    filename = file_extensions.get(template_name, "output.txt")
    output_path = Path(ctx.workspace) / output_dir / filename
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    output_path.write_text(code, encoding="utf-8")
    return f"已生成 {template['name']}: {output_path}"


def list_code_templates(ctx: ToolContext) -> str:
    """列出所有可用的代码模板。"""
    lines = ["可用的代码模板:"]
    for name, template in CODE_TEMPLATES.items():
        lines.append(f"  • {name}: {template['description']}")
    lines.append("")
    lines.append("使用方法: 在 /sandbox 中执行 python -c 'from qingxiaotuan.tools.backend_dev import generate_code_from_template; ...'")
    return "\n".join(lines)


class BackendDevPlugin(Plugin):
    """后端开发工具插件。"""
    
    name = "tools.backend_dev"
    provides = []
    requires = ["tool_registry"]
    
    def activate(self, kernel: Kernel) -> None:
        config = kernel.get("config")
        if config and not config.get("tools.backend_dev.enabled", True):
            return
        
        registry = kernel.require("tool_registry")
        
        # 注册模板生成工具
        registry.register(Tool(
            name="generate_from_template",
            description="从预定义模板生成代码 (FastAPI/Flask/Docker/GitHub Actions)",
            parameters={
                "type": "object",
                "properties": {
                    "template_name": {
                        "type": "string",
                        "description": "模板名称 (python_fastapi/python_flask/docker_compose/github_actions)",
                    },
                    "project_name": {
                        "type": "string",
                        "description": "项目名称",
                    },
                    "description": {
                        "type": "string",
                        "description": "项目描述",
                    },
                    "output_dir": {
                        "type": "string",
                        "description": "输出目录 (默认当前目录)",
                    },
                },
                "required": ["template_name"],
            },
            handler=generate_code_from_template,
            group="backend",
        ))
        
        # 注册模板列表工具
        registry.register(Tool(
            name="list_templates",
            description="列出所有可用的代码模板",
            parameters={
                "type": "object",
                "properties": {},
            },
            handler=list_code_templates,
            read_only=True,
            group="backend",
        ))
