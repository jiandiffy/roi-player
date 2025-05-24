// --- Globals for this instance of the script (can be top frame or an iframe) ---
let localIsShiftPressed = false; // Is Shift pressed in this current frame?
let localIsSelecting = false;   // Is selection happening in this current frame?
let localSelectionRectDiv = null;
let localStartX, localStartY;

// --- Globals for TOP FRAME ONLY ---
let globalIsMagnified = false; // Tracks if the page is *actually* magnified (top-level state)
let originalPageStyles = {
  transform: '',
  transformOrigin: '',
  htmlOverflow: '',
  bodyOverflow: ''
};

const IS_TOP_FRAME = (window === window.top);

// --- Initialization ---

// Listen for messages from other frames (e.g., iframe telling top to magnify)
chrome.runtime.onMessage.addListener(handleRuntimeMessages);

// Add event listeners using capture phase for better preemption
window.addEventListener('keydown', handleKeyDown, { capture: true });
window.addEventListener('keyup', handleKeyUp, { capture: true });
window.addEventListener('mousedown', handleMouseDown, { capture: true });
window.addEventListener('mousemove', handleMouseMove, { capture: true });
window.addEventListener('mouseup', handleMouseUp, { capture: true });


// --- Event Handlers ---

function handleKeyDown(event) {
  if (event.key === 'Shift' && !event.repeat) {
    if (IS_TOP_FRAME && globalIsMagnified) {
      exitMagnifier();
    } else if (!globalIsMagnified) { // Any frame can set localShiftPressed if page isn't magnified
      localIsShiftPressed = true;
      // Cursor change will be applied on mousedown if selection starts
    }
  }
}

function handleKeyUp(event) {
  if (event.key === 'Shift') {
    const wasSelectingInThisFrame = localIsSelecting;
    localIsShiftPressed = false;

    if (wasSelectingInThisFrame) {
      localIsSelecting = false;
      removeLocalSelectionRect();
    }
    document.body.classList.remove('magnifier-cursor-crosshair');
  }
}

function handleMouseDown(event) {
  if (event.button !== 0) return; // Only react to main (left) mouse button

  // Prevent selection if globally magnified
  if (globalIsMagnified) return;

  // If shift is pressed, attempt to start selection
  if (localIsShiftPressed) {
    // Don't start selection on scrollbars
    if (event.clientX >= document.documentElement.clientWidth || event.clientY >= document.documentElement.clientHeight) {
      return;
    }
    // Don't start selection on input elements or contenteditables
    const targetTagName = event.target.tagName;
    if (targetTagName === 'INPUT' || targetTagName === 'TEXTAREA' || targetTagName === 'SELECT' || event.target.isContentEditable) {
      return;
    }

    localIsSelecting = true;
    localStartX = event.clientX; // Viewport coordinate
    localStartY = event.clientY; // Viewport coordinate

    createLocalSelectionRect(); // Creates rect in the current frame's document
    updateLocalSelectionRect(localStartX, localStartY, localStartX, localStartY);
    document.body.classList.add('magnifier-cursor-crosshair');

    event.preventDefault();    // Prevent text selection, image dragging etc.
    event.stopPropagation();   // Stop event from bubbling further
  }
}

function handleMouseMove(event) {
  if (localIsSelecting) {
    updateLocalSelectionRect(localStartX, localStartY, event.clientX, event.clientY);
    event.preventDefault();
    event.stopPropagation();
  }
}

function handleMouseUp(event) {
  if (event.button !== 0) return; // Only react to main (left) mouse button

  if (localIsSelecting) {
    const localEndX = event.clientX;
    const localEndY = event.clientY;

    // Reset selection state for this frame *before* any async messaging
    localIsSelecting = false;
    removeLocalSelectionRect();
    document.body.classList.remove('magnifier-cursor-crosshair');

    const selWidth = Math.abs(localEndX - localStartX);
    const selHeight = Math.abs(localEndY - localStartY);

    if (selWidth > 10 && selHeight > 10) { // Minimum selection size
      const selectedRegionViewport = {
        x: Math.min(localStartX, localEndX), // Viewport X
        y: Math.min(localStartY, localEndY), // Viewport Y
        width: selWidth,
        height: selHeight
      };

      if (IS_TOP_FRAME) {
        activateMagnifier(selectedRegionViewport);
      } else {
        // Iframe sends message to top frame to activate magnification
        chrome.runtime.sendMessage({
          type: "magnifierAction",
          action: "activate",
          data: selectedRegionViewport
        });
      }
    }
    event.stopPropagation();
  }
}


// --- Selection Rectangle Functions (Local to each frame) ---

