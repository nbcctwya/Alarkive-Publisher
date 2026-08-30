from __future__ import annotations

import logging
import os
from pathlib import Path

import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .. import __version__
from ..content import ContentError
from .publish_manager import (
    PublishManager,
    PublisherAlreadyPublishedError,
    PublisherBusyError,
    PublishManagerError,
    PublisherNotWaitingError,
    PublisherUnsupportedPlatformError,
)
from .publish_state import PublishStateError, mark_unpublished
from .storage import (
    ImageData,
    StorageError,
    get_image_path,
    get_post_folder,
    get_post_detail,
    list_post_summaries,
    MAX_IMAGE_COUNT,
    MAX_IMAGE_SIZE_BYTES,
    MAX_TOTAL_IMAGE_SIZE_BYTES,
    save_post,
)


LOGGER = logging.getLogger(__name__)
WEB_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = WEB_DIR / "templates"
STATIC_DIR = WEB_DIR / "static"

app = FastAPI(title="Alarkive Publisher", version=__version__)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
templates = Jinja2Templates(directory=TEMPLATES_DIR)
publish_manager = PublishManager()


def _load_web_post(post_id: str) -> dict:
    post = get_post_detail(post_id)
    post["publish_state"] = publish_manager.reconcile_post_if_needed(post_id)
    post["browser_open"] = publish_manager.browser_open_for(post_id)
    post["publisher_active"] = publish_manager.has_active_workflow()
    return post


def _render_detail_error(
    request: Request,
    post_id: str,
    message: str,
    *,
    status_code: int = 409,
) -> HTMLResponse:
    try:
        post = _load_web_post(post_id)
    except (StorageError, PublishStateError):
        return templates.TemplateResponse(
            request=request,
            name="not_found.html",
            context={"message": "任务不存在，或任务文件已损坏。"},
            status_code=404,
        )
    post["action_error"] = message
    return templates.TemplateResponse(
        request=request,
        name="detail.html",
        context={"post": post},
        status_code=status_code,
    )


def _form_values(
    name: str,
    xiaohongshu_title: str,
    xiaohongshu_body: str,
    baijiahao_title: str,
    baijiahao_body: str,
    wechat_title: str,
    wechat_body: str,
) -> dict[str, str]:
    return {
        "name": name,
        "xiaohongshu_title": xiaohongshu_title,
        "xiaohongshu_body": xiaohongshu_body,
        "baijiahao_title": baijiahao_title,
        "baijiahao_body": baijiahao_body,
        "wechat_title": wechat_title,
        "wechat_body": wechat_body,
    }


