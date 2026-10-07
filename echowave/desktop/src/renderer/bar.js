// The always-visible bar while Decibyl works on this computer. Stop is
// always enabled; pressing it aborts the model call and every action after.
const api = window.decibylDesktop;
const line = document.getElementById('line');
const stop = document.getElementById('stop');

stop.addEventListener('click', async () => {
    stop.disabled = true;
    stop.textContent = 'Stopping…';
    try {
        await api.bar.stop();
    } catch {
        stop.disabled = false;
        stop.textContent = 'Stop';
    }
});

const fmt = (e) => {
    switch (e.type) {
        case 'started':
            return e.task;
        case 'step':
            return `${e.app}: ${e.detail}`;
        case 'blocked':
            return `Not allowed: ${e.reason}`;
        case 'awaiting_approval':
            return `Waiting for you in Decibyl: ${e.summary}`;
        case 'approval_settled':
            return e.outcome === 'claimed'
                ? 'Approved. Doing it once.'
                : e.outcome === 'declined'
                  ? 'You said no.'
                  : '';
        case 'progress':
            return null;
        case 'finished':
            return 'Finished.';
        default:
            return null;
    }
};
api.bar.onUpdate((event) => {
    const text = fmt(event);
    if (text) line.textContent = text;
});
