(function () {
    "use strict";

    const input = document.getElementById("images");
    const zone = document.getElementById("upload-zone");
    const grid = document.getElementById("preview-grid");
    const error = document.getElementById("upload-error");
    const form = document.getElementById("post-form");
    const baijiahaoBody = document.getElementById("baijiahao_body");
    const markerStatus = document.getElementById("baijiahao-marker-status");
    const promptButton = document.getElementById("copy-ai-prompt");
    const promptStatus = document.getElementById("prompt-status");
    const promptFallback = document.getElementById("ai-prompt-fallback");
    if (!input || !zone || !grid || !form) return;

    let selectedFiles = [];
    let draggedIndex = null;
    const maxImageCount = 20;

    function markerIndexes(text) {
        const indexes = [];
        String(text || "").split(/\r?\n/).forEach((line) => {
            const match = line.match(/^[ \t]*\[\[image:(\d+)\]\][ \t]*$/);
            if (match) indexes.push(Number(match[1]));
        });
        return indexes;
    }

    function uniqueNumbers(values) {
        return Array.from(new Set(values));
    }

    function renderMarkerStatus() {
        if (!markerStatus || !baijiahaoBody) return;
        const count = selectedFiles.length;
        if (!count) {
            markerStatus.textContent = "请先添加图片，再校验图片占位符。";
            markerStatus.className = "marker-status warning";
            return;
        }

        const indexes = markerIndexes(baijiahaoBody.value);
        if (!indexes.length) {
            markerStatus.textContent = "未使用图片占位符；发布时将按原始顺序把全部图片追加到正文末尾。";
            markerStatus.className = "marker-status";
            return;
        }

        const invalid = uniqueNumbers(indexes.filter((index) => index < 1 || index > count));
        const duplicates = uniqueNumbers(
            indexes.filter((index, position) => indexes.indexOf(index) !== position)
        );
        const used = uniqueNumbers(indexes.filter((index) => index >= 1 && index <= count));
        const unused = [];
        for (let index = 1; index <= count; index += 1) {
            if (!used.includes(index)) unused.push(index);
        }

        const messages = [];
        if (invalid.length) {
            messages.push("⚠ 无效占位符：" + invalid.map((index) => "[[image:" + index + "]]" ).join("、"));
        }
        if (duplicates.length) {
            messages.push("⚠ 图片 " + duplicates.join("、") + " 被重复引用");
        }
        messages.push((invalid.length || duplicates.length || unused.length ? "已引用 " : "✓ 已引用 ") + used.length + " / " + count + " 张图片");
        if (unused.length) messages.push("未使用：图片 " + unused.join("、"));
        markerStatus.textContent = messages.join("；");
        markerStatus.className = "marker-status" + (invalid.length || duplicates.length || unused.length ? " warning" : "");
    }

    function buildAiPrompt() {
        const count = selectedFiles.length;
        const markerList = Array.from(
            { length: count },
            (_, index) => "[[image:" + (index + 1) + "]]"
        ).join("\n");
        return [
            "请根据我们当前对话中已经完成的研究、底稿和配图，生成最终的百家号正文。",
            "",
            "要求：",
            "1. 直接输出可以发布的百家号正文，不要解释写作过程，不要输出标题，不要输出“以下是正文”之类的说明，不要使用代码块。",
            "2. 文风通俗易懂、自然流畅、信息完整，不要过度书面化，不要写成论文或报告。",
            "3. 当前一共有 " + count + " 张配图。图片编号严格对应 Alarkive Publisher 当前图片列表顺序：",
            markerList,
            "请根据当前对话中这些图片的实际内容，以及文章上下文，把图片安排在最适合阅读的位置。优先使用全部图片，不要把所有图片集中放在文章末尾。",
            "",
            "4. 只能使用以上图片占位符。不要引用不存在的图片编号，不要重复使用同一张图片。每张图片最多使用一次。",
            "",
            "5. 每个图片占位符必须单独占一行。",
            "例如：",
            "上一段正文。",
            "[[image:1]]",
            "下一段正文。",
            "错误：上一段正文。[[image:1]]下一段正文。",
            "6. 不要修改图片占位符格式。必须严格使用：",
            markerList,
            "不要输出：",
            "[image:1]",
            "![image:1]",
            "{{image:1}}",
            "image:1",
            "7. 除图片占位符外，其余正文正常输出即可，可以使用普通 Markdown 的段落、小标题、加粗、列表。",
            "8. 不要在正文中解释“下面插入图片”“这里放一张图”“如图所示”等提示语，图片应自然融入阅读流程。"
        ].join("\n");
    }

    function fallbackCopy(text) {
        const textarea = document.createElement("textarea");
        textarea.value = text;
        textarea.setAttribute("readonly", "");
        textarea.style.position = "fixed";
        textarea.style.opacity = "0";
        document.body.appendChild(textarea);
        textarea.select();
        let copied = false;
        try {
            copied = document.execCommand("copy");
        } catch (_) {
            copied = false;
        }
        textarea.remove();
        return copied;
    }

    function showPromptStatus(message, isError) {
        if (!promptStatus) return;
        promptStatus.textContent = message;
        promptStatus.className = "prompt-status" + (isError ? " error" : "");
    }

    function showPromptFallback(prompt) {
        if (!promptFallback) return;
        promptFallback.value = prompt;
        promptFallback.hidden = false;
        promptFallback.focus();
        promptFallback.select();
    }

    async function copyAiPrompt() {
        if (!selectedFiles.length) {
            showPromptStatus("请先添加图片，再生成兼容 Alarkive 的 Prompt。", true);
            renderMarkerStatus();
            return;
        }
        const prompt = buildAiPrompt();
        let copied = false;
        try {
            if (navigator.clipboard && navigator.clipboard.writeText) {
                await navigator.clipboard.writeText(prompt);
                copied = true;
            }
        } catch (_) {
            copied = false;
        }
        if (!copied) copied = fallbackCopy(prompt);
        if (copied) {
            showPromptStatus("✓ Prompt 已复制", false);
            if (promptFallback) promptFallback.hidden = true;
            const original = promptButton.textContent;
            promptButton.textContent = "✓ 已复制";
            window.setTimeout(() => {
                promptButton.textContent = original;
            }, 2500);
        } else {
            showPromptFallback(prompt);
            showPromptStatus("复制失败，请手动复制下方 Prompt。", true);
        }
    }

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
        const invalid = files.filter((file) => !validPng(file));
        const valid = files.filter(validPng);
        if (!valid.length) {
            showError("已忽略不支持的文件：" + invalid.map((file) => file.name).join("、") + "。");
            return;
        }
        if (selectedFiles.length + valid.length > maxImageCount) {
            showError("图片数量超过限制，单个任务最多上传 " + maxImageCount + " 张图片。本次选择未添加。");
            return;
        }
        selectedFiles = selectedFiles.concat(valid);
        if (invalid.length) {
            showError("已忽略不支持的文件：" + invalid.map((file) => file.name).join("、") + "。");
        } else {
            showError("");
        }
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
        renderMarkerStatus();
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
    if (baijiahaoBody) {
        baijiahaoBody.addEventListener("input", renderMarkerStatus);
        baijiahaoBody.addEventListener("change", renderMarkerStatus);
    }
    if (promptButton) promptButton.addEventListener("click", copyAiPrompt);
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

    renderMarkerStatus();
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
        const publisherActive = state.publisher_active === true;

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
        } else if (publisherActive) {
            const notice = document.createElement("span");
            notice.className = "workflow-action-note";
            notice.textContent = "发布流程进行中";
            actions.appendChild(notice);
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
