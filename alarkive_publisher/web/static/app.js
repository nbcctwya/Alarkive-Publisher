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

