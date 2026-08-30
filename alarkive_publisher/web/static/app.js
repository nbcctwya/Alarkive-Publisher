(function () {
    "use strict";

    const input = document.getElementById("images");
    const zone = document.getElementById("upload-zone");
    const grid = document.getElementById("preview-grid");
    const error = document.getElementById("upload-error");
    const form = document.getElementById("post-form");
    const baijiahaoBody = document.getElementById("baijiahao_body");
    const markerStatus = document.getElementById("baijiahao-marker-status");
    const xiaohongshuPromptButton = document.getElementById("copy-xiaohongshu-prompt");
    const baijiahaoPromptButton = document.getElementById("copy-baijiahao-prompt");
    const wechatPromptButton = document.getElementById("copy-wechat-prompt");
    const xiaohongshuPromptStatus = document.getElementById("xiaohongshu-prompt-status");
    const baijiahaoPromptStatus = document.getElementById("baijiahao-prompt-status");
    const wechatPromptStatus = document.getElementById("wechat-prompt-status");
    const xiaohongshuPromptFallback = document.getElementById("xiaohongshu-prompt-fallback");
    const baijiahaoPromptFallback = document.getElementById("baijiahao-prompt-fallback");
    const wechatPromptFallback = document.getElementById("wechat-prompt-fallback");
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

    function buildXiaohongshuPrompt() {
        return [
            "请根据我们当前对话中已经完成的研究、底稿和配图，生成最终的小红书正文。",
            "",
            "要求：",
            "1. 直接输出可以发布的小红书正文，不要解释写作过程，不要输出标题，不要输出“以下是正文”之类的说明，不要使用代码块。",
            "2. 内容要比长文章明显更精炼。保留最有价值、最有趣、最容易让人记住的信息，不要把研究过程和所有细节都塞进去。",
            "3. 符合小红书的阅读习惯和注意力机制：",
            "- 开头尽快进入主题，让用户前几行就知道这篇内容为什么值得看；",
            "- 段落尽量短，避免连续大段文字；",
            "- 信息密度高，但不要写得像论文、报告或新闻通稿；",
            "- 可以适当使用 Emoji、短句、分点和留白提升阅读节奏，但不要过量；",
            "- 重点内容可以适当强化，但不要制造廉价的夸张感。",
            "4. 语气自然、轻松、有交流感，像一个真正了解这件事的人在和读者分享，而不是 AI 在总结资料。",
            "5. 不要为了制造“爆款感”强行使用夸张标题党表达，例如：",
            "“震惊”",
            "“太炸裂了”",
            "“封神”",
            "“所有人都必须知道”",
            "除非上下文本身确实适合这种表达。",
            "6. 可以适当加入自己的判断、感受或一句自然的总结，让内容有人味，但不要编造我们当前对话中没有形成的观点。",
            "7. 不需要在正文中描述图片位置，也不要输出任何图片占位符。图片会由 Alarkive Publisher 单独处理。",
            "8. 最终正文必须可以直接复制并粘贴进 Alarkive Publisher 的“小红书正文”输入框。"
        ].join("\n");
    }

    function buildBaijiahaoPrompt() {
        const count = selectedFiles.length;
        const markerList = Array.from(
            { length: count },
            (_, index) => "[[image:" + (index + 1) + "]]"
        ).join("\n");
        const markerMapping = Array.from(
            { length: count },
            (_, index) => "第 " + (index + 1) + " 张图片 → [[image:" + (index + 1) + "]]"
        ).join("\n");
        return [
            "请根据我们当前对话中已经完成的研究、底稿和配图，生成最终的百家号正文。",
            "",
            "要求：",
            "1. 直接输出可以发布的百家号正文，不要解释写作过程，不要输出标题，不要输出“以下是正文”之类的说明，不要使用代码块。",
            "2. 文风通俗易懂、自然流畅、信息完整，不要过度书面化，不要写成论文或报告。",
            "3. 当前一共有 " + count + " 张配图。",
            "",
            "请按以下规则确定图片：",
            "- 如果我在这条消息中附上了图片，请使用本条消息附带的图片，并严格按照附件显示顺序编号。",
            "- 如果这条消息没有附图，请使用我们当前对话中此前已经为这篇文章生成的这组配图，并严格按照它们的生成顺序编号。",
            "",
            "图片编号与 Alarkive Publisher 占位符的对应关系为：",
            markerMapping,
            "",
            "请根据每张图片的实际视觉内容，以及文章上下文，把图片安排在最适合阅读的位置。",
            "优先使用全部图片，但不要为了平均分布图片而机械插入。图片应放在与其内容最相关的段落附近，不要把所有图片集中放在文章末尾。",
            "",
            "4. 只能使用以下 Alarkive Publisher 图片占位符：",
            markerList,
            "不要引用不存在的图片编号，不要重复使用同一张图片。每张图片最多使用一次。",
            "",
            "5. 每个图片占位符必须单独占一行。",
            "正确：",
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
            "<image:1>",
            "7. 除图片占位符外，其余正文正常输出即可，可以使用普通 Markdown 的段落、小标题、加粗、列表。",
            "8. 不要在正文中解释“下面插入图片”“这里放一张图”“如图所示”等提示语。图片应该自然融入文章阅读流程。",
            "9. 不要仅根据图片文件名猜测图片内容，应根据图片本身的视觉内容判断。",
            "10. 最终输出必须可以直接复制并粘贴进 Alarkive Publisher 的“百家号正文”输入框。"
        ].join("\n");
    }

    function buildWechatPrompt() {
        return [
            "请根据我们当前对话中已经完成的研究、底稿和配图，生成最终的微信公众号小绿书正文。",
            "",
            "要求：",
            "1. 直接输出可以发布的小绿书正文，不要解释写作过程，不要输出标题，不要输出“以下是正文”之类的说明，不要使用代码块。",
            "2. 内容尽可能简洁、直观。优先保留最重要、最有价值、最容易理解的信息，不要写成长篇公众号文章。",
            "3. 符合微信图文的阅读习惯：",
            "- 开头直接进入主题；",
            "- 段落短；",
            "- 一段尽量只表达一个重点；",
            "- 重要信息放在容易扫读的位置；",
            "- 不要连续堆大量背景信息；",
            "- 让读者用较短时间就能看懂这件事。",
            "4. 语气自然、克制、清晰，可以有一点个人表达，但不要太营销，不要写成媒体通稿、研究报告或营销软文。",
            "5. 不需要刻意追求小红书式的强情绪和“爆款感”。相比制造刺激，更重视清楚、舒服、值得读完。",
            "6. 可以适当使用小标题、短句、分点和留白，让手机阅读更轻松，但不要把文章切得过碎。",
            "7. 不需要在正文中描述图片位置，也不要输出任何图片占位符。图片会由 Alarkive Publisher 单独处理。",
            "8. 最终正文必须可以直接复制并粘贴进 Alarkive Publisher 的“微信公众号正文”输入框。"
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

    function showPromptStatus(statusElement, message, isError) {
        if (!statusElement) return;
        statusElement.textContent = message;
        statusElement.className = "prompt-status" + (isError ? " error" : "");
    }

    function showPromptFallback(fallbackElement, prompt) {
        if (!fallbackElement) return;
        fallbackElement.value = prompt;
        fallbackElement.hidden = false;
        fallbackElement.focus();
        fallbackElement.select();
    }

    async function copyPrompt(button, prompt, statusElement, fallbackElement, successMessage) {
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
            showPromptStatus(statusElement, successMessage, false);
            if (fallbackElement) fallbackElement.hidden = true;
            const original = button.textContent;
            button.textContent = "✓ 已复制";
            window.setTimeout(() => {
                button.textContent = original;
            }, 2500);
        } else {
            showPromptFallback(fallbackElement, prompt);
            showPromptStatus(statusElement, "复制失败，请手动复制下方 Prompt。", true);
        }
    }

    function bindPrompt(button, statusElement, fallbackElement, builder, successMessage, requiresImages) {
        if (!button) return;
        button.addEventListener("click", async () => {
            if (requiresImages && !selectedFiles.length) {
                showPromptStatus(statusElement, "请先添加图片，再生成兼容 Alarkive 的 Prompt。", true);
                renderMarkerStatus();
                return;
            }
            await copyPrompt(button, builder(), statusElement, fallbackElement, successMessage);
        });
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
    bindPrompt(
        xiaohongshuPromptButton,
        xiaohongshuPromptStatus,
        xiaohongshuPromptFallback,
        buildXiaohongshuPrompt,
        "✓ 小红书 Prompt 已复制",
        false
    );
    bindPrompt(
        baijiahaoPromptButton,
        baijiahaoPromptStatus,
        baijiahaoPromptFallback,
        buildBaijiahaoPrompt,
        "✓ 百家号 Prompt 已复制",
        true
    );
    bindPrompt(
        wechatPromptButton,
        wechatPromptStatus,
        wechatPromptFallback,
        buildWechatPrompt,
        "✓ 小绿书 Prompt 已复制",
        false
    );
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
    const platformActionContainers = document.querySelectorAll("[data-platform-action]");
    const platformActionLabels = {
        xiaohongshu: "发布小红书",
        baijiahao: "发布百家号",
        wechat: "发布小绿书"
    };

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
        }

        if (publisherActive) {
            const notice = document.createElement("span");
            notice.className = "workflow-action-note";
            notice.textContent = "发布流程进行中";
            actions.appendChild(notice);
        } else if (!state.published) {
            const allForm = createForm(
                panel.dataset.publishUrl,
                "发布全部",
                "开始发布全部平台的准备流程？\n\nAlarkive 会自动填写三个平台，但不会点击平台真正的发布按钮。"
            );
            allForm.querySelector("button").className = "button button-secondary";
            actions.appendChild(allForm);
        }
        if (failedBrowserOpen) {
            actions.appendChild(createForm(panel.dataset.closeUrl, "关闭浏览器"));
        }
    }

    function renderPlatformActions(state) {
        const publisherActive = state.publisher_active === true;
        platformActionContainers.forEach((container) => {
            const platform = container.dataset.platformAction;
            container.replaceChildren();
            if (publisherActive) {
                const notice = document.createElement("span");
                notice.className = "workflow-action-note";
                notice.textContent = "发布流程进行中";
                container.appendChild(notice);
                return;
            }

            const label = platformActionLabels[platform];
            if (!label) return;
            const form = createForm(
                container.dataset.publishUrl,
                label,
                "开始准备" + (platform === "wechat" ? "小绿书" : labels[platform]) + "内容？\n\nAlarkive 不会点击平台真正的发布按钮。"
            );
            container.appendChild(form);
        });
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
        if (
            workflow.current_step === "ready" &&
            (workflow.workflow_mode === "single" || workflow.current_platform === "wechat")
        ) {
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
        renderPlatformActions(state);
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
