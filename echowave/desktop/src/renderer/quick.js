// Quick ask: Enter asks in the Decibyl window; with "Work on my computer"
// ticked it starts the computer-use task instead (main checks everything).
const api = window.decibylDesktop;
const text = document.getElementById('text');
const computer = document.getElementById('computer');
const message = document.getElementById('message');

async function refresh() {
    try {
        const s = await api.settings.get();
        document.getElementById('computer-row').hidden = s.computerUse.allowedApps.length === 0;
    } catch {
        // Settings unreadable: the box still asks.
    }
}

document.getElementById('ask').addEventListener('submit', async (event) => {
    event.preventDefault();
    message.textContent = '';
    message.className = 'muted';
    try {
        const result = await api.quick.submit(text.value, computer.checked ? 'computer' : 'ask');
        if (result && result.started === false) {
            message.textContent = result.reason;
            message.className = 'error';
            return;
        }
        text.value = '';
        computer.checked = false;
        await api.quick.close();
    } catch (err) {
        message.textContent = err.message;
        message.className = 'error';
    }
});
text.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && !event.shiftKey) {
        event.preventDefault();
        document.getElementById('ask').requestSubmit();
    }
    if (event.key === 'Escape') api.quick.close();
});
api.quick.onPrefill((value) => {
    text.value = String(value || '').trim();
    text.focus();
});
window.addEventListener('focus', () => {
    text.focus();
    refresh();
});
refresh();
