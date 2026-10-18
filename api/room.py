import time
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from log.logger import log_api_request, log_exception, resolve_request_id
from service.room import (
    create_room, join_room, leave_room,
    get_room_info, get_public_room_list,
    RoomServiceError, RoomNotFoundError, UserNotInRoomError,
    InvalidDomainError, RoomAlreadyExistsError
)

router = APIRouter(prefix="/api/room", tags=["房间管理"])


class CreateRoomRequest(BaseModel):
    """创建房间请求"""
    user_id: str
    domain_enum: int = 1  # 1: 公共房间, 2: 私有房间


class JoinRoomRequest(BaseModel):
    """加入房间请求"""
    user_id: str
    room_code: str


class RoomInfoResponse(BaseModel):
    """房间信息响应"""
    room_code: str
    domain: str
    creator: str
    members: List[str]
    online: List[str]
    created_at: float


class PublicRoomListResponse(BaseModel):
    """公共房间列表响应"""
    rooms: List[RoomInfoResponse]


class ErrorResponse(BaseModel):
    """错误响应模型"""
    error_code: str
    message: str
    detail: Optional[str] = None


# 自定义异常处理器
def handle_service_exception(e: Exception, request: Request = None) -> HTTPException:
    """处理服务层异常并转换为HTTP异常

    安全约定：已知业务异常只回传 error_code 与面向用户的 message；
    未预期的异常不把 ``str(e)`` 返回给客户端，只回传通用文案 + request_id，
    完整异常（type/message/traceback）通过 log_exception 写服务端日志。
    """
    if isinstance(e, RoomNotFoundError):
        return HTTPException(status_code=404, detail={
            "error_code": e.error_code,
            "message": e.message
        })
    elif isinstance(e, (InvalidDomainError, UserNotInRoomError)):
        return HTTPException(status_code=400, detail={
            "error_code": e.error_code,
            "message": e.message
        })
    elif isinstance(e, RoomAlreadyExistsError):
        return HTTPException(status_code=409, detail={
            "error_code": e.error_code,
            "message": e.message
        })
    elif isinstance(e, RoomServiceError):
        return HTTPException(status_code=500, detail={
            "error_code": e.error_code,
            "message": e.message
        })
    else:
        request_id = resolve_request_id(request)
        extra_info = {"request_id": request_id}
        if request is not None:
            extra_info["path"] = getattr(getattr(request, "url", None), "path", "")
            extra_info["method"] = getattr(request, "method", "")
        log_exception(
            e,
            f"房间服务未预期异常 - {type(e).__name__} - request_id:{request_id}",
            extra_info,
        )
        return HTTPException(status_code=500, detail={
            "error_code": "INTERNAL_ERROR",
            "message": "服务器内部错误",
            "request_id": request_id,
        })


@router.post("/create", response_model=dict, responses={
    400: {"model": ErrorResponse, "description": "请求参数错误"},
    500: {"model": ErrorResponse, "description": "服务器内部错误"}
})
async def create_room_api(request: Request, create_request: CreateRoomRequest):
    """
    创建房间
    
    Args:
        request: FastAPI请求对象
        create_request: 创建房间请求参数
    
    Returns:
        {"room_code": "房间码", "message": "创建成功"}
    """
    start_time = time.time()
    try:
        # 记录API请求
        client_ip = request.client.host if request.client else "unknown"

        room_code = create_room(
            create_request.user_id,
            domain_enum=create_request.domain_enum,
            request_id=resolve_request_id(request),
        )

        # 记录成功请求
        duration = time.time() - start_time
        log_api_request("POST", "/api/room/create", 200, client_ip, duration)

        return {
            "room_code": room_code,
            "message": "房间创建成功"
        }
    except Exception as e:
        # 记录错误请求
        duration = time.time() - start_time
        client_ip = request.client.host if request.client else "unknown"
        status_code = 500
        if isinstance(e, InvalidDomainError):
            status_code = 400

        log_api_request("POST", "/api/room/create", status_code, client_ip, duration)
        log_exception(e, "创建房间API失败", {
            "user_id": create_request.user_id,
            "domain_enum": create_request.domain_enum
        })

        raise handle_service_exception(e, request)


