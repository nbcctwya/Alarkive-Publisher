(function () {
    "use strict";

    const input = document.getElementById("images");
    const zone = document.getElementById("upload-zone");
    const grid = document.getElementById("preview-grid");
    const error = document.getElementById("upload-error");
    const form = document.getElementById("post-form");
    if (!input || !zone || !grid || !form) return;

    const publicLongBody = document.getElementById("public_long_body");
    const markerStatus = document.getElementById("public-long-marker-status");
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

    function uniqueNumbers(values) { return Array.from(new Set(values)); }

    function renderMarkerStatus() {
        if (!markerStatus || !publicLongBody) return;
        const count = selectedFiles.length;
        if (!count) {
            markerStatus.textContent = "请先添加图片，再校验图片占位符。";
            markerStatus.className = "marker-status warning";
            return;
        }
        const indexes = markerIndexes(publicLongBody.value);
        if (!indexes.length) {
            markerStatus.textContent = "未使用图片占位符；发布时将按原始顺序把全部图片追加到正文末尾。";
            markerStatus.className = "marker-status";
            return;
        }
        const invalid = uniqueNumbers(indexes.filter((index) => index < 1 || index > count));
        const duplicates = uniqueNumbers(indexes.filter((index, position) => indexes.indexOf(index) !== position));
        const used = uniqueNumbers(indexes.filter((index) => index >= 1 && index <= count));
        const unused = [];
        for (let index = 1; index <= count; index += 1) if (!used.includes(index)) unused.push(index);
        const messages = [];
        if (invalid.length) messages.push("⚠ 无效占位符：" + invalid.map((index) => "[[image:" + index + "]]" ).join("、"));
        if (duplicates.length) messages.push("⚠ 图片 " + duplicates.join("、") + " 被重复引用");
        messages.push((invalid.length || duplicates.length || unused.length ? "已引用 " : "✓ 已引用 ") + used.length + " / " + count + " 张图片");
        if (unused.length) messages.push("未使用：图片 " + unused.join("、"));
        markerStatus.textContent = messages.join("；");
        markerStatus.className = "marker-status" + (invalid.length || duplicates.length || unused.length ? " warning" : "");
    }

    function promptWithImages(intro, style, destination, marker) {
        const count = selectedFiles.length;
        const mapping = Array.from({length: count}, (_, index) => "第 " + (index + 1) + " 张图片 → [[image:" + (index + 1) + "]]" ).join("\n");
        const markers = Array.from({length: count}, (_, index) => "[[image:" + (index + 1) + "]]" ).join("\n");
        return [
            intro, "", "要求：",
            "1. 直接输出可以粘贴到 Alarkive Publisher 的" + destination + "正文，不要解释写作过程、不要输出标题、不要使用代码块。",
            "2. " + style,
            "3. 当前一共有 " + count + " 张配图。请根据图片实际内容和文章上下文安排图片。",
            "图片编号与占位符的对应关系：", mapping, "",
            "4. 只允许使用以下图片占位符，每张最多使用一次：", markers,
            "5. 每个占位符必须单独占一行，不要修改 [[image:N]] 格式。",
            "6. 不要在正文中解释图片位置；图片应自然融入阅读流程。"
        ].join("\n");
    }

    function buildPublicLongPrompt() {
        return promptWithImages(
            "请根据我们当前对话中已经完成的研究、底稿和配图，生成最终的公域长文正文。",
            "文风通俗易懂、自然流畅、信息完整，内容将同时用于百家号和今日头条文章；不要写成论文或报告。",
            "公域长文",
            true
        );
    }

    function buildWechatLongPrompt() {
        return promptWithImages(
            "请根据我们当前对话中已经完成的研究、底稿和配图，生成最终的微信长文正文。",
            "比公域文章更克制、更有文章感，逻辑完整，可以使用小标题并加入有依据的作者判断；保持普通读者可读，不要写成论文、流量文或营销软文。",
            "微信长文",
            true
        );
    }

    function buildWechatShortPrompt() {
        return [
            "请根据我们当前对话中已经完成的研究、底稿和配图，生成最终的微信图文 / 小绿书正文。", "",
            "要求：",
            "1. 直接输出可以发布的正文，不要解释写作过程，不要输出标题，不要使用代码块。",
            "2. 内容简洁、直观，开头直接进入主题；段落短，一段尽量只表达一个重点。",
            "3. 语气自然、克制、清晰，适合手机快速阅读，不要写成长篇文章或营销软文。",
            "4. 可以使用短句、分点和留白，但不要输出图片占位符。图片由 Alarkive Publisher 作为独立图文组处理。",
            "5. 最终正文必须可以直接复制到微信图文正文输入框。"
        ].join("\n");
    }

    function buildToutiaoShortPrompt() {
        return [
            "请根据我们当前对话中已经完成的研究、底稿和配图，生成最终的微头条正文。", "",
            "要求：",
            "1. 直接输出可以发布的微头条正文，不要解释写作过程，不要输出标题，不要使用代码块。",
            "2. 内容明显短于长文，更口语、更直接，开头快速进入信息核心，符合信息流阅读节奏。",
            "3. 可以适度突出话题性并促进评论互动，但不要夸张、编造或写成正式长文。",
            "4. 不要输出 [[image:N]] 图片占位符。图片由 Alarkive Publisher 作为独立图文组处理。",
            "5. 最终正文必须可以直接复制到微头条正文输入框。"
        ].join("\n");
    }

    function fallbackCopy(text) {
        const textarea = document.createElement("textarea");
        textarea.value = text; textarea.setAttribute("readonly", ""); textarea.style.position = "fixed"; textarea.style.opacity = "0";
        document.body.appendChild(textarea); textarea.select();
        let copied = false; try { copied = document.execCommand("copy"); } catch (_) { copied = false; }
        textarea.remove(); return copied;
    }

    function showPromptStatus(element, message, isError) {
        if (!element) return;
        element.textContent = message; element.className = "prompt-status" + (isError ? " error" : "");
    }

    function showPromptFallback(element, prompt) {
        if (!element) return;
        element.value = prompt; element.hidden = false; element.focus(); element.select();
    }

    async function copyPrompt(button, prompt, statusElement, fallbackElement, successMessage) {
        let copied = false;
        try {
            if (navigator.clipboard && navigator.clipboard.writeText) { await navigator.clipboard.writeText(prompt); copied = true; }
        } catch (_) { copied = false; }
        if (!copied) copied = fallbackCopy(prompt);
        if (copied) {
            showPromptStatus(statusElement, successMessage, false);
            if (fallbackElement) fallbackElement.hidden = true;
            const original = button.textContent; button.textContent = "✓ 已复制";
            window.setTimeout(() => { button.textContent = original; }, 2500);
        } else {
            showPromptFallback(fallbackElement, prompt);
            showPromptStatus(statusElement, "复制失败，请手动复制下方 Prompt。", true);
        }
    }

    function bindPrompt(buttonId, statusId, fallbackId, builder, successMessage, requiresImages) {
        const button = document.getElementById(buttonId);
        if (!button) return;
        const status = document.getElementById(statusId);
        const fallback = document.getElementById(fallbackId);
        button.addEventListener("click", async () => {
            if (requiresImages && !selectedFiles.length) {
                showPromptStatus(status, "请先添加图片，再生成兼容 Alarkive 的 Prompt。", true);
                renderMarkerStatus(); return;
            }
            await copyPrompt(button, builder(), status, fallback, successMessage);
        });
    }

    function showError(message) { if (error) { error.textContent = message; error.hidden = !message; } }
    function validPng(file) { return file && /\.png$/i.test(file.name); }

    function addFiles(fileList) {
        const files = Array.from(fileList || []); if (!files.length) return;
        const invalid = files.filter((file) => !validPng(file)); const valid = files.filter(validPng);
        if (!valid.length) { showError("已忽略不支持的文件：" + invalid.map((file) => file.name).join("、") + "。"); return; }
        if (selectedFiles.length + valid.length > maxImageCount) { showError("图片数量超过限制，单个任务最多上传 " + maxImageCount + " 张图片。本次选择未添加。"); return; }
        selectedFiles = selectedFiles.concat(valid);
        showError(invalid.length ? "已忽略不支持的文件：" + invalid.map((file) => file.name).join("、") + "。" : "");
        render(); syncInput();
    }

    function moveFile(from, to) {
        if (to < 0 || to >= selectedFiles.length || from === to) return;
        const next = selectedFiles.slice(); const file = next.splice(from, 1)[0]; next.splice(to, 0, file); selectedFiles = next; render(); syncInput();
    }

    function sortButton(text, label, action) {
        const button = document.createElement("button"); button.type = "button"; button.className = "sort-button"; button.textContent = text; button.title = label; button.setAttribute("aria-label", label); button.addEventListener("click", action); return button;
    }

    function render() {
        grid.replaceChildren();
        selectedFiles.forEach((file, index) => {
            const card = document.createElement("div"); card.className = "preview-card"; card.draggable = true; card.title = "拖动调整顺序";
            const image = document.createElement("img"); image.src = URL.createObjectURL(file); image.alt = file.name; image.onload = () => URL.revokeObjectURL(image.src);
            const number = document.createElement("span"); number.className = "preview-number"; number.textContent = String(index + 1).padStart(2, "0");
            const name = document.createElement("span"); name.className = "preview-name"; name.textContent = file.name;
            const controls = document.createElement("div"); controls.className = "preview-controls"; controls.append(sortButton("↑", "上移", () => moveFile(index, index - 1)), sortButton("↓", "下移", () => moveFile(index, index + 1)));
            card.append(image, number, name, controls);
            card.addEventListener("dragstart", () => { draggedIndex = index; card.classList.add("dragging"); });
            card.addEventListener("dragend", () => { draggedIndex = null; card.classList.remove("dragging"); });
            card.addEventListener("dragover", (event) => event.preventDefault());
            card.addEventListener("drop", (event) => { event.preventDefault(); if (draggedIndex !== null) moveFile(draggedIndex, index); });
            grid.appendChild(card);
        });
        renderMarkerStatus();
    }

    function syncInput() {
        try { const transfer = new DataTransfer(); selectedFiles.forEach((file) => transfer.items.add(file)); input.files = transfer.files; } catch (_) { /* visible controls remain usable */ }
    }

    input.addEventListener("change", () => addFiles(input.files));
    zone.addEventListener("dragover", (event) => { event.preventDefault(); zone.classList.add("drag-over"); });
    zone.addEventListener("dragleave", () => zone.classList.remove("drag-over"));
    zone.addEventListener("drop", (event) => { event.preventDefault(); zone.classList.remove("drag-over"); addFiles(event.dataTransfer.files); });
    zone.addEventListener("keydown", (event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); input.click(); } });
    if (publicLongBody) { publicLongBody.addEventListener("input", renderMarkerStatus); publicLongBody.addEventListener("change", renderMarkerStatus); }

    bindPrompt("copy-public-long-prompt", "public-long-prompt-status", "public-long-prompt-fallback", buildPublicLongPrompt, "✓ 公域长文 Prompt 已复制", true);
    bindPrompt("copy-wechat-long-prompt", "wechat-long-prompt-status", "wechat-long-prompt-fallback", buildWechatLongPrompt, "✓ 微信长文 Prompt 已复制", true);
    bindPrompt("copy-wechat-short-prompt", "wechat-short-prompt-status", "wechat-short-prompt-fallback", buildWechatShortPrompt, "✓ 微信图文 Prompt 已复制", false);
    bindPrompt("copy-toutiao-short-prompt", "toutiao-short-prompt-status", "toutiao-short-prompt-fallback", buildToutiaoShortPrompt, "✓ 微头条 Prompt 已复制", false);

    form.addEventListener("submit", (event) => { if (!selectedFiles.length) { event.preventDefault(); showError("至少需要上传 1 张 PNG 图片。"); zone.focus(); return; } syncInput(); });
    render();
})();

