// Draggable vertical divider that resizes the editor pane.

const MIN_PANE = 280;

export function initResizer(resizer, leftPane, container) {
  let dragging = false;

  const applyX = (clientX) => {
    const rect = container.getBoundingClientRect();
    let basis = clientX - rect.left;
    const max = rect.width - MIN_PANE;
    basis = Math.max(MIN_PANE, Math.min(max, basis));
    leftPane.style.flex = `0 0 ${basis}px`;
  };

  // Mouse
  resizer.addEventListener("mousedown", (event) => {
    dragging = true;
    document.body.classList.add("resizing");
    event.preventDefault();
  });
  window.addEventListener("mousemove", (event) => {
    if (dragging) applyX(event.clientX);
  });
  window.addEventListener("mouseup", () => {
    if (!dragging) return;
    dragging = false;
    document.body.classList.remove("resizing");
  });

  // Touch
  resizer.addEventListener(
    "touchstart",
    () => {
      dragging = true;
    },
    { passive: true }
  );
  window.addEventListener(
    "touchmove",
    (event) => {
      if (dragging && event.touches[0]) applyX(event.touches[0].clientX);
    },
    { passive: true }
  );
  window.addEventListener("touchend", () => {
    dragging = false;
  });

  // Keyboard accessibility
  resizer.addEventListener("keydown", (event) => {
    const width = leftPane.getBoundingClientRect().width;
    if (event.key === "ArrowLeft") leftPane.style.flex = `0 0 ${width - 24}px`;
    if (event.key === "ArrowRight") leftPane.style.flex = `0 0 ${width + 24}px`;
  });
}
