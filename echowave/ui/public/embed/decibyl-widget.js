/**
 * Decibyl Voice Widget
 * Embeddable voice call widget for Decibyl workflows
 * Version: 1.0.0
 */

(function() {
  'use strict';

  // Widget configuration defaults
  const DEFAULT_CONFIG = {
    position: 'bottom-right',
    // The pill can be dragged anywhere in the viewport and remembers where it
    // was put. On by default: the widget is fixed at 999999 and a host page
    // has no way to move it out of its own way, so on a narrow screen it can
    // land on top of the site's own header button with no recourse for the
    // visitor. Set `draggable: false` to pin it.
    draggable: true,
    autoStart: false,
    // Text chat alongside voice. Off by default: every snippet already pasted
    // into a customer's site sends no such setting, and a widget that silently
    // grows a new control is a change to their page they did not ask for.
    enableText: false,
    apiBaseUrl: window.location.hostname === 'localhost'
      ? 'http://localhost:8000'
      : 'https://api.decibyl.com'
  };

  // Widget state
  const state = {
    config: {},
    isInitialized: false,
    isOpen: false,
    pc: null,
    ws: null,
    stream: null,
    sessionToken: null,
    workflowRunId: null,
    pcId: null,
    connectionStatus: 'idle', // idle, connecting, connected, failed
    audioElement: null,
    turnCredentials: null, // TURN server credentials
    callStartedAt: null, // Timestamp when call connected (for duration tracking)
    // Text mode. Its own session token: a typed conversation is a separate
    // workflow run from a call, so one widget can hold both without either
    // overwriting the other's session.
    textSessionToken: null,
    textMessages: [],
    textSending: false,
    textCompleted: false,
    textOpen: false,
    gracefulDisconnect: false,
    callbacks: {
      onReady: null,
      onCallStart: null,
      onCallConnected: null,
      onCallDisconnected: null,
      onCallEnd: null,
      onError: null,
      onStatusChange: null
    }
  };

  /**
   * Initialize the widget
   */
  async function init() {
    if (state.isInitialized) return;

    // Get token from script URL
    const script = document.currentScript || document.querySelector('script[src*="decibyl-widget.js"]');
    if (!script) {
      console.error('Decibyl Widget: Script not found');
      return;
    }

    // Extract parameters from URL
    const scriptUrl = new URL(script.src);
    const token = scriptUrl.searchParams.get('token');
    const apiEndpoint = scriptUrl.searchParams.get('apiEndpoint');
    const environment = scriptUrl.searchParams.get('environment');
    // A query param, like token and apiEndpoint, because that is the shape of
    // the snippet a customer pastes. A data attribute would be a second way to
    // configure the same widget.
    const enableText = scriptUrl.searchParams.get('text') === 'true';

    if (!token) {
      console.error('Decibyl Widget: No token found in script URL');
      return;
    }

    // Determine API base URL
    let apiBaseUrl = DEFAULT_CONFIG.apiBaseUrl;
    if (apiEndpoint) {
      // Use the apiEndpoint from URL parameter if provided
      // Ensure it has a protocol
      if (!apiEndpoint.startsWith('http://') && !apiEndpoint.startsWith('https://')) {
        // Default to https for production endpoints
        apiBaseUrl = 'https://' + apiEndpoint.replace(/\/+$/, '');
      } else {
        apiBaseUrl = apiEndpoint.replace(/\/+$/, ''); // Remove trailing slashes
      }
    } else if (scriptUrl.origin.includes('localhost')) {
      apiBaseUrl = 'http://localhost:8000';
    } else {
      apiBaseUrl = scriptUrl.origin.replace(/:\d+$/, ':8000');
    }

    // Store base configuration
    state.config = {
      ...DEFAULT_CONFIG,
      token: token,
      apiBaseUrl: apiBaseUrl,
      enableText: enableText,
      environment: environment || 'production',
      // Allow data attributes to override fetched config
      contextVariables: parseContextVariables(script.getAttribute('data-decibyl-context'))
    };

    try {
      // Fetch configuration from API
      const configResponse = await fetch(`${state.config.apiBaseUrl}/api/v1/public/embed/config/${token}`, {
        method: 'GET',
        headers: {
          'Content-Type': 'application/json',
          'Origin': window.location.origin
        }
      });

      if (!configResponse.ok) {
        throw new Error(`Failed to fetch config: ${configResponse.status}`);
      }

      const configData = await configResponse.json();

      // Merge fetched configuration with defaults
      state.config = {
        ...state.config,
        workflowId: configData.workflow_id,
        embedMode: configData.settings?.embedMode || 'floating',
        containerId: configData.settings?.containerId || 'decibyl-inline-container',
        position: configData.position || DEFAULT_CONFIG.position,
        draggable: configData.draggable !== false,
        buttonColor: configData.settings?.buttonColor || '#10b981',
        buttonText: configData.settings?.buttonText || 'Talk to Agent',
        callToActionText: configData.settings?.callToActionText || 'Click to start voice conversation',
        autoStart: configData.auto_start || false
      };
    } catch (error) {
      console.error('Decibyl Widget: Failed to fetch configuration', error);
      return;
    }

    state.isInitialized = true;

    // Create widget UI based on mode
    if (state.config.embedMode === 'inline') {
      injectStyles();
      createInlineWidget();
    } else if (state.config.embedMode === 'headless') {
      createHeadlessWidget();
    } else {
      injectStyles();
      createFloatingWidget();
    }

    // Trigger ready callback
    if (state.callbacks.onReady) {
      state.callbacks.onReady();
    }

    // Auto-start if configured
    if (state.config.autoStart) {
      setTimeout(() => startCall(), 1000);
    }
  }

  /**
   * Parse context variables from JSON string
   */
  function parseContextVariables(contextStr) {
    if (!contextStr) return {};
    try {
      return JSON.parse(contextStr);
    } catch (e) {
      console.warn('Decibyl Widget: Invalid context variables', e);
      return {};
    }
  }

  /**
   * Inject widget styles
   */
  function injectStyles() {
    if (document.getElementById('decibyl-widget-styles')) return;

    const styles = `
      .decibyl-widget-container {
        position: fixed;
        z-index: 999999;
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
      }

      /* Dragging is pointer-driven, so the browser must not claim the gesture
         for scrolling first. Only set once dragging is enabled, so a pinned
         widget keeps normal touch behaviour. */
      .decibyl-widget-container.decibyl-draggable { touch-action: none; }

      /* While a drag is live: no text selection anywhere, no transition
         fighting the pointer, and the pill lifts slightly so it reads as
         picked up rather than stuck. */
      .decibyl-widget-container.decibyl-dragging { user-select: none; cursor: grabbing; }
      .decibyl-widget-container.decibyl-dragging .decibyl-widget-cta {
        transition: none;
        transform: scale(1.04);
        box-shadow: 0 10px 28px rgba(0, 0, 0, 0.28);
      }
      .decibyl-widget-container.decibyl-dragging .decibyl-widget-cta:active { transform: scale(1.04); }


      .decibyl-widget-container.bottom-right {
        bottom: 20px;
        right: 20px;
      }

      .decibyl-widget-container.bottom-left {
        bottom: 20px;
        left: 20px;
      }

      .decibyl-widget-container.top-right {
        top: 20px;
        right: 20px;
      }

      .decibyl-widget-container.top-left {
        top: 20px;
        left: 20px;
      }

      /* A pill the visitor has moved is positioned by left/top alone. This has
         to come after the corner rules: they are the same specificity, so
         source order is what decides, and leaving the corner's right offset in
         place stretches the container across the viewport instead of
         shrink-wrapping the pill. max-content states that shrink-wrap rather
         than leaving it to the fixed-position sizing rules. */
      .decibyl-widget-container.decibyl-placed {
        right: auto;
        bottom: auto;
        width: max-content;
        max-width: calc(100vw - 16px);
      }

      /* Text chat. Sits under the pill and shares its stacking context, so a
         host page's own fixed header cannot land on top of the panel. */
      .decibyl-text-toggle {
        background: #ffffff;
        color: #111827;
        border: 1px solid #e5e7eb;
        margin-top: 8px;
      }

      .decibyl-text-panel {
        margin-top: 8px;
        width: 320px;
        max-width: calc(100vw - 32px);
        background: #ffffff;
        border: 1px solid #e5e7eb;
        border-radius: 12px;
        box-shadow: 0 12px 32px rgba(0, 0, 0, 0.16);
        overflow: hidden;
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      }

      .decibyl-text-messages {
        max-height: 320px;
        overflow-y: auto;
        padding: 12px;
        display: flex;
        flex-direction: column;
        gap: 8px;
      }

      .decibyl-text-msg {
        padding: 8px 12px;
        border-radius: 12px;
        font-size: 14px;
        line-height: 1.4;
        max-width: 85%;
        /* Server-relayed text of unknown shape — a long unbroken string must
           wrap rather than widen the panel past the viewport. */
        overflow-wrap: anywhere;
      }

      .decibyl-text-msg-user {
        align-self: flex-end;
        background: #111827;
        color: #ffffff;
      }

      .decibyl-text-msg-assistant {
        align-self: flex-start;
        background: #f3f4f6;
        color: #111827;
      }

      .decibyl-text-typing { opacity: 0.6; }

      .decibyl-text-form {
        display: flex;
        gap: 8px;
        padding: 10px;
        border-top: 1px solid #e5e7eb;
      }

      .decibyl-text-input {
        flex: 1;
        min-width: 0;
        border: 1px solid #e5e7eb;
        border-radius: 8px;
        padding: 8px 10px;
        font-size: 14px;
        font-family: inherit;
        color: #111827;
        background: #ffffff;
      }

      .decibyl-text-input:focus {
        outline: 2px solid #111827;
        outline-offset: -1px;
      }

      .decibyl-text-send {
        border: none;
        border-radius: 8px;
        padding: 8px 14px;
        font-size: 14px;
        font-weight: 500;
        color: #ffffff;
        background: #111827;
        cursor: pointer;
      }

      .decibyl-widget-cta {
        display: inline-flex;
        align-items: center;
        gap: 8px;
        padding: 12px 20px;
        border: none;
        border-radius: 9999px;
        color: #ffffff;
        font-size: 14px;
        font-weight: 600;
        cursor: pointer;
        white-space: nowrap;
        max-width: calc(100vw - 40px);
        box-shadow: 0 4px 14px rgba(0, 0, 0, 0.2);
        transition: filter 150ms ease, transform 100ms ease, box-shadow 200ms ease;
        animation: decibyl-cta-in 220ms ease-out;
      }

      .decibyl-widget-cta:hover {
        filter: brightness(1.08);
        box-shadow: 0 6px 18px rgba(0, 0, 0, 0.28);
      }
      .decibyl-widget-cta:active { transform: scale(0.98); }

      .decibyl-widget-cta.decibyl-state-connecting { background: #f59e0b !important; animation: decibyl-pulse 1.6s infinite; }
      .decibyl-widget-cta.decibyl-state-connected  { background: #ef4444 !important; }
      .decibyl-widget-cta.decibyl-state-failed     { background: #ef4444 !important; opacity: 0.85; }

      @keyframes decibyl-pulse {
        0%, 100% { opacity: 1; }
        50% { opacity: 0.6; }
      }

      @keyframes decibyl-cta-in {
        from { opacity: 0; transform: translateY(8px); }
        to { opacity: 1; transform: translateY(0); }
      }
    `;

    const styleSheet = document.createElement('style');
    styleSheet.id = 'decibyl-widget-styles';
    styleSheet.textContent = styles;
    document.head.appendChild(styleSheet);
  }

  function ctaLabelForStatus(status) {
    switch (status) {
      case 'connecting': return 'Connecting…';
      case 'connected':  return 'End Call';
      case 'failed':     return 'Retry';
      default:           return state.config.buttonText || 'Talk to Agent';
    }
  }

  /**
   * Create floating widget UI — a single CTA pill button anchored to the
   * configured corner of the viewport.
   */
  function createFloatingWidget() {
    const container = document.createElement('div');
    container.className = `decibyl-widget-container ${state.config.position}`;
    container.id = 'decibyl-widget-root';

    const audio = document.createElement('audio');
    audio.id = 'decibyl-widget-audio';
    audio.autoplay = true;
    audio.style.display = 'none';
    container.appendChild(audio);
    state.audioElement = audio;

    document.body.appendChild(container);
    renderFloating();

    if (state.config.draggable) makeDraggable(container);
  }

  /** Where a visitor last put the pill, keyed per origin. */
  const DRAG_STORAGE_KEY = 'decibyl-widget-position';
  /** Movement past this many pixels is a drag, below it is a click. Four is
   *  enough to survive the wobble of a thumb pressing a button without
   *  swallowing a deliberate short drag. */
  const DRAG_THRESHOLD_PX = 4;
  /** Keep at least this much of the pill on screen when clamping. */
  const DRAG_MARGIN_PX = 8;

  function readStoredPosition() {
    try {
      const raw = window.localStorage.getItem(DRAG_STORAGE_KEY);
      if (!raw) return null;
      const parsed = JSON.parse(raw);
      if (typeof parsed?.left !== 'number' || typeof parsed?.top !== 'number') return null;
      return parsed;
    } catch {
      // Private mode, blocked storage, or a corrupt value. The widget keeps
      // its configured corner rather than failing to render.
      return null;
    }
  }

  function writeStoredPosition(left, top) {
    try {
      window.localStorage.setItem(DRAG_STORAGE_KEY, JSON.stringify({ left, top }));
    } catch {
      // Not being able to remember the position is not a reason to stop
      // letting the visitor move it.
    }
  }

  /**
   * Let the visitor drag the pill anywhere and keep it there.
   *
   * The widget is `position: fixed` at z-index 999999, which means the host
   * page cannot move it out of the way of its own UI — on a narrow screen it
   * can sit squarely on top of the site's header button, and a visitor who
   * wants that button has no way through. Dragging is the fix that does not
   * require the host to know anything.
   *
   * Bound to the container rather than the button, because `renderFloating`
   * replaces the button on every status change and a listener on it would be
   * lost the moment a call started.
   */
  function makeDraggable(container) {
    container.classList.add('decibyl-draggable');

    let pointerId = null;
    let startX = 0;
    let startY = 0;
    let originLeft = 0;
    let originTop = 0;
    let moved = false;

    /** Hold the pill inside the viewport, whatever the viewport just did. */
    function clamp(left, top) {
      const rect = container.getBoundingClientRect();
      const maxLeft = window.innerWidth - rect.width - DRAG_MARGIN_PX;
      const maxTop = window.innerHeight - rect.height - DRAG_MARGIN_PX;
      return {
        left: Math.min(Math.max(left, DRAG_MARGIN_PX), Math.max(maxLeft, DRAG_MARGIN_PX)),
        top: Math.min(Math.max(top, DRAG_MARGIN_PX), Math.max(maxTop, DRAG_MARGIN_PX)),
      };
    }

    function place(left, top) {
      const safe = clamp(left, top);
      container.classList.add('decibyl-placed');
      container.style.left = `${safe.left}px`;
      container.style.top = `${safe.top}px`;
      return safe;
    }

    // Restore where they left it. Clamped on the way in, because the window
    // they saved it from may have been a different size — or a different
    // device entirely.
    const stored = readStoredPosition();
    if (stored) place(stored.left, stored.top);

    // The move and end listeners live on the document, not the container.
    // Two things go wrong otherwise, and the browser tests caught both:
    // capturing the pointer on pointerdown retargets the click that follows
    // from the button to the container, so the pill stops working as a button
    // at all; and without capture, a container-bound pointermove stops firing
    // the instant the pointer outruns the pill, which for a 40px-tall target
    // is immediately. Listening on the document needs neither compromise.
    function onMove(event) {
      if (event.pointerId !== pointerId) return;

      const dx = event.clientX - startX;
      const dy = event.clientY - startY;

      if (!moved && Math.hypot(dx, dy) < DRAG_THRESHOLD_PX) return;
      if (!moved) {
        moved = true;
        container.classList.add('decibyl-dragging');
      }

      event.preventDefault();
      place(originLeft + dx, originTop + dy);
    }

    function endDrag(event) {
      if (event.pointerId !== pointerId) return;

      document.removeEventListener('pointermove', onMove);
      document.removeEventListener('pointerup', endDrag);
      document.removeEventListener('pointercancel', endDrag);

      pointerId = null;
      container.classList.remove('decibyl-dragging');

      if (!moved) return;

      const rect = container.getBoundingClientRect();
      writeStoredPosition(rect.left, rect.top);

      // Swallow the click this pointer sequence is about to produce, so
      // dragging the pill across the screen does not also place a call.
      container.addEventListener('click', (clickEvent) => {
        clickEvent.stopPropagation();
        clickEvent.preventDefault();
      }, { capture: true, once: true });
    }

    container.addEventListener('pointerdown', (event) => {
      // Left button or touch only; a right-click should open the menu.
      if (event.button !== 0 || pointerId !== null) return;

      pointerId = event.pointerId;
      moved = false;
      startX = event.clientX;
      startY = event.clientY;

      const rect = container.getBoundingClientRect();
      originLeft = rect.left;
      originTop = rect.top;

      document.addEventListener('pointermove', onMove, { passive: false });
      document.addEventListener('pointerup', endDrag);
      document.addEventListener('pointercancel', endDrag);
    });

    // A rotation or a resize can leave the pill off-screen, or stranded over
    // the fold of a keyboard that just opened. Re-clamp rather than reset:
    // moving it back to the corner would undo a choice the visitor made.
    window.addEventListener('resize', () => {
      if (!container.classList.contains('decibyl-placed')) return;
      const rect = container.getBoundingClientRect();
      const safe = place(rect.left, rect.top);
      writeStoredPosition(safe.left, safe.top);
    });
  }

  /**
   * Render the floating CTA pill. Re-renders preserve the hidden audio
   * element so an in-progress call is not interrupted on status changes.
   */
  function renderFloating() {
    const container = document.getElementById('decibyl-widget-root');
    if (!container) return;

    Array.from(container.children).forEach((child) => {
      if (child !== state.audioElement) container.removeChild(child);
    });

    const status = state.connectionStatus || 'idle';

    const button = document.createElement('button');
    button.id = 'decibyl-widget-cta';
    button.type = 'button';
    button.className = `decibyl-widget-cta decibyl-state-${status}`;
    // Idle uses configured color; status states use CSS-defined colors.
    if (status === 'idle') {
      button.style.backgroundColor = state.config.buttonColor;
    }
    button.innerHTML = `
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3z"/>
        <path d="M19 10v2a7 7 0 0 1-14 0v-2"/>
        <line x1="12" y1="19" x2="12" y2="23"/>
        <line x1="8" y1="23" x2="16" y2="23"/>
      </svg>
      <span></span>
    `;
    button.querySelector('span').textContent = ctaLabelForStatus(status);
    button.onclick = toggleCall;

    container.appendChild(button);

    // A voice-only widget is a widget most visitors close. Someone in an
    // office, on a train, or who simply does not want to talk out loud is most
    // of the traffic a website sees. Off by default so no existing embed
    // changes behaviour on its own.
    if (state.config.enableText) {
      container.appendChild(buildTextToggle());
      if (state.textOpen) container.appendChild(buildTextPanel());
    }
  }

  function buildTextToggle() {
    const chatButton = document.createElement('button');
    chatButton.type = 'button';
    chatButton.className = 'decibyl-widget-cta decibyl-text-toggle';
    chatButton.setAttribute('aria-expanded', state.textOpen ? 'true' : 'false');
    chatButton.innerHTML = `
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z"/>
      </svg>
      <span></span>
    `;
    chatButton.querySelector('span').textContent = state.textOpen ? 'Close chat' : 'Type instead';
    chatButton.onclick = function () {
      state.textOpen = !state.textOpen;
      renderFloating();
      if (state.textOpen) {
        const input = document.getElementById('decibyl-text-input');
        if (input) input.focus();
      }
    };
    return chatButton;
  }

  function buildTextPanel() {
    const panel = document.createElement('div');
    panel.className = 'decibyl-text-panel';
    panel.innerHTML = `
      <div class="decibyl-text-messages" id="decibyl-text-messages"></div>
      <form class="decibyl-text-form" id="decibyl-text-form">
        <input
          id="decibyl-text-input"
          class="decibyl-text-input"
          type="text"
          maxlength="2000"
          autocomplete="off"
          placeholder="Type your message"
          aria-label="Type your message"
        />
        <button type="submit" class="decibyl-text-send" aria-label="Send">Send</button>
      </form>
    `;
    panel.querySelector('#decibyl-text-form').addEventListener('submit', function (event) {
      event.preventDefault();
      const input = document.getElementById('decibyl-text-input');
      const text = input.value;
      // Cleared before the request rather than after: a visitor who types the
      // next sentence while the first is in flight should not have it wiped
      // when the response lands.
      input.value = '';
      sendTextMessage(text);
    });
    // Appended before the messages are rendered into it, so the scroll
    // position below is measured against a panel that is already in the DOM.
    setTimeout(function () { renderTextMessages(); }, 0);
    return panel;
  }

  /**
   * Paint the transcript.
   *
   * `pendingUserText` shows the visitor's own message immediately, before the
   * server has confirmed it. Waiting for the round trip makes the widget feel
   * broken on a slow connection — the message they just typed vanishes.
   */
  function renderTextMessages(options) {
    const list = document.getElementById('decibyl-text-messages');
    if (!list) return;

    const messages = state.textMessages.slice();
    if (options && options.pendingUserText) {
      messages.push({ role: 'user', content: options.pendingUserText });
    }

    list.innerHTML = '';
    messages.forEach(function (message) {
      const row = document.createElement('div');
      row.className = 'decibyl-text-msg decibyl-text-msg-' + message.role;
      // textContent, never innerHTML: this is server-relayed content and the
      // widget runs on a customer's own page.
      row.textContent = message.content;
      list.appendChild(row);
    });

    if (state.textSending) {
      const typing = document.createElement('div');
      typing.className = 'decibyl-text-msg decibyl-text-msg-assistant decibyl-text-typing';
      typing.textContent = '…';
      list.appendChild(typing);
    }

    list.scrollTop = list.scrollHeight;
  }

  /**
   * Create headless widget (no UI — host page drives everything via window.DecibylWidget API)
   */
  function createHeadlessWidget() {
    const audio = document.createElement('audio');
    audio.id = 'decibyl-widget-audio';
    audio.autoplay = true;
    audio.style.display = 'none';
    document.body.appendChild(audio);
    state.audioElement = audio;
  }

  /**
   * Toggle call (start or stop based on current state)
   */
  function toggleCall() {
    if (state.connectionStatus === 'idle' || state.connectionStatus === 'failed') {
      startCall();
    } else {
      stopCall();
    }
  }

  function updateFloatingButton(status) {
    state.connectionStatus = status;
    renderFloating();
  }

  /**
   * Create inline widget UI
   */
  function createInlineWidget() {
    // Find container element
    const container = document.getElementById(state.config.containerId);
    if (!container) {
      console.error(`Decibyl Widget: Container element with id "${state.config.containerId}" not found`);
      if (state.callbacks.onError) {
        state.callbacks.onError(new Error('Container element not found'));
      }
      return;
    }

    // Clear container
    container.innerHTML = '';
    container.className = 'decibyl-inline-container';

    // Add minimal inline styles
    const inlineStyles = `
      .decibyl-inline-container {
        min-height: 200px;
        padding: 20px;
        display: flex;
        align-items: center;
        justify-content: center;
      }

      .decibyl-inline-status {
        text-align: center;
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      }

      .decibyl-inline-status-icon {
        width: 64px;
        height: 64px;
        margin: 0 auto 20px;
      }

      .decibyl-inline-status-text {
        font-size: 18px;
        font-weight: 500;
        margin: 0 0 8px;
        color: #111827;
      }

      .decibyl-inline-status-subtext {
        font-size: 14px;
        color: #6b7280;
        margin: 0 0 20px;
      }

      .decibyl-inline-button-container {
        display: flex;
        gap: 12px;
        justify-content: center;
        margin-top: 20px;
      }

      .decibyl-inline-btn {
        padding: 12px 32px;
        border-radius: 8px;
        border: none;
        font-size: 16px;
        font-weight: 600;
        cursor: pointer;
        transition: all 0.3s;
        color: white;
        box-shadow: 0 2px 8px rgba(0, 0, 0, 0.15);
      }

      .decibyl-inline-btn:hover {
        transform: translateY(-2px);
        box-shadow: 0 4px 12px rgba(0, 0, 0, 0.2);
      }

      .decibyl-inline-btn:active {
        transform: translateY(0);
      }

      .decibyl-inline-btn-start {
        background: #10b981;
      }

      .decibyl-inline-btn-start:hover {
        background: #059669;
      }

      .decibyl-inline-btn-end {
        background: #ef4444;
      }

      .decibyl-inline-btn-end:hover {
        background: #dc2626;
      }

      @keyframes pulse {
        0%, 100% { opacity: 1; }
        50% { opacity: 0.5; }
      }

      .decibyl-inline-pulse {
        animation: pulse 2s infinite;
      }
    `;

    // Add inline styles if not already added
    if (!document.getElementById('decibyl-inline-styles')) {
      const styleSheet = document.createElement('style');
      styleSheet.id = 'decibyl-inline-styles';
      styleSheet.textContent = inlineStyles;
      document.head.appendChild(styleSheet);
    }

    // Create initial status display
    updateInlineStatus('idle');

    // Store audio element (hidden)
    state.audioElement = document.createElement('audio');
    state.audioElement.autoplay = true;
    state.audioElement.style.display = 'none';
    container.appendChild(state.audioElement);

    // Mark widget as open (for inline mode, it's always "open")
    state.isOpen = true;
  }

  /**
   * Update inline widget status
   */
  function updateInlineStatus(status, text, subtext) {
    const container = document.getElementById(state.config.containerId);
    if (!container) return;

    // Update state
    state.connectionStatus = status;

    // Determine display text
    const displayText = text || {
      idle: 'Ready to Connect',
      connecting: 'Connecting...',
      connected: 'Call Active',
      failed: 'Connection Failed'
    }[status];

    const displaySubtext = subtext || {
      idle: state.config.callToActionText,
      connecting: 'Please wait while we establish connection',
      connected: 'You can speak now',
      failed: 'Please check your microphone and try again'
    }[status];

    // Simple button design: green to start, red to end
    let buttonHTML = '';
    if (status === 'idle' || status === 'failed') {
      // Button to start with configured color
      buttonHTML = `
        <button class="decibyl-inline-btn decibyl-inline-btn-start" id="decibyl-inline-start-btn" style="background: ${state.config.buttonColor};">
          ${status === 'failed' ? 'Retry' : state.config.buttonText}
        </button>
      `;
    } else if (status === 'connecting' || status === 'connected') {
      // Red button to end
      buttonHTML = `
        <button class="decibyl-inline-btn decibyl-inline-btn-end" id="decibyl-inline-end-btn">
          End Call
        </button>
      `;
    }

    // Update container content (preserve audio element)
    const audioElement = state.audioElement;
    container.innerHTML = `
      <div class="decibyl-inline-status">
        <div class="decibyl-inline-status-icon ${status === 'connecting' ? 'decibyl-inline-pulse' : ''}">
          ${getStatusIcon(status)}
        </div>
        <p class="decibyl-inline-status-text">${displayText}</p>
        <p class="decibyl-inline-status-subtext">${displaySubtext}</p>
        <div class="decibyl-inline-button-container">
          ${buttonHTML}
        </div>
      </div>
    `;

    // Re-append audio element
    if (audioElement) {
      container.appendChild(audioElement);
    }

    // Attach event handlers
    const startBtn = document.getElementById('decibyl-inline-start-btn');
    if (startBtn) startBtn.onclick = startCall;

    const endBtn = document.getElementById('decibyl-inline-end-btn');
    if (endBtn) endBtn.onclick = stopCall;

    // Trigger status change callback
    if (state.callbacks.onStatusChange) {
      state.callbacks.onStatusChange(status, displayText, displaySubtext);
    }
  }

  /**
   * Get status icon SVG
   */
  function getStatusIcon(status) {
    const icons = {
      idle: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3z"/>
        <path d="M19 10v2a7 7 0 0 1-14 0v-2"/>
        <line x1="12" y1="19" x2="12" y2="23"/>
        <line x1="8" y1="23" x2="16" y2="23"/>
      </svg>`,
      connecting: `<svg class="decibyl-widget-spinner" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <path d="M12 2v4"/>
        <path d="M12 18v4"/>
        <path d="M4.93 4.93l2.83 2.83"/>
        <path d="M16.24 16.24l2.83 2.83"/>
        <path d="M2 12h4"/>
        <path d="M18 12h4"/>
        <path d="M4.93 19.07l2.83-2.83"/>
        <path d="M16.24 7.76l2.83-2.83"/>
      </svg>`,
      connected: `<svg viewBox="0 0 24 24" fill="none" stroke="#10b981" stroke-width="2">
        <path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72"/>
        <path d="M15 7a2 2 0 0 1 2 2"/>
        <path d="M15 3a6 6 0 0 1 6 6"/>
      </svg>`,
      failed: `<svg viewBox="0 0 24 24" fill="none" stroke="#ef4444" stroke-width="2">
        <circle cx="12" cy="12" r="10"/>
        <line x1="12" y1="8" x2="12" y2="12"/>
        <line x1="12" y1="16" x2="12.01" y2="16"/>
      </svg>`
    };
    return icons[status] || icons.idle;
  }

  /**
   * Update widget status
   */
  function updateStatus(status, text, subtext) {
    state.connectionStatus = status;

    // Use appropriate update function based on mode
    if (state.config.embedMode === 'inline') {
      updateInlineStatus(status, text, subtext);
    } else if (state.config.embedMode === 'headless') {
      if (state.callbacks.onStatusChange) {
        state.callbacks.onStatusChange(status, text, subtext);
      }
    } else {
      updateFloatingButton(status);
    }
  }

  /**
   * Open widget (deprecated - kept for backwards compatibility)
   */
  function openWidget() {
    // No-op since we removed the modal
  }

  /**
   * Close widget (deprecated - kept for backwards compatibility)
   */
  function closeWidget() {
    // Stop call if active
    if (state.connectionStatus === 'connected' || state.connectionStatus === 'connecting') {
      stopCall();
    }
  }

  /**
   * Start voice call
   */
  async function startCall() {
    state.gracefulDisconnect = false;
    updateStatus('connecting', 'Connecting...', 'Please wait while we establish the connection');

    if (state.callbacks.onCallStart) {
      state.callbacks.onCallStart();
    }

    try {
      // Initialize session if using embed token
      if (state.config.token) {
        await initializeEmbedSession();
      } else {
        // Direct mode with workflow and run IDs
        state.sessionToken = 'direct-mode';
        state.workflowRunId = state.config.runId;
      }

      // Request microphone permission
      try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        // Release any stream still held from a prior attempt before retaining
        // the new one, so a re-entrant start can't leak the microphone.
        if (state.stream) {
          state.stream.getTracks().forEach(track => track.stop());
        }
        state.stream = stream;
      } catch (micError) {
        // Handle specific microphone permission errors
        let errorMessage = 'Please check your microphone and try again';

        if (micError.name === 'NotAllowedError' || micError.name === 'PermissionDeniedError') {
          errorMessage = 'Microphone permission denied. Please allow microphone access to start the call.';
        } else if (micError.name === 'NotFoundError' || micError.name === 'DevicesNotFoundError') {
          errorMessage = 'No microphone found. Please connect a microphone and try again.';
        } else if (micError.name === 'NotReadableError' || micError.name === 'TrackStartError') {
          errorMessage = 'Microphone is already in use by another application.';
        }

        throw new Error(errorMessage);
      }

      // Create WebRTC connection
      await createWebRTCConnection();

      // Connect WebSocket
      await connectWebSocket();

      // Start negotiation
      await negotiate();

    } catch (error) {
      console.error('Decibyl Widget: Failed to start call', error);

      // Release anything acquired before the failure so a retry starts clean.
      // getUserMedia may have succeeded before a later step (WebSocket /
      // negotiation) threw, which would otherwise leave the mic held and block
      // the next getUserMedia(). Null the refs before close() so the peer/ws
      // state handlers short-circuit instead of re-entering teardown.
      if (state.stream) {
        state.stream.getTracks().forEach(track => track.stop());
        state.stream = null;
      }
      if (state.pc) {
        const pc = state.pc;
        state.pc = null;
        if (pc.signalingState !== 'closed') {
          pc.close();
        }
      }
      if (state.ws) {
        const ws = state.ws;
        state.ws = null;
        if (ws.readyState !== WebSocket.CLOSED && ws.readyState !== WebSocket.CLOSING) {
          ws.close();
        }
      }

      updateStatus('failed', 'Connection failed', error.message || 'Please check your microphone and try again');

      // Trigger error callback
      if (state.callbacks.onError) {
        state.callbacks.onError(error);
      }
    }
  }

  /**
   * Initialize embed session
   */
  async function initializeEmbedSession() {
    const response = await fetch(`${state.config.apiBaseUrl}/api/v1/public/embed/init`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Origin': window.location.origin
      },
      body: JSON.stringify({
        token: state.config.token,
        context_variables: state.config.contextVariables
      })
    });

    if (!response.ok) {
      const error = await response.json();
      throw new Error(error.detail || 'Failed to initialize session');
    }

    const data = await response.json();
    state.sessionToken = data.session_token;
    state.workflowRunId = data.workflow_run_id;
    state.workflowId = data.config.workflow_id;

    // Fetch TURN credentials after session initialization
    await fetchTurnCredentials();
  }

  /**
   * Start a typed session.
   *
   * Deliberately not a branch inside initializeEmbedSession: that one also
   * fetches TURN credentials and stands up a peer connection, and a typed
   * conversation needs neither. Sharing it would mean a visitor who only wants
   * to type is asked for nothing but still pays for the WebRTC setup.
   */
  async function initializeTextSession() {
    const response = await fetch(`${state.config.apiBaseUrl}/api/v1/public/embed/init`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        token: state.config.token,
        context_variables: state.config.contextVariables,
        mode: 'text'
      })
    });

    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      throw new Error(error.detail || 'Could not start the chat');
    }

    const data = await response.json();
    state.textSessionToken = data.session_token;
    state.workflowRunId = data.workflow_run_id;
  }

  /**
   * Send one typed message and render whatever comes back.
   *
   * The server returns the whole transcript rather than a delta, so this
   * replaces the list instead of appending to it — a reload or a dropped
   * response cannot leave the panel showing a conversation the server does not
   * agree with.
   */
  async function sendTextMessage(text) {
    if (!text.trim() || state.textSending) return;
    state.textSending = true;
    renderTextMessages({ pendingUserText: text });

    try {
      if (!state.textSessionToken) {
        await initializeTextSession();
      }

      const response = await fetch(
        `${state.config.apiBaseUrl}/api/v1/public/embed/text/${state.textSessionToken}/messages`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ text: text })
        }
      );

      if (!response.ok) {
        throw new Error('send failed');
      }

      const data = await response.json();
      state.textMessages = data.messages || [];
      state.textCompleted = Boolean(data.is_completed);
    } catch (error) {
      // Shown in the thread rather than as a toast: the visitor's own message
      // is already on screen, and a failure that appears somewhere else looks
      // like the agent ignored them. The cause still goes to the console the
      // way every other failure in this file does -- the visitor gets one
      // sentence, whoever is debugging the customer's page gets the reason.
      console.error('Decibyl Widget: Failed to send text message', error);
      state.textMessages = state.textMessages.concat([{
        role: 'assistant',
        content: 'Sorry — that did not send. Please try again.'
      }]);
    } finally {
      state.textSending = false;
      renderTextMessages();
    }
  }

  /**
   * Fetch TURN credentials for WebRTC connection
   */
  async function fetchTurnCredentials() {
    if (!state.sessionToken) {
      console.warn('Decibyl Widget: No session token available for TURN credentials');
      return;
    }

    try {
      const response = await fetch(`${state.config.apiBaseUrl}/api/v1/public/embed/turn-credentials/${state.sessionToken}`, {
        method: 'GET',
        headers: {
          'Content-Type': 'application/json',
          'Origin': window.location.origin
        }
      });

      if (response.ok) {
        state.turnCredentials = await response.json();
        console.log(`TURN credentials obtained, TTL: ${state.turnCredentials.ttl}s`);
      } else if (response.status === 503) {
        // TURN not configured on server - this is OK, we'll use STUN only
        console.log('TURN server not configured, using STUN only');
      } else {
        console.warn(`Failed to fetch TURN credentials: ${response.status}`);
      }
    } catch (error) {
      console.warn('Failed to fetch TURN credentials, continuing without TURN:', error);
    }
  }

  /**
   * Create WebRTC peer connection
   */
  function createWebRTCConnection() {
    // Build ICE servers list
    const iceServers = [{ urls: ['stun:stun.l.google.com:19302'] }];

    // Add TURN server if credentials are available
    if (state.turnCredentials && state.turnCredentials.uris && state.turnCredentials.uris.length > 0) {
      iceServers.push({
        urls: state.turnCredentials.uris,
        username: state.turnCredentials.username,
        credential: state.turnCredentials.password
      });
      console.log(`TURN server configured with ${state.turnCredentials.uris.length} URIs`);
    }

    const config = {
      iceServers: iceServers
    };

    state.pc = new RTCPeerConnection(config);

    // Add audio track
    if (state.stream) {
      state.stream.getTracks().forEach(track => {
        state.pc.addTrack(track, state.stream);
      });
    }

    // Handle incoming audio
    state.pc.ontrack = (event) => {
      if (event.track.kind === 'audio' && state.audioElement) {
        state.audioElement.srcObject = event.streams[0];
      }
    };

    // Monitor connection state
    state.pc.oniceconnectionstatechange = handlePeerConnectionStateChange;
    state.pc.onconnectionstatechange = handlePeerConnectionStateChange;
    state.pc.onicecandidate = sendIceCandidate;
  }

  function handlePeerConnectionStateChange() {
    const pc = state.pc;
    if (!pc) return;

    console.log('Peer connection state:', pc.connectionState, 'ICE:', pc.iceConnectionState);

    if (pc.connectionState === 'connected' || pc.iceConnectionState === 'connected' || pc.iceConnectionState === 'completed') {
      const wasAlreadyConnected = state.callStartedAt !== null;
      updateStatus('connected', 'Connected', 'Your voice call is now active');
      if (!wasAlreadyConnected) {
        state.callStartedAt = Date.now();
        if (state.callbacks.onCallConnected) {
          state.callbacks.onCallConnected({
            agentId: state.config.workflowId || null,
            token: state.config.token || null,
            workflowRunId: state.workflowRunId || null
          });
        }
      }
      return;
    }

    if (pc.connectionState === 'failed' || pc.iceConnectionState === 'failed') {
      stopCall({
        graceful: false,
        status: 'failed',
        text: 'Connection lost',
        subtext: 'The call has been disconnected'
      });
      return;
    }

    if (
      pc.connectionState === 'closed' ||
      pc.connectionState === 'disconnected' ||
      pc.iceConnectionState === 'closed' ||
      pc.iceConnectionState === 'disconnected'
    ) {
      stopCall({ graceful: true });
    }
  }

  function sendIceCandidate(event) {
    // Handle ICE candidates for trickling
    if (state.ws && state.ws.readyState === WebSocket.OPEN) {
      const message = {
        type: 'ice-candidate',
        payload: {
          candidate: event.candidate ? {
            candidate: event.candidate.candidate,
            sdpMid: event.candidate.sdpMid,
            sdpMLineIndex: event.candidate.sdpMLineIndex
          } : null,
          pc_id: state.pcId
        }
      };
      state.ws.send(JSON.stringify(message));
    }
  }

  /**
   * Connect WebSocket for signaling
   */
  async function connectWebSocket() {
    return new Promise((resolve, reject) => {
      // Use public signaling endpoint for embed tokens
      const wsUrl = `${state.config.apiBaseUrl.replace('http', 'ws')}/api/v1/ws/public/signaling/${state.sessionToken}`;

      state.ws = new WebSocket(wsUrl);
      state.pcId = generatePeerId();

      state.ws.onopen = () => {
        console.log('WebSocket connected');
        resolve();
      };

      state.ws.onerror = (error) => {
        console.error('WebSocket error:', error);
        reject(error);
      };

      state.ws.onclose = (event) => {
        console.log('WebSocket closed');
        state.ws = null;

        if (event.reason === 'call ended') {
          stopCall({ graceful: true, closeWebSocket: false });
          return;
        }

        if (state.connectionStatus === 'connected' && !state.gracefulDisconnect) {
          updateStatus('failed', 'Connection lost', 'The call has been disconnected');
        }
      };

      state.ws.onmessage = async (event) => {
        try {
          const message = JSON.parse(event.data);
          await handleWebSocketMessage(message);
        } catch (e) {
          console.error('Failed to handle WebSocket message:', e);
        }
      };
    });
  }

  /**
   * Handle WebSocket messages
   */
  async function handleWebSocketMessage(message) {
    switch (message.type) {
      case 'answer':
        const answer = message.payload;
        console.log('Received answer from server');

        await state.pc.setRemoteDescription({
          type: 'answer',
          sdp: answer.sdp
        });
        break;

      case 'ice-candidate':
        const candidate = message.payload.candidate;
        if (candidate) {
          try {
            await state.pc.addIceCandidate({
              candidate: candidate.candidate,
              sdpMid: candidate.sdpMid,
              sdpMLineIndex: candidate.sdpMLineIndex
            });
            console.log('Added remote ICE candidate');
          } catch (e) {
            console.error('Failed to add ICE candidate:', e);
          }
        }
        break;

      case 'error':
        console.error('Server error:', message.payload);
        updateStatus('failed', 'Server error', message.payload.message || 'An error occurred');
        break;

      case 'call-ended':
        console.log('Call ended by server:', message.payload);
        stopCall({ graceful: true });
        break;

      default:
        console.warn('Unknown message type:', message.type);
    }
  }

  /**
   * Negotiate WebRTC connection
   */
  async function negotiate() {
    const offer = await state.pc.createOffer();
    await state.pc.setLocalDescription(offer);

    const message = {
      type: 'offer',
      payload: {
        sdp: offer.sdp,
        type: 'offer',
        pc_id: state.pcId,
        workflow_id: parseInt(state.config.workflowId),
        workflow_run_id: parseInt(state.workflowRunId),
        call_context_vars: state.config.contextVariables || {}
      }
    };

    state.ws.send(JSON.stringify(message));
    console.log('Sent offer via WebSocket');
  }

  /**
   * Stop voice call
   */
  function stopCall(options = {}) {
    const graceful = options.graceful !== false;
    const closeWebSocket = options.closeWebSocket !== false;
    const status = options.status || 'idle';
    const text = options.text || 'Call ended';
    const subtext = options.subtext || 'Click below to start a new call';

    state.gracefulDisconnect = graceful;

    // Fire onCallDisconnected only if the call had actually connected, with
    // identifiers and duration. Must run before we clear callStartedAt.
    if (state.callStartedAt && state.callbacks.onCallDisconnected) {
      const durationSeconds = Math.round((Date.now() - state.callStartedAt) / 1000);
      state.callbacks.onCallDisconnected({
        agentId: state.config.workflowId || null,
        token: state.config.token || null,
        workflowRunId: state.workflowRunId || null,
        durationSeconds
      });
    }
    state.callStartedAt = null;

    updateStatus(status, text, subtext);

    if (state.callbacks.onCallEnd) {
      state.callbacks.onCallEnd();
    }

    // Close WebSocket
    if (closeWebSocket && state.ws) {
      const ws = state.ws;
      state.ws = null;
      if (ws.readyState !== WebSocket.CLOSED && ws.readyState !== WebSocket.CLOSING) {
        ws.close();
      }
    } else if (!closeWebSocket) {
      state.ws = null;
    }

    // Stop media tracks
    if (state.stream) {
      state.stream.getTracks().forEach(track => track.stop());
      state.stream = null;
    }

    // Close peer connection
    if (state.pc) {
      const pc = state.pc;
      state.pc = null;
      if (pc.signalingState !== 'closed') {
        pc.close();
      }
    }

    // Clear audio
    if (state.audioElement) {
      state.audioElement.srcObject = null;
    }
  }

  /**
   * Retry connection
   */
  function retryCall() {
    updateStatus('idle', 'Ready to start', 'Click below to begin your voice call');
    setTimeout(() => startCall(), 500);
  }

  /**
   * Generate unique peer ID
   */
  function generatePeerId() {
    const array = new Uint8Array(16);
    crypto.getRandomValues(array);
    return 'PC-' + Array.from(array)
      .map(b => b.toString(16).padStart(2, '0'))
      .join('');
  }

  // Public API
  window.DecibylWidget = {
    // Core methods
    init: init,
    start: startCall,
    stop: stopCall,
    end: stopCall, // Alias for stop
    retry: retryCall,

    // Floating widget specific
    open: openWidget,
    close: closeWidget,

    // State and callbacks
    getState: () => state,
    onReady: (callback) => { state.callbacks.onReady = callback; },
    onCallStart: (callback) => { state.callbacks.onCallStart = callback; },
    onCallConnected: (callback) => { state.callbacks.onCallConnected = callback; },
    onCallDisconnected: (callback) => { state.callbacks.onCallDisconnected = callback; },
    onCallEnd: (callback) => { state.callbacks.onCallEnd = callback; },
    onError: (callback) => { state.callbacks.onError = callback; },
    onStatusChange: (callback) => { state.callbacks.onStatusChange = callback; },

    // Check if inline mode
    isInlineMode: () => state.config.embedMode === 'inline',

    // Re-render the inline widget (useful when React component remounts)
    refresh: () => {
      if (state.config.embedMode === 'inline') {
        // Re-render inline widget with current status
        updateInlineStatus(state.connectionStatus);
      }
    },

    // Initialize inline mode manually (for advanced use cases)
    initInline: (options) => {
      if (options.container) {
        state.config.containerId = options.container.id || 'decibyl-inline-container';
      }
      state.config.embedMode = 'inline';

      // Set callbacks if provided
      if (options.onReady) state.callbacks.onReady = options.onReady;
      if (options.onCallStart) state.callbacks.onCallStart = options.onCallStart;
      if (options.onCallConnected) state.callbacks.onCallConnected = options.onCallConnected;
      if (options.onCallDisconnected) state.callbacks.onCallDisconnected = options.onCallDisconnected;
      if (options.onCallEnd) state.callbacks.onCallEnd = options.onCallEnd;
      if (options.onError) state.callbacks.onError = options.onError;
      if (options.onStatusChange) state.callbacks.onStatusChange = options.onStatusChange;

      // Initialize
      if (!state.isInitialized) {
        init();
      }
    }
  };

  // Auto-initialize on DOM ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

})();
