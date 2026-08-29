from __future__ import annotations

import logging
import os
from pathlib import Path

import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .storage import (
    ImageData,
    StorageError,
    get_image_path,
    get_post_detail,
    list_post_summaries,
    save_post,
)


LOGGER = logging.getLogger(__name__)
WEB_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = WEB_DIR / "templates"
STATIC_DIR = WEB_DIR / "static"

app = FastAPI(title="Alarkive Publisher", version="0.1.0")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
templates = Jinja2Templates(directory=TEMPLATES_DIR)


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
    return templates.TemplateResponse(
        request=request,
        name="posts.html",
        context={"posts": list_post_summaries()},
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
        if any(Path(image.filename or "").suffix.lower() != ".png" for image in uploaded):
            raise StorageError("当前版本仅支持 PNG 图片。")

        image_data: list[ImageData] = []
        for image in uploaded:
            try:
                data = await image.read()
            except Exception as exc:
                raise StorageError(f"读取图片失败：{image.filename}") from exc
            image_data.append(ImageData(filename=image.filename or "image.png", data=data))

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
        post = get_post_detail(post_id)
    except StorageError:
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


def main() -> None:
    host = os.environ.get("ALARKIVE_WEB_HOST", "127.0.0.1")
    port = int(os.environ.get("ALARKIVE_WEB_PORT", "8000"))
    print(f"Alarkive Publisher Web Content Manager v0.1.0")
    print(f"Open http://{host}:{port}")
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