def _render_create(
    request: Request,
    *,
    form: dict[str, str] | None = None,
    error: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    response = templates.TemplateResponse(
        request=request,
        name="create.html",
        context={"form": form or {}, "error": error},
    )
    response.status_code = status_code
    return response


@app.get("/", name="home")
async def home() -> RedirectResponse:
    return RedirectResponse(url="/posts", status_code=303)


@app.get("/posts", response_class=HTMLResponse, name="post_list")
async def post_list(request: Request) -> HTMLResponse:
    posts = list_post_summaries()
    for post in posts:
        try:
            post_state = publish_manager.reconcile_post_if_needed(post["id"])
            post["published"] = post_state["published"]
            post["published_at"] = post_state["published_at"]
        except (StorageError, PublishStateError):
            # A malformed runtime sidecar must not hide a valid content list.
            pass
    return templates.TemplateResponse(
        request=request,
        name="posts.html",
        context={"posts": posts},
    )


@app.get("/posts/new", response_class=HTMLResponse, name="new_post")
async def new_post(request: Request) -> HTMLResponse:
    return _render_create(request)


@app.post("/posts", name="create_post")
async def create_post(
    request: Request,
    name: str = Form(default=""),
    xiaohongshu_title: str = Form(default=""),
    xiaohongshu_body: str = Form(default=""),
    baijiahao_title: str = Form(default=""),
    baijiahao_body: str = Form(default=""),
    wechat_title: str = Form(default=""),
    wechat_body: str = Form(default=""),
    images: list[UploadFile] | None = File(default=None),
) -> Response:
    form = _form_values(
        name,
        xiaohongshu_title,
        xiaohongshu_body,
        baijiahao_title,
        baijiahao_body,
        wechat_title,
        wechat_body,
    )
    uploaded = [image for image in (images or []) if image.filename]
    try:
        if not name.strip():
            raise StorageError("任务名称不能为空。")
        required_fields = (
            ("小红书标题", xiaohongshu_title),
            ("小红书正文", xiaohongshu_body),
            ("百家号标题", baijiahao_title),
            ("百家号正文", baijiahao_body),
            ("微信公众号标题", wechat_title),
            ("微信公众号正文", wechat_body),
        )
        for label, value in required_fields:
            if not value.strip():
                raise StorageError(f"{label}不能为空。")
        if not uploaded:
            raise StorageError("至少需要上传 1 张 PNG 图片。")

        unsupported = [
            image.filename or "未命名文件"
            for image in uploaded
            if Path(image.filename or "").suffix.lower() != ".png"
        ]
        valid_uploaded = [
            image
            for image in uploaded
            if Path(image.filename or "").suffix.lower() == ".png"
        ]
        if unsupported and not valid_uploaded:
            names = "、".join(unsupported)
            raise StorageError(
                f"已忽略不支持的文件：{names}。至少需要上传 1 张 PNG 图片。"
            )
        if len(valid_uploaded) > MAX_IMAGE_COUNT:
            raise StorageError(
                f"图片数量超过限制，单个任务最多上传 {MAX_IMAGE_COUNT} 张图片。"
            )

        image_data: list[ImageData] = []
        total_read = 0
        for image in valid_uploaded:
            try:
                remaining_total = MAX_TOTAL_IMAGE_SIZE_BYTES - total_read
                read_limit = min(MAX_IMAGE_SIZE_BYTES, remaining_total) + 1
                data = await image.read(read_limit)
            except Exception as exc:
                raise StorageError(f"读取图片失败：{image.filename}") from exc
            filename = image.filename or "image.png"
            if len(data) > MAX_IMAGE_SIZE_BYTES:
                raise StorageError(
                    f"图片过大：{filename}。单张图片不能超过 "
                    f"{MAX_IMAGE_SIZE_BYTES // (1024 * 1024)} MB。"
                )
            total_read += len(data)
            if total_read > MAX_TOTAL_IMAGE_SIZE_BYTES:
                raise StorageError(
                    "图片总大小超过限制：单个任务的图片总大小不能超过 "
                    f"{MAX_TOTAL_IMAGE_SIZE_BYTES // (1024 * 1024)} MB。"
                )
            image_data.append(ImageData(filename=filename, data=data))

        saved = save_post(
            name=name,
            titles={
                "xiaohongshu": xiaohongshu_title,
                "baijiahao": baijiahao_title,
                "wechat": wechat_title,
            },
            bodies={
                "xiaohongshu": xiaohongshu_body,
                "baijiahao": baijiahao_body,
                "wechat": wechat_body,
            },
            images=image_data,
        )
    except StorageError as exc:
        return _render_create(request, form=form, error=str(exc), status_code=400)
    except Exception:
        LOGGER.exception("创建图文任务失败")
        return _render_create(
            request,
            form=form,
            error="保存图文失败，请检查终端日志后重试。",
            status_code=500,
        )
    finally:
        for image in images or []:
            await image.close()

    return RedirectResponse(url=f"/posts/{saved.id}", status_code=303)


@app.get("/posts/{post_id}/images/{image_name}", name="image_file")
async def image_file(post_id: str, image_name: str) -> FileResponse:
    try:
        image_path = get_image_path(post_id, image_name)
    except StorageError:
        # Avoid exposing whether arbitrary paths exist outside a valid package.
        raise HTTPException(status_code=404, detail="图片不存在")
    return FileResponse(path=image_path, media_type="image/png")


@app.get("/posts/{post_id}", response_class=HTMLResponse, name="post_detail")
async def post_detail(request: Request, post_id: str) -> HTMLResponse:
    try:
        post = _load_web_post(post_id)
    except (StorageError, PublishStateError):
        return templates.TemplateResponse(
            request=request,
            name="not_found.html",
            context={"message": "任务不存在，或任务文件已损坏。"},
            status_code=404,
        )
    return templates.TemplateResponse(
        request=request,
        name="detail.html",
        context={"post": post},
    )


@app.post("/posts/{post_id}/publish", name="publish_post")
async def publish_post(request: Request, post_id: str) -> Response:
    try:
        publish_manager.start_publish(post_id)
    except PublisherBusyError as exc:
        return _render_detail_error(request, post_id, str(exc))
    except PublisherAlreadyPublishedError as exc:
        return _render_detail_error(request, post_id, str(exc))
    except (StorageError, ContentError, PublishStateError) as exc:
        return _render_detail_error(request, post_id, str(exc), status_code=400)
    except Exception:
        LOGGER.exception("启动发布准备流程失败：%s", post_id)
        return _render_detail_error(
            request,
            post_id,
            "无法启动发布准备流程，请检查终端日志。",
            status_code=500,
        )
    return RedirectResponse(url=f"/posts/{post_id}", status_code=303)


@app.post("/posts/{post_id}/publish/continue", name="continue_publish")
async def continue_publish(request: Request, post_id: str) -> Response:
    try:
        publish_manager.continue_publish(post_id)
    except PublisherNotWaitingError as exc:
        return _render_detail_error(request, post_id, str(exc))
    except (StorageError, PublishStateError) as exc:
        return _render_detail_error(request, post_id, str(exc), status_code=400)
    return RedirectResponse(url=f"/posts/{post_id}", status_code=303)


@app.post("/posts/{post_id}/mark-unpublished", name="mark_unpublished_post")
async def mark_unpublished_post(request: Request, post_id: str) -> Response:
    try:
        # This action intentionally calls the pure local-marker operation only.
        # It never consults or changes the active Publisher job.
        post_folder = get_post_folder(post_id)
        mark_unpublished(post_folder)
    except (StorageError, PublishStateError) as exc:
        return _render_detail_error(request, post_id, str(exc), status_code=400)
    return RedirectResponse(url=f"/posts/{post_id}", status_code=303)


@app.post("/posts/{post_id}/publish/close-browser", name="close_publish_browser")
async def close_publish_browser(request: Request, post_id: str) -> Response:
    try:
        publish_manager.close_browser(post_id)
    except (PublishManagerError, StorageError, PublishStateError) as exc:
        return _render_detail_error(request, post_id, str(exc))
    return RedirectResponse(url=f"/posts/{post_id}", status_code=303)


@app.post("/posts/{post_id}/publish/{platform}", name="publish_platform")
async def publish_platform(request: Request, post_id: str, platform: str) -> Response:
    """Start a single-platform preparation without changing the all route."""

    try:
        publish_manager.start_platform_publish(post_id, platform)
    except PublisherUnsupportedPlatformError as exc:
        return _render_detail_error(request, post_id, str(exc), status_code=400)
    except PublisherBusyError as exc:
        return _render_detail_error(request, post_id, str(exc))
    except PublisherAlreadyPublishedError as exc:
        return _render_detail_error(request, post_id, str(exc))
    except (StorageError, ContentError, PublishStateError) as exc:
        return _render_detail_error(request, post_id, str(exc), status_code=400)
    except Exception:
        LOGGER.exception("启动单平台发布准备流程失败：%s/%s", post_id, platform)
        return _render_detail_error(
            request,
            post_id,
            "无法启动单平台发布准备流程，请检查终端日志。",
            status_code=500,
        )
    return RedirectResponse(url=f"/posts/{post_id}", status_code=303)


@app.get("/api/posts/{post_id}/publish-state", name="publish_state_api")
async def publish_state_api(post_id: str) -> dict:
    try:
        state = publish_manager.reconcile_post_if_needed(post_id)
        state["browser_open"] = publish_manager.browser_open_for(post_id)
        # This is process state, not inferred from the persisted workflow
        # status. It closes the gap between reset-local-marker and the single
        # active-browser guard.
        state["publisher_active"] = publish_manager.has_active_workflow()
        return state
    except (StorageError, PublishStateError):
        raise HTTPException(status_code=404, detail="发布状态不存在")


def main() -> None:
    host = os.environ.get("ALARKIVE_WEB_HOST", "127.0.0.1")
    port = int(os.environ.get("ALARKIVE_WEB_PORT", "8000"))
    print(f"Alarkive Publisher Web Content Manager v{__version__}")
    print(f"Open http://{host}:{port}")
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
