(function () {
    "use strict";

    const input = document.getElementById("images");
    const zone = document.getElementById("upload-zone");
    const grid = document.getElementById("preview-grid");
    const error = document.getElementById("upload-error");
    const form = document.getElementById("post-form");
    if (!input || !zone || !grid || !form) return;

    let selectedFiles = [];
    let draggedIndex = null;

    function showError(message) {
        if (!error) return;
        error.textContent = message;
        error.hidden = !message;
    }

    function validPng(file) {
        return file && /\.png$/i.test(file.name);
    }

    function addFiles(fileList) {
        const files = Array.from(fileList || []);
        if (!files.length) return;
        const invalid = files.find((file) => !validPng(file));
        if (invalid) {
            showError("当前版本仅支持 PNG 图片。未添加 " + invalid.name + "。");
            return;
        }
        selectedFiles = selectedFiles.concat(files);
        showError("");
        render();
        syncInput();
    }

    function moveFile(from, to) {
        if (to < 0 || to >= selectedFiles.length || from === to) return;
        const next = selectedFiles.slice();
        const [file] = next.splice(from, 1);
        next.splice(to, 0, file);
        selectedFiles = next;
        render();
        syncInput();
    }

    function render() {
        grid.replaceChildren();
        selectedFiles.forEach((file, index) => {
            const card = document.createElement("div");
            card.className = "preview-card";
            card.draggable = true;
            card.dataset.index = String(index);
            card.title = "拖动调整顺序";

            const image = document.createElement("img");
            image.src = URL.createObjectURL(file);
            image.alt = file.name;
            image.onload = () => URL.revokeObjectURL(image.src);

            const number = document.createElement("span");
            number.className = "preview-number";
            number.textContent = String(index + 1).padStart(2, "0");

            const name = document.createElement("span");
            name.className = "preview-name";
            name.textContent = file.name;

            const controls = document.createElement("div");
            controls.className = "preview-controls";
            controls.appendChild(sortButton("↑", "上移", () => moveFile(index, index - 1)));
            controls.appendChild(sortButton("↓", "下移", () => moveFile(index, index + 1)));

            card.append(image, number, name, controls);
            card.addEventListener("dragstart", () => {
                draggedIndex = index;
                card.classList.add("dragging");
            });
            card.addEventListener("dragend", () => {
                draggedIndex = null;
                card.classList.remove("dragging");
            });
            card.addEventListener("dragover", (event) => event.preventDefault());
            card.addEventListener("drop", (event) => {
                event.preventDefault();
                if (draggedIndex !== null) moveFile(draggedIndex, index);
            });
            grid.appendChild(card);
        });
    }

    function sortButton(text, label, action) {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "sort-button";
        button.textContent = text;
        button.title = label;
        button.setAttribute("aria-label", label);
        button.addEventListener("click", action);
        return button;
    }

    function syncInput() {
        // DataTransfer lets the multipart request follow the visible order.
        try {
            const transfer = new DataTransfer();
            selectedFiles.forEach((file) => transfer.items.add(file));
            input.files = transfer.files;
        } catch (_) {
            // The arrow controls still provide a usable fallback in browsers
            // that do not allow assigning a FileList.
        }
    }

    input.addEventListener("change", () => addFiles(input.files));
    zone.addEventListener("dragover", (event) => {
        event.preventDefault();
        zone.classList.add("drag-over");
    });
    zone.addEventListener("dragleave", () => zone.classList.remove("drag-over"));
    zone.addEventListener("drop", (event) => {
        event.preventDefault();
        zone.classList.remove("drag-over");
        addFiles(event.dataTransfer.files);
    });
    zone.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            input.click();
        }
    });
    form.addEventListener("submit", (event) => {
        if (!selectedFiles.length) {
            event.preventDefault();
            showError("至少需要上传 1 张 PNG 图片。");
            zone.focus();
            return;
        }
        syncInput();
    });
})();

