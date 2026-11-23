"""业务路由汇总：固定路径接口先注册，再挂通用资源路由。"""
from __future__ import annotations

from app.routers import intake, resource

ROUTERS = [intake, resource]
