"""A themed reference-attachment tray built with Streamlit Components v2 (CCv2).

Displays attached reference images as crisp rounded square cards with a small
overlay remove button in the top-right corner, a subtle order badge when
multiple images are attached, and native drag-and-drop reordering. Every color
and radius inherits from Streamlit's ``--st-*`` CSS custom properties so the
tray follows the active light or dark theme automatically.
"""

from __future__ import annotations

from typing import Any

import streamlit as st

_HTML = """
<div class="vertigo-refs" role="list"></div>
"""

_CSS = """
:host {
  display: block;
  width: 100%;
}

.vertigo-refs {
  box-sizing: border-box;
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
  padding: 0.25rem 0 0.15rem;
  font-family: var(--st-font, sans-serif);
  user-select: none;
}

.vertigo-ref {
  position: relative;
  width: 78px;
  height: 78px;
  border-radius: var(--st-base-radius, 0.5rem);
  overflow: hidden;
  border: 1px solid var(--st-border-color, rgba(128, 128, 128, 0.28));
  background-color: var(--st-secondary-background-color, rgba(128, 128, 128, 0.08));
  background-image:
    linear-gradient(45deg, rgba(128, 128, 128, 0.09) 25%, transparent 25%),
    linear-gradient(-45deg, rgba(128, 128, 128, 0.09) 25%, transparent 25%),
    linear-gradient(45deg, transparent 75%, rgba(128, 128, 128, 0.09) 75%),
    linear-gradient(-45deg, transparent 75%, rgba(128, 128, 128, 0.09) 75%);
  background-size: 12px 12px;
  background-position: 0 0, 0 6px, 6px -6px, -6px 0;
  transition: transform 130ms ease, box-shadow 130ms ease, border-color 130ms ease, opacity 130ms ease;
  outline: none;
}

.vertigo-ref.is-draggable {
  cursor: grab;
}

.vertigo-ref.is-draggable:active {
  cursor: grabbing;
}

.vertigo-ref:hover {
  border-color: color-mix(in srgb, var(--st-primary-color, #ff4b4b) 55%, var(--st-border-color, #999));
  box-shadow: 0 3px 10px rgba(0, 0, 0, 0.12);
}

.vertigo-ref:focus-visible {
  box-shadow: 0 0 0 2px var(--st-primary-color, #ff4b4b);
  border-color: var(--st-primary-color, #ff4b4b);
}

.vertigo-ref.is-dragging {
  opacity: 0.38;
  transform: scale(0.94);
}

.vertigo-ref.is-drop-target {
  border-color: var(--st-primary-color, #ff4b4b);
  box-shadow: 0 0 0 2px var(--st-primary-color, #ff4b4b);
  transform: scale(1.04);
}

.vertigo-ref__img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
  pointer-events: none;
  -webkit-user-drag: none;
}

.vertigo-ref__remove {
  position: absolute;
  top: 4px;
  right: 4px;
  width: 20px;
  height: 20px;
  padding: 0;
  margin: 0;
  border: none;
  border-radius: 999px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(15, 23, 42, 0.72);
  color: #ffffff;
  backdrop-filter: blur(4px);
  -webkit-backdrop-filter: blur(4px);
  cursor: pointer;
  opacity: 0.88;
  transition: background-color 120ms ease, transform 120ms ease, opacity 120ms ease;
}

.vertigo-ref:hover .vertigo-ref__remove,
.vertigo-ref__remove:focus-visible {
  opacity: 1;
}

.vertigo-ref__remove:hover {
  background: rgba(220, 38, 38, 0.92);
  transform: scale(1.08);
}

.vertigo-ref__remove:active {
  transform: scale(0.95);
}

.vertigo-ref__remove svg {
  width: 12px;
  height: 12px;
  display: block;
  pointer-events: none;
}

.vertigo-ref__badge {
  position: absolute;
  bottom: 4px;
  left: 4px;
  min-width: 18px;
  height: 18px;
  padding: 0 5px;
  border-radius: 999px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 0.6875rem;
  font-weight: 600;
  line-height: 1;
  background: rgba(15, 23, 42, 0.72);
  color: #ffffff;
  backdrop-filter: blur(4px);
  -webkit-backdrop-filter: blur(4px);
  pointer-events: none;
}

.vertigo-ref__badge.is-primary {
  background: var(--st-primary-color, #ff4b4b);
  color: #ffffff;
}
"""