(function () {
    "use strict";

    const panel = document.getElementById("publish-panel");
    if (!panel) return;

    const labels = {
        xiaohongshu: "小红书",
        baijiahao: "百家号",
        wechat: "微信公众号"
    };
    const statusLabels = {
        pending: "等待",
        running: "正在运行",
        waiting: "等待人工操作",
        ready: "已准备完成",
        failed: "失败"
    };
    const workflowLabels = {
        idle: "未启动",
        running: "正在运行",
        waiting: "等待人工操作",
        completed: "已完成",
        failed: "失败",
        interrupted: "已中断"
    };
    const statusElement = document.getElementById("content-published-status");
    const publishedAt = document.getElementById("published-at");
    const workflowStatus = document.getElementById("workflow-status");
    const workflowMessage = document.getElementById("workflow-message");
    const workflowError = document.getElementById("workflow-error");
    const workflowContinue = document.getElementById("workflow-continue");
    const actions = document.getElementById("publish-actions");

    function createForm(url, text, confirmText) {
        const form = document.createElement("form");
        form.method = "post";
        form.action = url;
        if (confirmText) {
            form.addEventListener("submit", (event) => {
                if (!window.confirm(confirmText)) event.preventDefault();
            });
        }
        const button = document.createElement("button");
        button.className = "button button-primary";
        button.type = "submit";
        button.textContent = text;
        form.appendChild(button);
        return form;
    }

    function renderActions(state) {
        if (!actions) return;
        actions.replaceChildren();
        const failedBrowserOpen =
            panel.dataset.browserOpen === "true" &&
            state.workflow &&
            state.workflow.status === "failed";

        // A failed workflow keeps its browser alive for inspection.  Until
        // that browser is explicitly closed, do not offer a second Publish
        // action that can only be rejected by the single-job guard.
        if (!state.published && failedBrowserOpen) {
            actions.appendChild(createForm(panel.dataset.closeUrl, "关闭浏览器"));
            return;
        }

        if (state.published) {
            const form = createForm(
                panel.dataset.resetUrl,
                "重新置为未发布",
                "仅将 Alarkive 中的状态改为“未发布”。\n\n不会撤回平台内容，也不会操作浏览器。"
            );
            form.querySelector("button").className = "button button-secondary";
            actions.appendChild(form);
        } else {
            actions.appendChild(createForm(
                panel.dataset.publishUrl,
                "发布",
                "开始发布准备流程？\n\nAlarkive 会自动填写三个平台，但不会点击平台真正的发布按钮。"
            ));
        }
        if (failedBrowserOpen) {
            actions.appendChild(createForm(panel.dataset.closeUrl, "关闭浏览器"));
        }
    }

    function renderContinue(state) {
        if (!workflowContinue) return;
        workflowContinue.replaceChildren();
        const workflow = state.workflow || {};
        if (workflow.status !== "waiting") return;

        if (state.browser_open === false) {
            const notice = document.createElement("p");
            notice.className = "workflow-error";
            notice.textContent = "共享浏览器已关闭，本次流程无法继续。请重新点击发布。";
            workflowContinue.appendChild(notice);
            return;
        }

        let text = "继续";
        if (workflow.current_platform === "xiaohongshu") text = "继续到百家号";
        if (workflow.current_platform === "baijiahao") text = "继续到微信公众号";
        if (workflow.current_platform === "wechat" && workflow.current_step === "ready") {
            text = "结束流程并关闭浏览器";
        }
        workflowContinue.appendChild(createForm(panel.dataset.continueUrl, text));
    }

    function render(state) {
        if (!state || !state.workflow) return;
        if (typeof state.browser_open === "boolean") {
            panel.dataset.browserOpen = state.browser_open ? "true" : "false";
        }
        if (statusElement) {
            statusElement.textContent = state.published ? "✓ 已发布" : "● 未发布";
            statusElement.className = "status-badge " + (state.published ? "status-published" : "status-unpublished");
        }
        if (publishedAt) publishedAt.textContent = state.published_at || "";
        if (workflowStatus) workflowStatus.textContent = workflowLabels[state.workflow.status] || state.workflow.status;
        if (workflowMessage) workflowMessage.textContent = state.workflow.message || "";
        if (workflowError) workflowError.textContent = state.workflow.error ? state.workflow.error.message : "";
        Object.keys(labels).forEach((platform) => {
            const element = panel.querySelector('[data-platform-status="' + platform + '"]');
            if (!element) return;
            const platformState = state.workflow.platforms[platform] || {};
            element.textContent = statusLabels[platformState.status] || platformState.status || "等待";
            element.dataset.status = platformState.status || "pending";
        });
        renderActions(state);
        renderContinue(state);
    }

    async function poll() {
        try {
            const response = await fetch(panel.dataset.stateUrl, { cache: "no-store" });
            if (!response.ok) throw new Error("发布状态暂时无法读取");
            render(await response.json());
        } catch (error) {
            if (workflowMessage) workflowMessage.textContent = error.message;
        }
    }

    poll();
    window.setInterval(poll, 1000);
})();