function createLocalSelectionRect() {
  if (localSelectionRectDiv) {
    removeLocalSelectionRect()
  }
  localSelectionRectDiv = document.createElement('div');
  localSelectionRectDiv.classList.add('magnifier-selection-rect-visual');
  // Append to current frame's body. It's only a visual guide for this frame.
  document.body.appendChild(localSelectionRectDiv);
}

function updateLocalSelectionRect(x1, y1, x2, y2) {
  if (!localSelectionRectDiv) return;
  const left = Math.min(x1, x2);
  const top = Math.min(y1, y2);
  const width = Math.abs(x1 - x2);
  const height = Math.abs(y1 - y2);

  localSelectionRectDiv.style.left = `${left}px`;
  localSelectionRectDiv.style.top = `${top}px`;
  localSelectionRectDiv.style.width = `${width}px`;
  localSelectionRectDiv.style.height = `${height}px`;
}

function removeLocalSelectionRect() {
  if (localSelectionRectDiv) {
    localSelectionRectDiv.remove();
    localSelectionRectDiv = null;
  }
}


// --- Magnifier Control Functions (Called by/in TOP FRAME ONLY) ---

function activateMagnifier(selectedRectViewport) {
  if (globalIsMagnified || !IS_TOP_FRAME) return;

  const scrollX = window.scrollX;
  const scrollY = window.scrollY;
  const selectedDocX = selectedRectViewport.x + scrollX;
  const selectedDocY = selectedRectViewport.y + scrollY;
  const selectedWidth = selectedRectViewport.width;
  const selectedHeight = selectedRectViewport.height;

  const viewportWidth = window.innerWidth;
  const viewportHeight = window.innerHeight;

  const scaleX = viewportWidth / selectedWidth;
  const scaleY = viewportHeight / selectedHeight;
  const scale = Math.min(scaleX, scaleY);

  const translateX = -selectedDocX * scale;
  const translateY = -selectedDocY * scale;
  const scaledContentWidth = selectedWidth * scale;
  const scaledContentHeight = selectedHeight * scale;
  const centeringTranslateX = (viewportWidth - scaledContentWidth) / 2;
  const centeringTranslateY = (viewportHeight - scaledContentHeight) / 2;
  const finalTranslateX = translateX + centeringTranslateX;
  const finalTranslateY = translateY + centeringTranslateY;

  const htmlEl = document.documentElement;
  const bodyEl = document.body;

  originalPageStyles.transform = htmlEl.style.transform;
  originalPageStyles.transformOrigin = htmlEl.style.transformOrigin;
  originalPageStyles.htmlOverflow = htmlEl.style.overflow;
  originalPageStyles.bodyOverflow = bodyEl.style.overflow;

  htmlEl.style.transformOrigin = '0 0';
  htmlEl.style.transform = `scale(${scale}) translate(${finalTranslateX}px, ${finalTranslateY}px)`;
  htmlEl.style.overflow = 'hidden';
  bodyEl.style.overflow = 'hidden';

  globalIsMagnified = true;
  // Notify all frames that magnification is active
  chrome.runtime.sendMessage({ type: "magnifierStateChange", magnified: true });
}

function exitMagnifier() {
  if (!globalIsMagnified || !IS_TOP_FRAME) return;

  const htmlEl = document.documentElement;
  const bodyEl = document.body;

  htmlEl.style.transform = originalPageStyles.transform;
  htmlEl.style.transformOrigin = originalPageStyles.transformOrigin;
  htmlEl.style.overflow = originalPageStyles.htmlOverflow;
  bodyEl.style.overflow = originalPageStyles.bodyOverflow;

  globalIsMagnified = false;
  localIsShiftPressed = false; // Reset shift state for top frame as well

  // Notify all frames that magnification is off
  chrome.runtime.sendMessage({ type: "magnifierStateChange", magnified: false });
  document.body.classList.remove('magnifier-cursor-crosshair'); // Ensure cursor reset in top frame
}


// --- Inter-frame Message Handling ---

function handleRuntimeMessages(request, sender, sendResponse) {
  if (request.type === "magnifierAction") {
    if (request.action === "activate" && IS_TOP_FRAME) {
      // An iframe requested to activate magnification
      activateMagnifier(request.data);
      sendResponse({ success: true });
    }
  } else if (request.type === "magnifierStateChange") {
    // Top frame broadcasted a state change
    globalIsMagnified = request.magnified; // Update global state in iframes too
    if (globalIsMagnified) {
      // If an iframe was in the middle of a selection, cancel it
      if (localIsSelecting) {
        localIsSelecting = false;
        removeLocalSelectionRect();
      }
      localIsShiftPressed = false; // Cannot start new selection while magnified
      document.body.classList.remove('magnifier-cursor-crosshair');
    }
  }
  // Keep the message channel open for asynchronous responses if needed, though not strictly here.
  return true;
}