_JS = """
const CLOSE_SVG =
  '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">' +
  '<path d="M4 4l8 8M12 4l-8 8"/></svg>';

export default function (component) {
  const { data, parentElement, setTriggerValue } = component;
  const grid = parentElement.querySelector(".vertigo-refs");
  if (!grid) return;

  const items = Array.isArray(data?.items) ? data.items : [];
  const removeLabel = data?.removeLabel || "Remove";
  const canReorder = items.length > 1;
  let draggedId = null;

  function clearDropTargets() {
    grid.querySelectorAll(".is-drop-target").forEach((el) => {
      el.classList.remove("is-drop-target");
    });
  }

  function emitReorder(fromId, toId) {
    if (!fromId || !toId || fromId === toId) return;
    const ids = items.map((item) => item.id);
    const fromIdx = ids.indexOf(fromId);
    const toIdx = ids.indexOf(toId);
    if (fromIdx === -1 || toIdx === -1) return;
    ids.splice(fromIdx, 1);
    ids.splice(toIdx, 0, fromId);
    setTriggerValue("reorder", ids);
  }

  grid.replaceChildren();

  items.forEach((item, index) => {
    const card = document.createElement("div");
    card.className = canReorder ? "vertigo-ref is-draggable" : "vertigo-ref";
    card.setAttribute("role", "listitem");
    card.setAttribute("tabindex", "0");
    card.dataset.id = item.id;
    card.title = item.name || "";
    card.draggable = canReorder;

    const img = document.createElement("img");
    img.className = "vertigo-ref__img";
    img.src = item.src || "";
    img.alt = item.name || "";
    card.appendChild(img);

    if (canReorder) {
      const badge = document.createElement("span");
      badge.className = index === 0 ? "vertigo-ref__badge is-primary" : "vertigo-ref__badge";
      badge.textContent = String(index + 1);
      card.appendChild(badge);
    }

    const removeBtn = document.createElement("button");
    removeBtn.type = "button";
    removeBtn.className = "vertigo-ref__remove";
    removeBtn.setAttribute("aria-label", removeLabel);
    removeBtn.title = removeLabel;
    removeBtn.innerHTML = CLOSE_SVG;
    removeBtn.addEventListener("pointerdown", (e) => {
      e.stopPropagation();
    });
    removeBtn.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      setTriggerValue("remove", item.id);
    });
    card.appendChild(removeBtn);

    if (canReorder) {
      card.addEventListener("dragstart", (e) => {
        draggedId = item.id;
        card.classList.add("is-dragging");
        if (e.dataTransfer) {
          e.dataTransfer.effectAllowed = "move";
          try {
            e.dataTransfer.setData("text/plain", item.id);
          } catch (_) {}
        }
      });

      card.addEventListener("dragover", (e) => {
        if (!draggedId || draggedId === item.id) return;
        e.preventDefault();
        if (e.dataTransfer) e.dataTransfer.dropEffect = "move";
        clearDropTargets();
        card.classList.add("is-drop-target");
      });

      card.addEventListener("dragleave", (e) => {
        if (!card.contains(e.relatedTarget)) {
          card.classList.remove("is-drop-target");
        }
      });

      card.addEventListener("drop", (e) => {
        e.preventDefault();
        const sourceId = draggedId || e.dataTransfer?.getData("text/plain");
        clearDropTargets();
        emitReorder(sourceId, item.id);
        draggedId = null;
      });

      card.addEventListener("dragend", () => {
        draggedId = null;
        card.classList.remove("is-dragging");
        clearDropTargets();
      });
    }

    card.addEventListener("keydown", (e) => {
      if (e.key === "Delete" || e.key === "Backspace") {
        e.preventDefault();
        setTriggerValue("remove", item.id);
      } else if (canReorder && e.key === "ArrowLeft" && index > 0) {
        e.preventDefault();
        emitReorder(item.id, items[index - 1].id);
      } else if (canReorder && e.key === "ArrowRight" && index < items.length - 1) {
        e.preventDefault();
        emitReorder(item.id, items[index + 1].id);
      }
    });

    grid.appendChild(card);
  });
}
"""

_REFERENCE_TRAY = st.components.v2.component(
    "vertigo_reference_tray",
    html=_HTML,
    css=_CSS,
    js=_JS,
)


def reference_tray(
    items: list[dict[str, str]],
    *,
    remove_label: str = "Remove",
    key: str = "studio_reference_tray",
) -> tuple[str | None, list[str] | None]:
    """Render attached references with overlay remove buttons and drag-to-reorder.

    Returns ``(removed_id, reordered_ids)`` for the current rerun.
    """
    result: Any = _REFERENCE_TRAY(
        data={"items": items, "removeLabel": remove_label},
        key=key,
        width="stretch",
        height="content",
        on_remove_change=lambda: None,
        on_reorder_change=lambda: None,
    )
    removed = getattr(result, "remove", None) if result is not None else None
    reordered = getattr(result, "reorder", None) if result is not None else None
    removed_id = str(removed) if isinstance(removed, str) and removed else None
    reordered_ids = (
        [str(item) for item in reordered]
        if isinstance(reordered, list) and reordered
        else None
    )
    return removed_id, reordered_ids
