// The person's settings for this computer. Every change goes to main,
// which sanitizes it (src/main/config.ts) and sends back what it kept.
const api = window.decibylDesktop;
const $ = (id) => document.getElementById(id);
let current = null;

function say(text) {
    $('saved').textContent = text;
    setTimeout(() => ($('saved').textContent = ''), 2000);
}

function render(s) {
    current = s;
    $('environment').value = s.environment;
    $('custom-row').hidden = s.environment !== 'custom';
    $('customUrl').value = s.customUrl || '';
    $('startAtLogin').checked = s.startAtLogin;
    $('shortcut').value = s.shortcut;
    $('updateChannel').value = s.updateChannel;
    $('folder').textContent = s.watchedFolder || 'No folder';
    $('clear-folder').disabled = !s.watchedFolder;
    $('maxSteps').value = s.computerUse.limits.maxSteps;
    $('maxMinutes').value = Math.round(s.computerUse.limits.maxSeconds / 60);
    $('maxCostUsd').value = s.computerUse.limits.maxCostUsd;
    const list = $('apps');
    list.replaceChildren();
    if (s.computerUse.allowedApps.length === 0) {
        const li = document.createElement('li');
        li.textContent = 'No apps yet. Decibyl cannot work on this computer until you add one.';
        li.className = 'muted';
        list.append(li);
    }
    for (const name of s.computerUse.allowedApps) {
        const li = document.createElement('li');
        li.textContent = name;
        const remove = document.createElement('button');
        remove.textContent = 'Remove';
        remove.setAttribute('aria-label', `Remove ${name}`);
        remove.addEventListener('click', () =>
            update({
                computerUse: { allowedApps: s.computerUse.allowedApps.filter((a) => a !== name) },
            }),
        );
        li.append(remove);
        list.append(li);
    }
}

async function update(change) {
    try {
        render(await api.settings.update(change));
        say('Saved');
    } catch (err) {
        say(err.message);
    }
}

async function loadRunning() {
    const select = $('running');
    select.replaceChildren();
    const names = await api.apps.running().catch(() => []);
    for (const name of names) {
        const option = document.createElement('option');
        option.value = option.textContent = name;
        select.append(option);
    }
    $('add-app').disabled = names.length === 0;
    if (names.length === 0)
        $('apps-error').textContent = 'No open apps found. Open the app you want, then press ↻.';
    else $('apps-error').textContent = '';
}

async function keyState() {
    const present = await api.apiKey.present().catch(() => false);
    $('key-state').textContent = present ? 'A key is saved.' : 'No key saved.';
    $('clear-key').disabled = !present;
}

$('environment').addEventListener('change', (e) => update({ environment: e.target.value }));
$('customUrl').addEventListener('change', (e) => update({ customUrl: e.target.value || null }));
$('startAtLogin').addEventListener('change', (e) => update({ startAtLogin: e.target.checked }));
$('shortcut').addEventListener('change', (e) => update({ shortcut: e.target.value }));
$('updateChannel').addEventListener('change', (e) => update({ updateChannel: e.target.value }));
for (const id of ['maxSteps', 'maxMinutes', 'maxCostUsd']) {
    $(id).addEventListener('change', () =>
        update({
            computerUse: {
                limits: {
                    maxSteps: Number($('maxSteps').value),
                    maxSeconds: Number($('maxMinutes').value) * 60,
                    maxCostUsd: Number($('maxCostUsd').value),
                },
            },
        }),
    );
}
$('add-app').addEventListener('click', () => {
    const name = $('running').value;
    if (!name || !current) return;
    update({ computerUse: { allowedApps: [...current.computerUse.allowedApps, name] } });
});
$('refresh-apps').addEventListener('click', loadRunning);
$('choose-folder').addEventListener('click', async () => {
    $('folder-error').textContent = '';
    try {
        await api.watchedFolder.choose();
        render(await api.settings.get());
    } catch (err) {
        $('folder-error').textContent = err.message;
    }
});
$('clear-folder').addEventListener('click', async () => {
    await api.watchedFolder.clear();
    render(await api.settings.get());
});
$('save-key').addEventListener('click', async () => {
    $('key-error').textContent = '';
    try {
        await api.apiKey.set($('key').value);
        $('key').value = '';
        keyState();
    } catch (err) {
        $('key-error').textContent = err.message;
    }
});
$('clear-key').addEventListener('click', async () => {
    await api.apiKey.clear();
    keyState();
});
api.settings.onChanged(render);

api.settings.get().then(render);
loadRunning();
keyState();
