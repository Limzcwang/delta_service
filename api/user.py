from typing import Optional

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from service.user import UserService

router = APIRouter(prefix="/api/users", tags=["users"])

# 只允许透传这些面向用户的领域文案。服务层若把异常原文拼进 message
# （历史写法如 "创建用户失败: {str(e)}"），一律回退为通用文案，
# 完整异常已经由服务层 log_exception 记录到服务端日志。
_SAFE_SERVICE_MESSAGES = frozenset({
    "创建用户失败",
    "获取用户信息失败",
    "更新用户信息失败",
    "删除用户失败",
    "获取用户列表失败",
    "登录失败",
    "用户不存在",
    "用户不存在或更新失败",
    "用户不存在或删除失败",
    "用户名或密码错误",
})


def _client_error_message(message: Optional[str], fallback: str) -> str:
    """把服务层 message 收敛为可安全回传客户端的文案。"""
    if isinstance(message, str) and message in _SAFE_SERVICE_MESSAGES:
        return message
    return fallback


# 对外用户字段白名单：即使服务层/模型将来多返回字段，API 也不会把 password 带出去。
_PUBLIC_USER_FIELDS = ("id", "username", "created_at", "updated_at")


def _public_user(user):
    """把用户对象/字典收敛为对外可公开字段（显式排除 password）。"""
    if user is None:
        return None
    if isinstance(user, dict):
        return {key: user[key] for key in _PUBLIC_USER_FIELDS if key in user}
    return {key: getattr(user, key) for key in _PUBLIC_USER_FIELDS if hasattr(user, key)}


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
            detail=_client_error_message(result.get("message"), "创建用户失败")
        )

    return {
        "code": 200,
        "message": result["message"],
        "data": _public_user(result["user"])
    }


@router.get("/list", summary="获取用户列表")
async def list_users(skip: int = 0, limit: int = 100):
    """获取用户列表"""
    result = await UserService.list_users(skip, limit)

    if not result["success"]:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=_client_error_message(result.get("message"), "获取用户列表失败")
        )

    return {
        "code": 200,
        "message": result["message"],
        "data": {
            "users": [_public_user(user) for user in result["users"]],
            "total": result["total"]
        }
    }


@router.get("/{user_id}", summary="根据ID获取用户信息")
async def get_user_by_id(user_id: int):
    """根据用户ID获取用户信息"""
    result = await UserService.get_user_by_id(user_id)

    if not result["success"]:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_client_error_message(result.get("message"), "获取用户信息失败")
        )

    return {
        "code": 200,
        "message": result["message"],
        "data": _public_user(result["user"])
    }


@router.get("/username/{username}", summary="根据用户名获取用户信息")
async def get_user_by_username(username: str):
    """根据用户名获取用户信息"""
    result = await UserService.get_user_by_username(username)

    if not result["success"]:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_client_error_message(result.get("message"), "获取用户信息失败")
        )

    return {
        "code": 200,
        "message": result["message"],
        "data": _public_user(result["user"])
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
            detail=_client_error_message(result.get("message"), "更新用户信息失败")
        )

    return {
        "code": 200,
        "message": result["message"],
        "data": _public_user(result.get("user"))
    }


@router.delete("/{user_id}", summary="删除用户")
async def delete_user(user_id: int):
    """删除用户（软删除）"""
    result = await UserService.delete_user(user_id)

    if not result["success"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_client_error_message(result.get("message"), "删除用户失败")
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
            detail=_client_error_message(result.get("message"), "登录失败")
        )

    return {
        "code": 200,
        "message": result["message"],
        "data": _public_user(result["user"])
    }