@router.post("/join", response_model=dict, responses={
    404: {"model": ErrorResponse, "description": "房间不存在"},
    500: {"model": ErrorResponse, "description": "服务器内部错误"}
})
async def join_room_api(request: Request, join_request: JoinRoomRequest):
    """
    加入房间
    
    Args:
        request: FastAPI请求对象
        join_request: 加入房间请求参数
    
    Returns:
        {"message": "加入成功", "room_info": 房间信息}
    """
    start_time = time.time()
    try:
        client_ip = request.client.host if request.client else "unknown"

        join_room(
            join_request.user_id,
            join_request.room_code,
            request_id=resolve_request_id(request),
        )
        room_info = get_room_info(join_request.room_code)

        duration = time.time() - start_time
        log_api_request("POST", "/api/room/join", 200, client_ip, duration)

        return {
            "message": "加入房间成功",
            "room_info": {
                "room_code": join_request.room_code,
                "domain": room_info["domain"],
                "creator": room_info["creator"],
                "members_count": len(room_info["members"]),
                "online_count": len(room_info["online"]),
                "created_at": room_info["created_at"]
            }
        }
    except Exception as e:
        duration = time.time() - start_time
        client_ip = request.client.host if request.client else "unknown"
        status_code = 500
        if isinstance(e, RoomNotFoundError):
            status_code = 404

        log_api_request("POST", "/api/room/join", status_code, client_ip, duration)
        log_exception(e, "加入房间API失败", {
            "user_id": join_request.user_id,
            "room_code": join_request.room_code
        })

        raise handle_service_exception(e, request)


@router.post("/leave", response_model=dict, responses={
    404: {"model": ErrorResponse, "description": "房间不存在或用户不在房间"},
    500: {"model": ErrorResponse, "description": "服务器内部错误"}
})
async def leave_room_api(request: Request, leave_request: JoinRoomRequest):
    """
    离开房间
    
    Args:
        request: FastAPI请求对象
        leave_request: 离开房间请求参数
    
    Returns:
        {"message": "离开成功"}
    """
    start_time = time.time()
    try:
        client_ip = request.client.host if request.client else "unknown"

        leave_room(
            leave_request.user_id,
            leave_request.room_code,
            request_id=resolve_request_id(request),
        )

        duration = time.time() - start_time
        log_api_request("POST", "/api/room/leave", 200, client_ip, duration)

        return {"message": "离开房间成功"}
    except Exception as e:
        duration = time.time() - start_time
        client_ip = request.client.host if request.client else "unknown"
        status_code = 500
        if isinstance(e, (RoomNotFoundError, UserNotInRoomError)):
            status_code = 404

        log_api_request("POST", "/api/room/leave", status_code, client_ip, duration)
        log_exception(e, "离开房间API失败", {
            "user_id": leave_request.user_id,
            "room_code": leave_request.room_code
        })

        raise handle_service_exception(e, request)


@router.get("/info/{room_code}", response_model=RoomInfoResponse, responses={
    404: {"model": ErrorResponse, "description": "房间不存在"},
    500: {"model": ErrorResponse, "description": "服务器内部错误"}
})
async def get_room_info_api(request: Request, room_code: str):
    """
    获取房间详细信息
    
    Args:
        request: FastAPI请求对象
        room_code: 房间码
    
    Returns:
        房间详细信息
    """
    start_time = time.time()
    try:
        client_ip = request.client.host if request.client else "unknown"

        room_info = get_room_info(room_code)

        duration = time.time() - start_time
        log_api_request("GET", f"/api/room/info/{room_code}", 200, client_ip, duration)

        return RoomInfoResponse(
            room_code=room_code,
            domain=room_info["domain"],
            creator=room_info["creator"],
            members=list(room_info["members"]),
            online=list(room_info["online"]),
            created_at=room_info["created_at"]
        )
    except Exception as e:
        duration = time.time() - start_time
        client_ip = request.client.host if request.client else "unknown"
        status_code = 500
        if isinstance(e, RoomNotFoundError):
            status_code = 404

        log_api_request("GET", f"/api/room/info/{room_code}", status_code, client_ip, duration)
        log_exception(e, "获取房间信息API失败", {"room_code": room_code})

        raise handle_service_exception(e, request)


@router.get("/public/list", response_model=PublicRoomListResponse, responses={
    500: {"model": ErrorResponse, "description": "服务器内部错误"}
})
async def get_public_room_list_api(request: Request):
    """
    获取公共房间列表
    
    Args:
        request: FastAPI请求对象
    
    Returns:
        公共房间列表
    """
    start_time = time.time()
    try:
        client_ip = request.client.host if request.client else "unknown"

        rooms = get_public_room_list(request_id=resolve_request_id(request))
        room_responses = []
        for room_info in rooms:
            room_responses.append(RoomInfoResponse(
                room_code=room_info["room_code"],
                domain=room_info["domain"],
                creator=room_info["creator"],
                members=list(room_info["members"]),
                online=list(room_info["online"]),
                created_at=room_info["created_at"]
            ))

        duration = time.time() - start_time
        log_api_request("GET", "/api/room/public/list", 200, client_ip, duration)

        return PublicRoomListResponse(rooms=room_responses)
    except Exception as e:
        duration = time.time() - start_time
        client_ip = request.client.host if request.client else "unknown"

        log_api_request("GET", "/api/room/public/list", 500, client_ip, duration)
        log_exception(e, "获取公共房间列表API失败")

        raise handle_service_exception(e, request)
