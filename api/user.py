from typing import Optional
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from service.user import UserService

router = APIRouter(prefix="/api/users", tags=["users"])


class CreateUserRequest(BaseModel):
    """创建用户请求模型"""
    username: str
    password: Optional[str] = None


class UpdateUserRequest(BaseModel):
    """更新用户请求模型"""
    username: Optional[str] = None
    password: Optional[str] = None


class LoginRequest(BaseModel):
    """登录请求模型"""
    username: str
    password: str


@router.post("/create", summary="创建用户", response_description="创建的用户信息")
async def create_user(request: CreateUserRequest):
    """创建新用户"""
    # 输入验证
    if not request.username or not request.username.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="用户名不能为空"
        )

    # 用户名长度验证
    if len(request.username.strip()) < 3:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="用户名长度不能少于3个字符"
        )

    if len(request.username.strip()) > 50:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="用户名长度不能超过50个字符"
        )

    # 用户名格式验证（只允许字母、数字、下划线）
    import re
    if not re.match(r'^[a-zA-Z0-9_]+$', request.username.strip()):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="用户名只能包含字母、数字和下划线"
        )

    # 如果提供了密码，进行密码强度验证
    if request.password:
        if len(request.password) < 6:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="密码长度不能少于6个字符"
            )

    result = await UserService.create_user(request.username.strip(), request.password)

    if not result["success"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result["message"]
        )

    return {
        "code": 200,
        "message": result["message"],
        "data": result["user"]
    }


@router.get("/{user_id}", summary="根据ID获取用户信息")
async def get_user_by_id(user_id: int):
    """根据用户ID获取用户信息"""
    result = await UserService.get_user_by_id(user_id)

    if not result["success"]:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=result["message"]
        )

    return {
        "code": 200,
        "message": result["message"],
        "data": result["user"]
    }


@router.get("/username/{username}", summary="根据用户名获取用户信息")
async def get_user_by_username(username: str):
    """根据用户名获取用户信息"""
    result = await UserService.get_user_by_username(username)

    if not result["success"]:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=result["message"]
        )

    return {
        "code": 200,
        "message": result["message"],
        "data": result["user"]
    }


@router.put("/{user_id}", summary="更新用户信息")
async def update_user(user_id: int, request: UpdateUserRequest):
    """更新用户信息"""
    # 过滤掉None值，只更新提供的字段
    update_data = {k: v for k, v in request.dict().items() if v is not None}

    result = await UserService.update_user(user_id, **update_data)

    if not result["success"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result["message"]
        )

    return {
        "code": 200,
        "message": result["message"],
        "data": result.get("user")
    }


@router.delete("/{user_id}", summary="删除用户")
async def delete_user(user_id: int):
    """删除用户（软删除）"""
    result = await UserService.delete_user(user_id)

    if not result["success"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result["message"]
        )

    return {
        "code": 200,
        "message": result["message"]
    }


@router.post("/login", summary="用户登录")
async def login(request: LoginRequest):
    """用户登录"""
    # 输入验证
    if not request.username or not request.username.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="用户名不能为空"
        )

    if not request.password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="密码不能为空"
        )

    result = await UserService.login(request.username.strip(), request.password)

    if not result["success"]:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=result["message"]
        )

    return {
        "code": 200,
        "message": result["message"],
        "data": result["user"]
    }


@router.get("/list", summary="获取用户列表")
async def list_users(skip: int = 0, limit: int = 100):
    """获取用户列表"""
    result = await UserService.list_users(skip, limit)

    if not result["success"]:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=result["message"]
        )

    return {
        "code": 200,
        "message": result["message"],
        "data": {
            "users": result["users"],
            "total": result["total"]
        }
    }