(function () {
    "use strict";
    const panel = document.getElementById("publish-panel");
    if (!panel) return;

    const labels = { baijiahao: "百家号", toutiao_article: "今日头条文章", wechat_article: "微信公众号长文", wechat_image: "微信图文", toutiao_micro: "微头条" };
    const statusLabels = { pending: "等待", running: "正在运行", waiting: "等待人工操作", ready: "已准备完成", failed: "失败" };
    const workflowLabels = { idle: "未启动", running: "正在运行", waiting: "等待人工操作", completed: "已完成", failed: "失败", interrupted: "已中断" };
    const statusElement = document.getElementById("content-published-status");
    const publishedAt = document.getElementById("published-at");
    const workflowStatus = document.getElementById("workflow-status");
    const workflowMessage = document.getElementById("workflow-message");
    const workflowError = document.getElementById("workflow-error");
    const workflowContinue = document.getElementById("workflow-continue");
    const actions = document.getElementById("publish-actions");
    const targetContainers = document.querySelectorAll("[data-platform-action]");

    function createForm(url, text, confirmText) {
        const form = document.createElement("form"); form.method = "post"; form.action = url;
        if (confirmText) form.addEventListener("submit", (event) => { if (!window.confirm(confirmText)) event.preventDefault(); });
        const button = document.createElement("button"); button.className = "button button-primary"; button.type = "submit"; button.textContent = text; form.appendChild(button); return form;
    }

    function renderActions(state) {
        if (!actions) return;
        actions.replaceChildren();
        const failedBrowserOpen = panel.dataset.browserOpen === "true" && state.workflow.status === "failed";
        const active = state.publisher_active === true;
        const available = panel.dataset.fullWorkflowAvailable !== "false";
        if (!state.published && failedBrowserOpen) { actions.appendChild(createForm(panel.dataset.closeUrl, "关闭浏览器")); return; }
        if (state.published) {
            const reset = createForm(panel.dataset.resetUrl, "重新置为未发布", "仅将 Alarkive 中的状态改为“未发布”。\n\n不会撤回平台内容，也不会操作浏览器。"); reset.querySelector("button").className = "button button-secondary"; actions.appendChild(reset);
        }
        if (active) {
            const notice = document.createElement("span"); notice.className = "workflow-action-note"; notice.textContent = "发布流程进行中"; actions.appendChild(notice);
        } else if (!state.published && available) {
            const all = createForm(panel.dataset.publishUrl, "发布全部", "开始准备当前任务中已有且已接入的发布平台？\n\nAlarkive 不会点击平台真正的发布按钮。"); all.querySelector("button").className = "button button-secondary"; actions.appendChild(all);
        } else if (!state.published) {
            const notice = document.createElement("span"); notice.className = "workflow-action-note"; notice.textContent = "当前任务包含内容，但没有已接入的可发布平台"; actions.appendChild(notice);
        }
        if (failedBrowserOpen) actions.appendChild(createForm(panel.dataset.closeUrl, "关闭浏览器"));
    }

    function renderTargetActions(state) {
        const active = state.publisher_active === true;
        targetContainers.forEach((container) => {
            container.replaceChildren();
            const target = container.dataset.platformAction;
            const available = container.dataset.contentAvailable === "true";
            const implemented = container.dataset.platformImplemented === "true";
            if (!available) { const note = document.createElement("span"); note.className = "workflow-action-note"; note.textContent = "无内容"; container.appendChild(note); return; }
            if (!implemented) { const note = document.createElement("span"); note.className = "workflow-action-note"; note.textContent = "Publisher 待接入"; container.appendChild(note); return; }
            if (active) { const note = document.createElement("span"); note.className = "workflow-action-note"; note.textContent = "发布流程进行中"; container.appendChild(note); return; }
            container.appendChild(createForm(container.dataset.publishUrl, "发布" + (labels[target] || target), "开始准备" + (labels[target] || target) + "内容？\n\nAlarkive 不会点击平台真正的发布按钮。"));
        });
    }

    function renderContinue(state) {
        if (!workflowContinue) return;
        workflowContinue.replaceChildren();
        const workflow = state.workflow || {};
        if (workflow.status !== "waiting") return;
        if (state.browser_open === false) { const notice = document.createElement("p"); notice.className = "workflow-error"; notice.textContent = "共享浏览器已关闭，本次流程无法继续。请重新点击发布。"; workflowContinue.appendChild(notice); return; }
        const targets = (panel.dataset.fullWorkflowTargets || "").split(",").filter(Boolean);
        const index = targets.indexOf(workflow.current_platform);
        const text = workflow.current_step === "ready" && (workflow.workflow_mode === "single" || index < 0 || index === targets.length - 1) ? "结束流程并关闭浏览器" : "继续到下一个发布平台";
        workflowContinue.appendChild(createForm(panel.dataset.continueUrl, text));
    }

    function render(state) {
        if (!state || !state.workflow) return;
        if (typeof state.browser_open === "boolean") panel.dataset.browserOpen = state.browser_open ? "true" : "false";
        if (statusElement) { statusElement.textContent = state.published ? "✓ 已发布" : "● 未发布"; statusElement.className = "status-badge " + (state.published ? "status-published" : "status-unpublished"); }
        if (publishedAt) publishedAt.textContent = state.published_at || "";
        if (workflowStatus) workflowStatus.textContent = workflowLabels[state.workflow.status] || state.workflow.status;
        if (workflowMessage) workflowMessage.textContent = state.workflow.message || "";
        if (workflowError) workflowError.textContent = state.workflow.error ? state.workflow.error.message : "";
        panel.querySelectorAll("[data-platform-status]").forEach((element) => {
            const target = element.dataset.platformStatus;
            if (element.dataset.platformAvailable === "false") { element.textContent = "无内容"; return; }
            if (element.dataset.platformImplemented === "false") { element.textContent = "待接入"; return; }
            const targetState = (state.workflow.platforms || {})[target] || {};
            element.textContent = statusLabels[targetState.status] || targetState.status || "等待";
            element.dataset.status = targetState.status || "pending";
        });
        renderActions(state); renderTargetActions(state); renderContinue(state);
    }

    async function poll() {
        try { const response = await fetch(panel.dataset.stateUrl, {cache: "no-store"}); if (!response.ok) throw new Error("发布状态暂时无法读取"); render(await response.json()); }
        catch (pollError) { if (workflowMessage) workflowMessage.textContent = pollError.message; }
    }
    poll(); window.setInterval(poll, 1000);
})();
